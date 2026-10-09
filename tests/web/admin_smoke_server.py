"""Isolated admin UI acceptance server with real access persistence and offline SQL queries.

Only binds loopback. The login token is deliberately test-only; production uses
its separately generated deployment secret. No live corpus or model is used.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "integration"))
os.environ.update(DATABASE_URL="sqlite+pysqlite:///:memory:", ELASTICSEARCH_URL="",
                  MODEL_API_KEY="", MODEL_BASE_URL="", ADMIN_ACCESS_TOKEN="")

import uvicorn
from test_analytics import seed_corpus
from interview_intelligence.agent.query_contract import QuerySpec
from interview_intelligence.api import create_app
from interview_intelligence.config import Settings


class OfflinePlanner:
    async def plan(self, run, context):
        return QuerySpec(action="LIST", page_size=20)


def main():
    with tempfile.TemporaryDirectory(prefix="interview-admin-acceptance-") as folder:
        database, _, _ = seed_corpus()
        settings = Settings(database_url="sqlite+pysqlite:///:memory:",
                            corpus_root=Path(folder), snapshot_root=Path(folder) / "snapshots",
                            app_signing_key="admin-acceptance-only-signing-key",
                            admin_access_token="admin-acceptance-only-token")
        try:
            uvicorn.run(create_app(database, settings, query_planner=OfflinePlanner()),
                        host="127.0.0.1", port=8013, log_level="warning", proxy_headers=False)
        finally:
            database.engine.dispose()


if __name__ == "__main__":
    main()
