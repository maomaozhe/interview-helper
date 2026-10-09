"""Optional PostgreSQL concurrency gate in an isolated, disposable schema.

Set ACCESS_TEST_DATABASE_URL to a test-capable PostgreSQL connection. The test
creates its own random schema, never selects the application's tables, and drops
only that schema. It verifies cross-instance locking rather than one process's
thread lock. Credentials are never included in assertion messages or output.
"""
from concurrent.futures import ThreadPoolExecutor
import os
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from starlette.requests import Request

from interview_intelligence.access import AccessService
from interview_intelligence.access_models import AccessCounter, AccessVisitor
from interview_intelligence.config import Settings
from interview_intelligence.domain.models import create_database


@pytest.mark.skipif(not os.environ.get("ACCESS_TEST_DATABASE_URL"), reason="explicit PostgreSQL test connection required")
def test_postgres_cross_instance_quota_admission_is_atomic():
    url = make_url(os.environ["ACCESS_TEST_DATABASE_URL"])
    assert url.get_backend_name() == "postgresql"
    schema = "access_acceptance_" + uuid4().hex
    control = create_engine(url)
    databases = []
    with control.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        isolated = url.update_query_dict({"options": f"-csearch_path={schema}"})
        settings = Settings(database_url=isolated.render_as_string(hide_password=False),
                            app_signing_key="postgres-isolated-test-key")
        databases = [create_database(settings.database_url) for _ in range(2)]
        services = [AccessService(database, settings) for database in databases]
        visitor_id = str(uuid4())
        with services[0].transaction() as session:
            policy = services[0].locked_policy(session)
            policy.quota_enabled = True
            policy.quota_limit = 10
            policy.quota_period = "LIFETIME"
            session.add(AccessVisitor(id=visitor_id, ip="198.51.100.25", user_agent="isolated-test",
                                     device="Desktop", browser="Other"))

        def admission(index):
            request = Request({"type": "http", "method": "POST", "path": "/api/query",
                               "headers": [], "client": ("198.51.100.25", 50000),
                               "server": ("localhost", 80), "scheme": "http", "query_string": b""})
            request.state.access_visitor_id = visitor_id
            request.state.access_ip = "198.51.100.25"
            try:
                services[index % 2].admit(request, f"pg-{index}", {"message": f"query {index}"})
                return 200
            except HTTPException as error:
                return error.status_code

        with ThreadPoolExecutor(max_workers=16) as pool:
            statuses = list(pool.map(admission, range(32)))
        assert statuses.count(200) == 10
        assert statuses.count(429) == 22
        with databases[1].session() as session:
            counters = list(session.scalars(select(AccessCounter).where(
                AccessCounter.key == f"quota:DEVICE:{visitor_id}:LIFETIME")))
            assert len(counters) == 1
            assert counters[0].used == 10
        # A new service/engine sees the same persisted limit.
        assert admission(100) == 429
    finally:
        for database in databases:
            database.engine.dispose()
        with control.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        control.dispose()
