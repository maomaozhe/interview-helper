from interview_intelligence.agent.service import AgentService
from interview_intelligence.review.service import ReviewService
from test_analytics import seed_corpus
from interview_intelligence.domain.models import ReviewEvent
from sqlalchemy import func, select


def test_two_review_clauses_update_distinct_questions_once():
    db, fast_id, persistence_id = seed_corpus()
    agent = AgentService(db)
    result = agent.chat("记录 Redis持久化 没答出，Redis为什么快 答得不错", request_id="review-two")
    assert result["intent"] == "USER_STATE"
    states = ReviewService(db).get_states("local", [fast_id, persistence_id])["states"]
    assert states[persistence_id]["status"] == "WEAK"
    assert states[fast_id]["status"] == "REVIEWED"
    assert states[fast_id]["review_count"] == 1
    assert agent.chat("记录 Redis持久化 没答出，Redis为什么快 答得不错", request_id="review-two")["facts"] == result["facts"]


def test_ambiguous_review_clause_does_not_write_any_batch_item():
    db, _, _ = seed_corpus()
    result = AgentService(db).chat("记录 Redis 没答出，Spring事务 掌握", request_id="ambiguous")
    assert result["needs_clarification"] is True
    with db.session() as session:
        assert session.scalar(select(func.count(ReviewEvent.id))) == 0
