"""Planning and SEARCH share pipeline precedence without rebuilding context."""
from datetime import timedelta

import pytest

from interview_intelligence.agent.preferences import PreferenceUpdate
from interview_intelligence.agent.query_contract import QueryRequest, QuerySpec
from interview_intelligence.agent.query_service import QueryService
from interview_intelligence.domain.models import CorpusState, UserPreference, now_utc
from test_analytics import seed_corpus


def service_fixture(*, compact=True):
    db, _, _ = seed_corpus()
    with db.session() as session, session.begin():
        corpus = session.get(CorpusState, 1)
        corpus.current_revision = 1
        corpus.indexed_revision = corpus.current_revision

    class Retriever:
        calls = []

        def retrieve(self, query, eligible, pipeline, top_k):
            self.calls.append(pipeline)
            return {"data": [{"canonical_question_id": eligible[0]}],
                    "meta": {"pipeline": pipeline, "rerank_status": "COMPLETED"}}

    retriever = Retriever()
    service = QueryService(db, retriever, signing_key="pipeline-test", compact_context=compact)
    return db, service, retriever


def save_pipeline(service, value, *, version=0):
    service.preferences.update("pipeline", PreferenceUpdate(
        value=value, source_message="显式保存检索偏好", expected_version=version))


def search(service, run):
    return service.execute(run, "search_questions", QuerySpec(
        action="SEARCH", search_query="Redis", pipeline="HYBRID"))


@pytest.mark.parametrize("compact", [False, True])
@pytest.mark.parametrize("request_fields,preference_status,expected", [
    ({}, None, "HYBRID_RERANK"),
    ({}, "active", "BM25"),
    ({"pipeline": "DENSE"}, "active", "DENSE"),
    ({"pipeline": "HYBRID_RERANK"}, "active", "HYBRID_RERANK"),
    ({}, "expired", "HYBRID_RERANK"),
    ({}, "deleted", "HYBRID_RERANK"),
])
def test_pipeline_precedence_matches_planning_and_actual_retrieval(compact, request_fields, preference_status, expected):
    db, service, retriever = service_fixture(compact=compact)
    if preference_status:
        save_pipeline(service, "BM25")
        with db.session() as session, session.begin():
            preference = session.get(UserPreference, (service.user_id, "pipeline"))
            if preference_status == "expired":
                preference.expires_at = now_utc() - timedelta(days=1)
            elif preference_status == "deleted":
                preference.deleted = True
    run = service.begin(QueryRequest(message="找 Redis 相关题", request_id="pipeline", **request_fields))
    assert service.model_context(run)["requested_pipeline"] == expected
    result = search(service, run)
    assert retriever.calls == [expected]
    assert result["planning"]["spec"]["pipeline"] == expected
    assert result["facts"]["meta"]["requested_pipeline"] == expected


@pytest.mark.parametrize("explicit", [False, True])
def test_execution_reads_current_preference_without_overriding_explicit_request(explicit):
    _, service, retriever = service_fixture()
    save_pipeline(service, "BM25")
    fields = {"pipeline": "BM25"} if explicit else {}
    run = service.begin(QueryRequest(message="找 Redis 相关题", request_id="changed-preference", **fields))
    assert service.model_context(run)["requested_pipeline"] == "BM25"
    save_pipeline(service, "DENSE", version=1)
    search(service, run)
    assert retriever.calls == ["BM25" if explicit else "DENSE"]


@pytest.mark.parametrize("override,expected", [({}, "BM25"), ({"pipeline": "HYBRID_RERANK"}, "HYBRID_RERANK")])
def test_requery_inherited_pipeline_remains_explicit_against_new_preferences(override, expected):
    _, service, retriever = service_fixture()
    source = service.begin(QueryRequest(message="找 Redis 相关题", request_id="source", pipeline="BM25"))
    search(service, source)
    service.finish(source, provider="fixture")
    save_pipeline(service, "DENSE")
    refresh = service.begin(QueryRequest(message="找 Redis 相关题", request_id="requery",
                                         requery_of_run_id=source.id, **override))
    assert "pipeline" in refresh.request.model_fields_set
    assert service.model_context(refresh)["requested_pipeline"] == expected
    search(service, refresh)
    assert retriever.calls == ["BM25", expected]


@pytest.mark.parametrize("explicit", [False, True])
def test_search_execution_does_not_read_planning_catalog_or_feedback(monkeypatch, explicit):
    _, service, retriever = service_fixture()
    save_pipeline(service, "DENSE")
    fields = {"pipeline": "BM25"} if explicit else {}
    run = service.begin(QueryRequest(message="找 Redis 相关题", request_id="no-context-rebuild", **fields))
    service.model_context(run)  # Planning still reads and validates the complete context.

    def unexpected_read(*args, **kwargs):
        pytest.fail("SEARCH execution rebuilt planning catalog or feedback")

    for target, name in [(service, "_filter_catalog"), (service.feedback_memory, "selected"),
                         (service.feedback_memory, "matching")]:
        monkeypatch.setattr(target, name, unexpected_read)
    defaults = service.preferences.defaults
    preference_reads = []

    def read_defaults():
        preference_reads.append(True)
        return defaults()

    monkeypatch.setattr(service.preferences, "defaults", read_defaults)
    search(service, run)
    assert retriever.calls == ["BM25" if explicit else "DENSE"]
    assert len(preference_reads) == (0 if explicit else 1)
