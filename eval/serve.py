"""Dedicated evaluation host. Context and telemetry exist only on this host."""
from __future__ import annotations

import argparse
import os
from datetime import date
from pathlib import Path

from fastapi import HTTPException, Request
from sqlalchemy import select

from eval.common import digest
from eval.runtime_identity import verify_shared_lock_evidence
from eval.snapshot import current_snapshot
from eval.validate_gold import require


def observe_shared_lock(settings, expected):
    from eval.runtime_identity import process_identity
    require(os.name != "nt", "SHARED_LINUX_MODEL_GATE_REQUIRED")
    require(isinstance(expected, dict), "SHARED_LOCK_EVIDENCE_REQUIRED")
    path = settings.model_lock_path.resolve()
    require(str(path) == expected.get("expected_path") and path.is_file(), "SHARED_LOCK_PATH_MISMATCH")
    observed = {"expected_path": str(path)}
    for role in ("api", "worker"):
        declared = expected.get(role, {})
        pid = os.getpid() if role == "api" else declared.get("process", {}).get("pid")
        require(type(pid) is int and pid > 0, "SHARED_LOCK_PROCESS_REQUIRED")
        # Read only this process-start setting; never expose other environment values.
        values = (Path("/proc") / str(pid) / "environ").read_bytes().split(b"\0")
        configured = [value.split(b"=", 1)[1].decode() for value in values if value.startswith(b"MODEL_LOCK_PATH=")]
        require(len(configured) == 1 and str(Path(configured[0]).resolve()) == str(path),
                "SHARED_LOCK_PROCESS_CONFIGURATION_MISMATCH")
        stat = path.stat()
        observed[role] = {"path": str(path), "device": stat.st_dev, "inode": stat.st_ino,
                          "process": process_identity(pid)}
    return verify_shared_lock_evidence(expected, observed)


def evaluation_app(database, settings, *, as_of=None, deployment_evidence=None, pi_observer=None):
    require(settings.local_user_id.startswith("eval-"), "EVALUATION_ACTOR_REQUIRED")
    from interview_intelligence.api import create_app
    from interview_intelligence.domain.models import AgentConversation, AgentEvent, AgentTurn, ModelCall, ReviewEvent, ToolInvocation, UserQuestionState
    app = create_app(database, settings)
    runtime_guard = None
    if deployment_evidence is not None:
        from eval.runtime_identity import RuntimeIdentityGuard
        if pi_observer is None:
            def pi_observer():
                import httpx
                require(bool(settings.internal_agent_token), "RUNTIME_PI_TOKEN_REQUIRED")
                with httpx.Client(timeout=60, trust_env=False) as client:
                    response = client.get(settings.pi_agent_url.rstrip("/") + "/runtime-identity",
                        headers={"Authorization": "Bearer " + settings.internal_agent_token})
                    response.raise_for_status()
                    return response.json()
        runtime_guard = RuntimeIdentityGuard(Path(deployment_evidence), pi_observer)
    if as_of is not None:
        # Only the dedicated evaluation host freezes time for reproducible replay.
        app.state.query_service.today = lambda: as_of
        app.state.response_as_of = lambda: as_of

    @app.get("/api/evaluation/context")
    def context(request: Request, request_id: str | None = None):
        if settings.multi_tenant_enabled and app.state.tenant_service.require(request) != settings.local_user_id:
            raise HTTPException(403, "EVALUATION_ACTOR_MISMATCH")
        runtime = runtime_guard.context() if runtime_guard is not None else {}
        lock = observe_shared_lock(settings, runtime["runtime_evidence"]["measurement"].get("lock")) if runtime else None
        with database.session() as session:
            profile = {"source": "global", "model": settings.query_model, "reranker_model": settings.reranker_model,
                       "embedding_model": settings.embedding_model}
            if settings.multi_tenant_enabled:
                from interview_intelligence.tenant_models import TenantAccount
                account = session.get(TenantAccount, settings.local_user_id)
                require(account is not None, "EVALUATION_ACCOUNT_MISSING")
                personal = bool(account.provider_key)
                profile.update(source="personal" if personal else "system", version=account.provider_version,
                    model=account.provider_model if personal else settings.tenant_default_query_model,
                    reranker_model=(account.provider_reranker_model or account.provider_model) if personal else
                                   (settings.tenant_default_reranker_model or settings.tenant_default_query_model),
                    profile_sha256=digest({"encrypted_key": account.provider_key if personal else None,
                        "base_url": account.provider_base_url if personal else settings.tenant_default_base_url,
                        "model": account.provider_model if personal else settings.tenant_default_query_model,
                        "reranker_model": account.provider_reranker_model if personal else settings.tenant_default_reranker_model,
                        "version": account.provider_version}))
            states = list(session.scalars(select(UserQuestionState).where(UserQuestionState.user_id == settings.local_user_id)))
            events = list(session.scalars(select(ReviewEvent).where(ReviewEvent.user_id == settings.local_user_id)))
            # Select metadata only: no conversation state, request or response text.
            conversations = session.execute(select(AgentConversation.id, AgentConversation.version,
                AgentConversation.updated_at).where(AgentConversation.user_id == settings.local_user_id)
                .order_by(AgentConversation.id)).all()
            turns = session.execute(select(AgentTurn.id, AgentTurn.conversation_id, AgentTurn.request_id,
                AgentTurn.payload_hash, AgentTurn.status, AgentTurn.event_sequence, AgentTurn.cancel_requested,
                AgentTurn.created_at, AgentTurn.finished_at).where(AgentTurn.user_id == settings.local_user_id)
                .order_by(AgentTurn.id)).all()
            ledger = {"schema": "conversation_ledger_v1", "conversations": [list(row) for row in conversations],
                      "turns": [list(row) for row in turns]}
            ledger = {key: [[value.isoformat() if hasattr(value, "isoformat") else value for value in row]
                           for row in rows] if isinstance(rows, list) else rows for key, rows in ledger.items()}
            model_calls, tools, trajectory, run_status, turn_state, model_attempts = [], [], [], None, None, None
            if request_id:
                turn = session.scalar(select(AgentTurn).where(AgentTurn.user_id == settings.local_user_id,
                                                             AgentTurn.request_id == request_id))
                if turn:
                    run_status, turn_state = turn.status, turn.state_after
                    model_attempts = (turn.response or {}).get("facts", {}).get("meta", {}).get("timings", {}).get("model_attempts")
                    model_calls = [{k: getattr(c, k) for k in ("id", "operation_type", "model", "model_revision", "prompt_version",
                        "input_tokens", "output_tokens", "token_budget_charge", "latency_ms", "queue_ms", "interval_ms",
                        "provider_ms", "ttft_ms", "status", "retry_count", "error_code")}
                        for c in session.scalars(select(ModelCall).where(ModelCall.query_run_id == turn.id).order_by(ModelCall.created_at, ModelCall.id))]
                    tools = [{"name": i.tool_name, "ordinal": i.ordinal, "run_id": i.run_id,
                              "arguments": i.arguments, "status": i.status, "result": i.result}
                             for i in session.scalars(select(ToolInvocation).where(ToolInvocation.run_id == turn.id).order_by(ToolInvocation.ordinal))]
                    trajectory = [{"sequence": e.sequence, "type": e.kind, "data": e.payload}
                                  for e in session.scalars(select(AgentEvent).where(AgentEvent.run_id == turn.id).order_by(AgentEvent.sequence))]
            def fields(entity):
                return {c.name: value.isoformat() if hasattr(value, "isoformat") else value
                        for c in entity.__table__.columns for value in [getattr(entity, c.name)]}
            result = {"evaluation_host": True, "user_id": settings.local_user_id,
                      "multi_tenant_enabled": settings.multi_tenant_enabled, "provider_profile": profile,
                      "state_count": len(states), "event_count": len(events), "conversation_count": len(conversations),
                      "conversation_ledger_sha256": digest(ledger),
                      "states": [fields(s) for s in states], "write_events": [fields(e) for e in events],
                      "model_calls": model_calls, "model_attempts": model_attempts,
                      "query_max_model_calls": settings.query_max_model_calls,
                      "query_planning_max_calls": settings.query_max_model_calls,
                      "request_max_model_calls": app.state.query_service.max_total_model_calls,
                      "request_deadline_seconds": app.state.query_service.deadline_seconds,
                      "request_max_tokens": app.state.query_service.max_tokens,
                      "tool_invocations": tools, "trajectory": trajectory, "run_status": run_status, "turn_state": turn_state,
                      "query_max_tokens": settings.query_max_tokens,
                      "query_prompt_version": settings.query_prompt_version,
                      "as_of_frozen": as_of is not None,
                      "model_gate_shared": lock is not None or os.name != "nt" and settings.model_lock_path.resolve() == Path("/app/runtime/model-call.lock"),
                      "model_gate_identity": lock, **runtime}
        result["snapshot"] = current_snapshot(database, settings, as_of=(as_of or date.today()).isoformat())
        return result
    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18002)
    parser.add_argument("--as-of", type=date.fromisoformat)
    parser.add_argument("--deployment-evidence", type=Path,
                        help="Independent API/Pi process, source and shared-lock measurements; checked on each context probe")
    args = parser.parse_args()
    from interview_intelligence.config import load_settings
    from interview_intelligence.domain.models import create_database
    import uvicorn
    settings = load_settings()
    app = evaluation_app(create_database(settings.database_url, create_tables=False), settings, as_of=args.as_of,
                         deployment_evidence=args.deployment_evidence)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
