"""Loopback-only tenant UI acceptance server, with SQL-only offline AI planning.

The injected planner uses the seeded sample corpus and never contacts a model
provider. Credentials and encryption keys here are disposable test fixtures.
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
from cryptography.fernet import Fernet
from test_analytics import seed_corpus
from interview_intelligence.agent.query_contract import QuerySpec
from interview_intelligence.api import create_app
from interview_intelligence.config import Settings
from interview_intelligence.tenant import password_hash
from interview_intelligence.tenant_models import TenantAccount


class OfflinePlanner:
    async def plan(self, run, context):
        return QuerySpec(action="LIST", page_size=20, final=True)


def main():
    with tempfile.TemporaryDirectory(prefix="interview-tenant-acceptance-") as folder:
        database, _, _ = seed_corpus()
        with database.session() as session, session.begin():
            session.add(TenantAccount(username="exhausted-fixture", password_hash=password_hash("acceptance-pass-10"), trial_used=10))
        settings = Settings(database_url="sqlite+pysqlite:///:memory:",
                            corpus_root=Path(folder), snapshot_root=Path(folder) / "snapshots",
                            app_signing_key="tenant-acceptance-only-signing-key",
                            multi_tenant_enabled=True, tenant_trial_limit=10,
                            tenant_default_api_key="tenant-acceptance-only-system-key",
                            tenant_default_base_url="https://system.example.test/v1",
                            tenant_default_query_model="offline-qwen-fixture",
                            model_credential_key=Fernet.generate_key().decode())
        try:
            uvicorn.run(create_app(database, settings, query_planner=OfflinePlanner()),
                        host="127.0.0.1", port=8014, log_level="warning", proxy_headers=False)
        finally:
            database.engine.dispose()


if __name__ == "__main__":
    main()
