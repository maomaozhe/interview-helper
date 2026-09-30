import json

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from interview_intelligence import cli
from interview_intelligence.api import create_app
from interview_intelligence.config import Settings
from test_analytics import seed_corpus


def test_default_stats_cli_omits_unset_optional_filters(monkeypatch):
    db, _, _ = seed_corpus()
    client = TestClient(create_app(db, Settings(database_url="sqlite+pysqlite:///:memory:")))
    monkeypatch.setattr(cli.httpx, "Client", lambda **kwargs: client)
    result = CliRunner().invoke(cli.app, ["stats", "--limit", "1"])
    assert result.exit_code == 0, result.output
    response = json.loads(result.output)
    assert response["meta"]["applied_filters"]["round"] is None
    assert response["meta"]["applied_filters"]["topic_l1"] is None
    assert response["data"]
