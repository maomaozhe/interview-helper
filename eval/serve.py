"""Dedicated evaluation host. Context and telemetry exist only on this host."""
from __future__ import annotations

import argparse
import os
from datetime import date
from pathlib import Path

from sqlalchemy import func, select

from eval.snapshot import current_snapshot
from eval.validate_gold import require


def evaluation_app(database, settings, *, as_of=None):
    require(settings.local_user_id.startswith("eval-"), "EVALUATION_ACTOR_REQUIRED")
    from interview_intelligence.api import create_app
    from interview_intelligence.domain.models import AgentConversation, AgentEvent, AgentTurn, ModelCall, ReviewEvent, ToolInvocation, UserQuestionState
    app = create_app(database, settings)
    if as_of is not None:
        # Only the dedicated evaluation host freezes time for reproducible replay.
        app.state.query_service.today = lambda: as_of

    @app.get("/api/evaluation/context")
    def context(request_id: str | None = None):
        with database.session() as session:
            states = list(session.scalars(select(UserQuestionState).where(UserQuestionState.user_id == settings.local_user_id)))
            events = list(session.scalars(select(ReviewEvent).where(ReviewEvent.user_id == settings.local_user_id)))
            conversations = session.scalar(select(func.count()).select_from(AgentConversation)
                                          .where(AgentConversation.user_id == settings.local_user_id)) or 0
            model_calls, tools, trajectory, run_status, turn_state = [], [], [], None, None
            if request_id:
                turn = session.scalar(select(AgentTurn).where(AgentTurn.user_id == settings.local_user_id,
                                                             AgentTurn.request_id == request_id))
                if turn:
                    run_status, turn_state = turn.status, turn.state_after
                    model_calls = [{k: getattr(c, k) for k in ("id", "operation_type", "model", "model_revision", "prompt_version",
                        "input_tokens", "output_tokens", "token_budget_charge", "latency_ms", "queue_ms", "interval_ms",
                        "provider_ms", "ttft_ms", "status", "retry_count", "error_code")}
                        for c in session.scalars(select(ModelCall).where(ModelCall.query_run_id == turn.id).order_by(ModelCall.created_at, ModelCall.id))]
                    tools = [{"name": i.tool_name, "arguments": i.arguments, "status": i.status, "result": i.result}
                             for i in session.scalars(select(ToolInvocation).where(ToolInvocation.run_id == turn.id).order_by(ToolInvocation.ordinal))]
                    trajectory = [{"sequence": e.sequence, "type": e.kind, "data": e.payload}
                                  for e in session.scalars(select(AgentEvent).where(AgentEvent.run_id == turn.id).order_by(AgentEvent.sequence))]
            def fields(entity):
                return {c.name: value.isoformat() if hasattr(value, "isoformat") else value
                        for c in entity.__table__.columns for value in [getattr(entity, c.name)]}
            result = {"evaluation_host": True, "user_id": settings.local_user_id,
                      "state_count": len(states), "event_count": len(events), "conversation_count": conversations,
                      "states": [fields(s) for s in states], "write_events": [fields(e) for e in events],
                      "model_calls": model_calls, "query_max_model_calls": settings.query_max_model_calls,
                      "tool_invocations": tools, "trajectory": trajectory, "run_status": run_status, "turn_state": turn_state,
                      "query_max_tokens": settings.query_max_tokens,
                      "query_prompt_version": settings.query_prompt_version,
                      "as_of_frozen": as_of is not None,
                      "model_gate_shared": os.name != "nt" and settings.model_lock_path.resolve() == Path("/app/runtime/model-call.lock")}
        result["snapshot"] = current_snapshot(database, settings, as_of=(as_of or date.today()).isoformat())
        return result
    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18002)
    parser.add_argument("--as-of", type=date.fromisoformat)
    args = parser.parse_args()
    from interview_intelligence.config import load_settings
    from interview_intelligence.domain.models import create_database
    import uvicorn
    settings = load_settings()
    app = evaluation_app(create_database(settings.database_url, create_tables=False), settings, as_of=args.as_of)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
