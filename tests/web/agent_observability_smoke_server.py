"""Loopback-only observation UI fixture, isolated from the corpus and model services."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "integration"))
os.environ.update(DATABASE_URL="sqlite+pysqlite:///:memory:", ELASTICSEARCH_URL="",
    MODEL_API_KEY="", MODEL_BASE_URL="", ADMIN_ACCESS_TOKEN="", MULTI_TENANT_ENABLED="false")

import uvicorn
from fastapi.testclient import TestClient
from test_analytics import seed_corpus
from test_agent_observability import seed_turn, seed_call
from interview_intelligence.agent.query_contract import QuerySpec
from interview_intelligence.api import create_app
from interview_intelligence.config import Settings
from interview_intelligence.domain.models import AgentEvent, AgentTurn, ToolInvocation


class OfflinePlanner:
    async def plan(self, run, context):
        return QuerySpec(action="LIST", page_size=20)


def main():
    with tempfile.TemporaryDirectory(prefix="interview-agent-observation-") as folder:
        database, _, _ = seed_corpus()
        settings = Settings(corpus_root=Path(folder), snapshot_root=Path(folder) / "snapshots",
            app_signing_key="observation-acceptance-only-signing-key",
            admin_access_token="observation-acceptance-only-token")
        app = create_app(database, settings, query_planner=OfflinePlanner())
        for index in range(22):
            seed_turn(database, f"ui-list-{index}", age=index + 120)
        start = seed_turn(database, "ui-failed-run", status="FAILED", age=10)
        with database.session() as session, session.begin():
            turn = session.get(AgentTurn, "ui-failed-run")
            turn.message = "示例失败：查询 Redis 高频题"; turn.error_code = "MODEL_REQUEST_TIMEOUT"; turn.event_sequence = 205
            session.add(ToolInvocation(id="ui-tool-1", run_id=turn.id, ordinal=1, tool_name="search_questions",
                payload_hash="hash", status="FAILED", arguments={"action": "SEARCH", "search_query": "Redis"},
                result={"error_code": "MODEL_REQUEST_TIMEOUT"}))
            for sequence in range(1, 206):
                session.add(AgentEvent(run_id=turn.id, sequence=sequence, kind="stage" if sequence < 205 else "tool_error",
                    payload={"stage": "rerank", "tool": "search_questions", "action_id": "ui-tool-1",
                        "error_code": "MODEL_REQUEST_TIMEOUT" if sequence == 205 else None}, created_at=start))
            seed_call(session, query_run_id=turn.id)
        try:
            print("Isolated observation UI ready on 127.0.0.1:8014", flush=True)
            uvicorn.run(app, host="127.0.0.1", port=8014, log_level="warning", proxy_headers=False)
        finally:
            database.engine.dispose()


if __name__ == "__main__":
    main()
