"""Read-only, credential-free progress for a dedicated evaluation actor."""
import argparse
import json
from sqlalchemy import select, func

from interview_intelligence.config import load_settings
from interview_intelligence.domain.models import AgentTurn, ModelCall, create_database

if __name__ == "__main__":
    p = argparse.ArgumentParser(); p.add_argument("--user", required=True); args = p.parse_args()
    if not args.user.startswith("eval-"): raise SystemExit("EVALUATION_ACTOR_REQUIRED")
    database = create_database(load_settings().database_url, create_tables=False)
    with database.session() as session:
        turns = list(session.scalars(select(AgentTurn).where(AgentTurn.user_id == args.user).order_by(AgentTurn.created_at)))
        calls = list(session.scalars(select(ModelCall).where(ModelCall.query_run_id.in_([t.id for t in turns]))))
        recent = list(session.scalars(select(ModelCall).order_by(ModelCall.created_at.desc()).limit(6)))
        print(json.dumps({"turns": len(turns), "succeeded": sum(t.status == "SUCCEEDED" for t in turns),
            "last_turns": [{"message": t.message, "status": t.status, "error": t.error_code} for t in turns[-4:]],
            "model_calls": len(calls), "last_calls": [{"operation": c.operation_type, "status": c.status,
                "latency_ms": c.latency_ms, "provider_ms": c.provider_ms, "queue_ms": c.queue_ms,
                "input_tokens": c.input_tokens, "output_tokens": c.output_tokens} for c in recent]}, ensure_ascii=False))
