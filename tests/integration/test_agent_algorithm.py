from sqlalchemy import select

from interview_intelligence.agent.service import AgentService
from interview_intelligence.domain.models import AlgorithmMatch, CanonicalQuestion, QuestionOccurrence
from test_analytics import seed_corpus


def test_algorithm_query_uses_stats_and_detail_without_inventing_id():
    db, _, persistence_id = seed_corpus()
    with db.session() as session:
        with session.begin():
            canonical = session.get(CanonicalQuestion, persistence_id)
            canonical.question_type = "ALGORITHM"
            occurrence = session.scalar(select(QuestionOccurrence).where(
                QuestionOccurrence.canonical_question_id == persistence_id))
            occurrence.question_type = "ALGORITHM"
            session.add(AlgorithmMatch(occurrence_id=occurrence.id, coding_kind="CODING_TASK",
                                       platform=None, problem_id=None, match_status="UNMATCHED",
                                       match_basis="DESCRIPTION_ONLY", confidence=0))
    result = AgentService(db).chat("字节二面手撕算法题有哪些？")
    assert [step["name"] for step in result["tool_trace"]][:2] == ["query_question_stats", "get_question_detail"]
    assert result["facts"]["questions"][0]["algorithm_matches"][0]["problem_id"] is None
