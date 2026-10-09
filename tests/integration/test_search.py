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


def test_soft_type_preference_uses_same_occurrence_scope_and_keeps_broad_projects():
    from sqlalchemy import select
    from interview_intelligence.domain.models import CanonicalQuestion, QuestionOccurrence, Interview
    db, project_id, design_id = seed_corpus()
    with db.session() as session, session.begin():
        session.get(CanonicalQuestion, project_id).question_type = "PROJECT"
        session.get(CanonicalQuestion, design_id).question_type = "SYSTEM_DESIGN"
        for occurrence, company in session.execute(select(QuestionOccurrence, Interview.company_normalized).join(
                Interview, QuestionOccurrence.interview_id == Interview.id)):
            occurrence.question_type = "SYSTEM_DESIGN" if company == "腾讯" or occurrence.canonical_question_id == design_id else "PROJECT"
            if occurrence.canonical_question_id == project_id and company == "字节":
                byte_project_occurrence = occurrence.id
    class Retriever:
        supports_question_type_preference = True
        calls = []
        def retrieve(self, query, eligible, pipeline, top_k, **options):
            self.calls.append((eligible, options))
            return {"data": [{"canonical_question_id": qid} for qid in eligible], "meta": {"pipeline": pipeline}}
    retriever = Retriever()
    with db.session() as session:
        result = search_questions(session, retriever, "business scene", FilterSpec(company="字节"),
                                  preferred_question_type="SYSTEM_DESIGN")
    eligible, options = retriever.calls[0]
    assert set(eligible) == {project_id, design_id}
    assert options == {"preferred_question_type": "SYSTEM_DESIGN", "preferred_eligible_ids": [design_id]}
    project = next(row for row in result["data"] if row["canonical_question_id"] == project_id)
    assert project["question_type"] == "PROJECT" and project["occurrence_count"] == 1
    assert {source["occurrence_id"] for source in project["sources"]} == {byte_project_occurrence}
    with db.session() as session:
        result = search_questions(session, retriever, "business scene", FilterSpec(company="字节", round="FIRST"),
                                  preferred_question_type="SYSTEM_DESIGN")
    assert retriever.calls[-1] == ([project_id], {"preferred_question_type": "SYSTEM_DESIGN", "preferred_eligible_ids": []})
    assert result["data"][0]["canonical_question_id"] == project_id


def test_soft_type_hint_does_not_change_legacy_fake_or_explicit_scope():
    db, _, persistence_id = seed_corpus()
    old = FakeRetriever(); old.allowed = persistence_id
    with db.session() as session:
        assert search_questions(session, old, "Redis", FilterSpec(company="字节", round="SECOND"),
                                preferred_question_type="SCENARIO")["data"][0]["canonical_question_id"] == persistence_id
    class Retriever:
        supports_question_type_preference = True
        def retrieve(self, query, eligible, pipeline, top_k, **options):
            assert options == {} and eligible == [persistence_id]
            return {"data": [{"canonical_question_id": persistence_id}], "meta": {}}
    with db.session() as session:
        search_questions(session, Retriever(), "Redis", FilterSpec(question_type="KNOWLEDGE"), preferred_question_type="SCENARIO")
