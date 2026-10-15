"""Frequency-ranked semantic searches use scoped corpus counts before page cutoff."""

import pytest
from sqlalchemy import select

from interview_intelligence.agent.query_contract import QueryRequest, QuerySpec
from interview_intelligence.agent.query_service import QueryService
from interview_intelligence.domain.models import CanonicalQuestion, CorpusState, Interview, QuestionOccurrence
from test_analytics import seed_corpus


class VerifiedRetriever:
    def __init__(self, low_id, high_id):
        self.ids = [low_id, high_id]
        self.top_ks = []

    def retrieve(self, query, eligible, pipeline, top_k):
        self.top_ks.append(top_k)
        ids = [identity for identity in self.ids if identity in eligible][:top_k]
        return {"data": [{"canonical_question_id": identity} for identity in ids],
                "meta": {"pipeline": pipeline, "rerank_status": "COMPLETED",
                    "candidate_verification_status": "COMPLETE", "invalid_candidate_count": 0,
                    "candidate_count": len(ids), "candidate_ids": ids,
                    "result_selection_policy": "top5_missing_facet_once_v1",
                    "rerank_audit": {"candidates": [
                        {"canonical_question_id": identity, "decision": "RETURNED"} for identity in ids]}}}


def service_fixture():
    db, high_id, low_id = seed_corpus()
    with db.session() as session, session.begin():
        for interview in session.scalars(select(Interview)):
            interview.company_normalized = "字节"
        for question in session.scalars(select(CanonicalQuestion)):
            question.canonical_text = ("求数组中的第 k 大元素" if question.id == high_id
                                       else "反转单向链表")
        for occurrence in session.scalars(select(QuestionOccurrence)):
            question = session.get(CanonicalQuestion, occurrence.canonical_question_id)
            occurrence.raw_question = occurrence.normalized_question = question.canonical_text
            occurrence.source_spans = [{"quote": question.canonical_text}]
        corpus = session.get(CorpusState, 1)
        corpus.current_revision = corpus.indexed_revision = 1
    retriever = VerifiedRetriever(low_id, high_id)
    return QueryService(db, retriever, signing_key="test"), retriever, high_id, low_id


def search(service, request, *, page_size=1):
    run = service.begin(request)
    result = service.execute(run, "search_questions", QuerySpec(action="SEARCH",
        filters={"company": "字节跳动"}, sort="frequency", page_size=page_size,
        search_query="字节 算法 数组 链表", relevance_query="独立算法求解题"))
    service.finish(run, provider="fixture")
    return run, result


def test_frequency_search_ranks_all_verified_candidates_before_page_cutoff():
    service, retriever, high_id, low_id = service_fixture()
    run, result = search(service, QueryRequest(
        message="字节跳动算法题，按照出现频率排名", request_id="frequency-search"))
    assert retriever.top_ks == [50]
    assert [row["canonical_question_id"] for row in result["facts"]["data"]] == [high_id]
    assert result["facts"]["data"][0]["occurrence_count"] == 2
    assert result["facts"]["meta"]["frequency_ranking"] == {
        "basis": "occurrence_count", "matched_candidates": 2,
        "candidate_pool_limit": 50, "scope": "verified_semantic_candidates"}
    assert result["facts"]["meta"]["returned_count"] == 1
    assert result["facts"]["meta"]["retrieval_selection_policy"] == "top5_missing_facet_once_v1"
    assert result["facts"]["meta"]["result_selection_policy"] == "occurrence_count_desc_then_retrieval_order_v1"
    assert "本次语义核验候选中" in result["answer"]
    assert "最多 50 个检索候选" in result["warnings"][0]
    assert result["facts"]["meta"]["rerank_audit"]["candidates"][0]["decision"] == "PAGE_CUTOFF"
    assert run.state["search_ranking"] == "frequency"
    assert low_id not in run.state["current_page_ids"]


def test_frequency_requery_and_continuation_keep_order_but_explicit_relevance_resets_it():
    service, retriever, high_id, low_id = service_fixture()
    first, _ = search(service, QueryRequest(
        message="字节跳动算法题，按出现频次排名", request_id="first"))
    _, requery = search(service, QueryRequest(message="重新检索", request_id="requery",
        requery_of_run_id=first.id))
    assert requery["facts"]["data"][0]["canonical_question_id"] == high_id
    _, continued = search(service, QueryRequest(message="再找一些", request_id="continue",
        conversation_id=first.conversation_id, expected_version=1))
    assert continued["facts"]["data"][0]["canonical_question_id"] == high_id
    _, relevance = search(service, QueryRequest(message="再找一些，按相关度排列", request_id="relevance",
        conversation_id=first.conversation_id, expected_version=2))
    assert relevance["facts"]["data"][0]["canonical_question_id"] == low_id
    assert relevance["facts"]["meta"]["search_ranking"] == "relevance"
    assert relevance["facts"]["meta"]["result_selection_policy"] == "top5_missing_facet_once_v1"
    assert "retrieval_selection_policy" not in relevance["facts"]["meta"]
    assert retriever.top_ks == [50, 50, 50, 1]


def test_equal_frequency_preserves_verified_retrieval_order():
    service, _, high_id, low_id = service_fixture()
    with service.database.session() as session, session.begin():
        interview = session.scalar(select(Interview).join(QuestionOccurrence).where(
            QuestionOccurrence.canonical_question_id == high_id))
        interview.company_normalized = "腾讯"
    _, result = search(service, QueryRequest(
        message="字节跳动算法题，按出现频次排名", request_id="equal-count"), page_size=2)
    assert [row["occurrence_count"] for row in result["facts"]["data"]] == [1, 1]
    assert [row["canonical_question_id"] for row in result["facts"]["data"]] == [low_id, high_id]


@pytest.mark.parametrize(("message", "expected"), [
    ("字节跳动算法题，按照出现频率排名", "frequency"),
    ("不要按频率排名", "relevance"),
    ("按相关度排列，不要按频率排名", "relevance"),
    ("按频率排名，不要按频率排名", "relevance"),
    ("不要按频率排名，改按频次排序", "frequency"),
    ("不要跑题，按出现频率排名", "frequency"),
])
def test_explicit_ranking_corrections(message, expected):
    assert QueryService._explicit_search_ranking(message) == expected


def test_negative_frequency_correction_overrides_inherited_frequency():
    service, retriever, _, low_id = service_fixture()
    first, _ = search(service, QueryRequest(
        message="字节跳动算法题，按出现频次排名", request_id="positive-first"))
    _, corrected = search(service, QueryRequest(message="不要按频率排名", request_id="negative-next",
        conversation_id=first.conversation_id, expected_version=1))
    assert corrected["facts"]["meta"]["search_ranking"] == "relevance"
    assert corrected["facts"]["data"][0]["canonical_question_id"] == low_id
    assert retriever.top_ks == [50, 1]
