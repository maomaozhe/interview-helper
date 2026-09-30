from interview_intelligence.agent.service import AgentService
from interview_intelligence.domain.models import Interview
from interview_intelligence.domain.models import CorpusState, UserRevision
from sqlalchemy import select
from test_analytics import seed_corpus
import pytest


def test_recent_topic_question_routes_to_sql_stats():
    db, _, _ = seed_corpus()
    agent = AgentService(db)
    result = agent.chat("最近三个月 Redis 高频题有哪些？")
    assert result["intent"] == "ANALYTICS"
    assert result["tool_trace"][0]["name"] == "query_question_stats"
    assert result["facts"]["sample_counts"]["occurrences"] == 2
    assert "2" in result["answer"]


def test_byte_second_round_preparation_uses_gap_before_detail():
    db, _, persistence_id = seed_corpus()
    with db.session() as session:
        with session.begin():
            interview = session.scalar(select(Interview).where(Interview.company_normalized == "字节",
                                                           Interview.round == "SECOND"))
            interview.job_family = "BACKEND"
            interview.language_tags = ["JAVA"]
    agent = AgentService(db)
    result = agent.chat("字节 Java 后端二面，按面经和复习记录给我冲刺清单")
    assert result["intent"] == "COMPOSITE"
    names = [step["name"] for step in result["tool_trace"]]
    assert names[:2] == ["query_question_stats", "get_user_question_state"]
    assert result["facts"]["questions"][0]["canonical_question_id"] == persistence_id


@pytest.mark.parametrize("changed", ["corpus", "review"])
def test_composite_rejects_revision_change_during_tool_chain(changed):
    db, _, _ = seed_corpus()
    agent = AgentService(db)
    original = agent.reviews.get_states

    def change_after_read(user_id, ids):
        states = original(user_id, ids)
        with db.session() as session:
            with session.begin():
                if changed == "corpus":
                    session.get(CorpusState, 1).current_revision += 1
                else:
                    revision = session.get(UserRevision, user_id)
                    if revision is None:
                        revision = UserRevision(user_id=user_id, state_revision=0)
                        session.add(revision)
                    revision.state_revision += 1
        return states

    agent.reviews.get_states = change_after_read
    with pytest.raises(ValueError, match="SNAPSHOT_CHANGED"):
        agent.chat("按复习缺口给我冲刺清单")
