"""Configured complex-task budgets reach the host, search and durable run lease."""
import time
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from interview_intelligence.agent.query_contract import QueryRequest
from interview_intelligence.agent.query_service import QueryService
from interview_intelligence.api import create_app
from interview_intelligence.config import Settings
from interview_intelligence.domain.models import create_database


def test_api_uses_selected_budgets_for_planning_execution_health_and_run_watchdog():
    settings = Settings(database_url="sqlite+pysqlite:///:memory:", query_max_model_calls=7,
        query_max_total_model_calls=13, query_max_tokens=200000, query_deadline_seconds=700)
    app = create_app(create_database(settings.database_url), settings)
    with TestClient(app) as client:
        service = app.state.query_service
        run = service.begin(QueryRequest(message="核查题库覆盖并解释", request_id="custom-budget"))
        assert service.max_planning_calls == 7
        assert run.limits.max_calls == 13 and run.limits.max_tokens == 200000
        assert 690 < run.limits.deadline - time.monotonic() <= 700
        context = service.model_context(run)
        assert context["budget"]["remaining_planning_calls"] == 7
        assert context["budget"]["remaining_shared_calls"] == 13
        snapshot = client.get(f"/api/runs/{run.id}").json()["data"]
        lease = datetime.fromisoformat(snapshot["lease_until"])
        assert 700 < (lease - datetime.now(timezone.utc)).total_seconds() <= 710
        health = client.get("/api/health").json()["data"]
        assert health["query_request_limits"] == {"max_planning_calls": 7, "max_model_calls": 13,
            "max_tool_calls": 8, "max_tokens": 200000, "deadline_seconds": 700}
        assert health["search_request_limits"]["max_model_calls"] == 13
        for _ in range(13):
            run.limits.attempt()
        with pytest.raises(ValueError, match="QUERY_MODEL_BUDGET_EXCEEDED"):
            run.limits.attempt()
        assert run.limits.calls == 13 and run.model_calls == 0


def test_direct_query_service_and_api_settings_share_complex_task_defaults(tmp_path):
    settings = Settings()
    service = QueryService(create_database("sqlite+pysqlite:///:memory:"), signing_key="defaults",
        feedback_root=tmp_path / "feedback")
    run = service.begin(QueryRequest(message="分析题库", request_id="default-budget"))
    assert service.max_planning_calls == settings.query_max_model_calls == 8
    assert run.limits.max_calls == settings.query_max_total_model_calls == 24
    assert run.limits.max_tokens == settings.query_max_tokens == 256000
    assert service.deadline_seconds == settings.query_deadline_seconds == 600


@pytest.mark.parametrize("field,value", [
    ("query_max_model_calls", 0), ("query_max_model_calls", 33),
    ("query_max_total_model_calls", 0), ("query_max_total_model_calls", 129),
    ("query_max_tokens", 4095), ("query_max_tokens", 1048577),
    ("query_deadline_seconds", 0), ("query_deadline_seconds", 1801),
])
def test_query_budget_configuration_keeps_finite_bounds(field, value):
    with pytest.raises(ValueError):
        Settings(**{field: value})
