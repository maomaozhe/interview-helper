from interview_intelligence.search.service import rrf, search_questions
from interview_intelligence.contracts import FilterSpec
from test_analytics import seed_corpus


def test_rrf_rank_begins_at_one_and_ties_use_stable_id():
    result = rrf([("b", 9), ("a", 8)], [("a", 0.9), ("c", 0.8)])
    assert [item["canonical_question_id"] for item in result] == ["a", "b", "c"]
    assert result[0]["rrf_score"] == 1 / 62 + 1 / 61


class FakeRetriever:
    def retrieve(self, query, eligible_ids, pipeline, top_k):
        assert set(eligible_ids) == {self.allowed}
        return {"data": [{"canonical_question_id": self.allowed, "score": 0.7}],
                "meta": {"pipeline": pipeline, "indexed_revision": 0}}


def test_search_filters_in_sql_before_retrieval_and_adds_matching_facts():
    db, _, persistence_id = seed_corpus()
    retriever = FakeRetriever()
    retriever.allowed = persistence_id
    with db.session() as session:
        result = search_questions(session, retriever, "Redis 持久化", FilterSpec(company="字节", round="SECOND"))
    assert result["data"][0]["occurrence_count"] == 1
    assert result["data"][0]["canonical_question_id"] == persistence_id
