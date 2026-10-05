import pytest
import json
import httpx
from sqlalchemy import func, select

from interview_intelligence.agent.task_annotation import annotate_tasks
from interview_intelligence.config import Settings
from interview_intelligence.domain.models import CorpusState, ModelCall, OccurrenceTaskAnnotation, QuestionOccurrence
from test_analytics import seed_corpus


def labels(payload):
    return {"items": [{"occurrence_id": row["occurrence_id"], "response_form": "CODE",
        "coding_focus": "ENGINEERING", "confidence": 0.99, "evidence_quote": row["raw_question"]} for row in payload]}


def test_backfill_resumes_without_changing_existing_occurrences():
    database, _, _ = seed_corpus()
    settings = Settings()
    with database.session() as session:
        before = session.get(CorpusState, 1).current_revision
    first = annotate_tasks(database, settings, limit=2, batch_size=2, classifier=labels)
    second = annotate_tasks(database, settings, classifier=labels)
    assert first["changed"] == 2 and second["changed"] == 1
    assert annotate_tasks(database, settings, dry_run=True)["pending_in_batch"] == 0
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(OccurrenceTaskAnnotation)) == 3
        assert session.get(CorpusState, 1).current_revision == before
        assert session.get(CorpusState, 1).task_annotation_revision == 2


def test_one_ungrounded_label_rejects_the_entire_batch():
    database, _, _ = seed_corpus()
    def bad(payload):
        result = labels(payload)
        result["items"][-1]["evidence_quote"] = "fabricated source quote"
        return result
    with pytest.raises(ValueError, match="ANNOTATION_UNGROUNDED"):
        annotate_tasks(database, Settings(), classifier=bad)
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(OccurrenceTaskAnnotation)) == 0
        assert session.get(CorpusState, 1).task_annotation_revision == 0


def test_invalid_model_schema_gets_one_bounded_repair_before_atomic_commit(monkeypatch, tmp_path):
    database, _, _ = seed_corpus()
    with database.session() as session:
        quotes = [r.raw_question for r in session.scalars(select(QuestionOccurrence).order_by(QuestionOccurrence.id))]
    calls = []
    def provider(request):
        calls.append(json.loads(request.content))
        items = [{"i":i, "f":"CODE", "c":"ENGINEERING", "p":.99} for i in range(len(quotes))]
        if len(calls) == 1:
            items[0]["f"] = "MIXED"
        return httpx.Response(200, json={"model": "test", "choices": [{"finish_reason": "stop",
            "message": {"content": json.dumps({"items": items})}}], "usage": {"prompt_tokens": 100, "completion_tokens": 50}})
    client_type = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client_type(transport=httpx.MockTransport(provider), **kwargs))
    result = annotate_tasks(database, Settings(model_api_key="test", model_base_url="http://model.test/v1",
        model_lock_path=tmp_path / "gate", model_min_interval_seconds=0), max_calls=2)
    assert result["changed"] == 3 and result["model_calls"] == 2
    assert len(calls) == 2 and "校验失败" in calls[-1]["messages"][-1]["content"]
    with database.session() as session:
        telemetry = list(session.scalars(select(ModelCall).order_by(ModelCall.created_at)))
        assert [row.status for row in telemetry] == ["FAILED", "SUCCEEDED"]
        assert telemetry[-1].retry_count == 1
        assert session.get(CorpusState, 1).task_annotation_revision == 1
        stored = list(session.scalars(select(OccurrenceTaskAnnotation)))
        assert {row.evidence["quote"] for row in stored} == set(quotes)
        assert all(row.evidence["quote_origin"] == "host_original" for row in stored)
