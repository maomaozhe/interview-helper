import importlib

import pytest
from sqlalchemy import func, select


def setup_review():
    models = importlib.import_module("interview_intelligence.domain.models")
    db = models.create_database("sqlite+pysqlite:///:memory:")
    with db.session() as session:
        with session.begin():
            question = models.CanonicalQuestion(canonical_text="Redis为什么快？", primary_topic_id="redis.performance", taxonomy_version="v1", question_type="PRINCIPLE")
            session.add(question)
        question_id = question.id
    return models, db, question_id


def test_review_replay_does_not_increment_review_count():
    models, db, question_id = setup_review()
    contracts = importlib.import_module("interview_intelligence.contracts")
    service = importlib.import_module("interview_intelligence.review.service").ReviewService(db)
    request = contracts.ReviewRequest(idempotency_key="same-request", items=[
        contracts.ReviewItem(canonical_question_id=question_id, status="WEAK", score=1),
    ])
    first = service.record("local", request)
    second = service.record("local", request)
    assert first == second
    with db.session() as session:
        state = session.scalar(select(models.UserQuestionState))
        assert state.status == "WEAK"
        assert state.review_count == 1
        assert session.scalar(select(models.UserRevision.state_revision)) == 1
        assert session.scalar(select(func.count(models.ReviewEvent.id))) == 1


def test_duplicate_key_with_changed_payload_is_conflict():
    _, db, question_id = setup_review()
    contracts = importlib.import_module("interview_intelligence.contracts")
    service = importlib.import_module("interview_intelligence.review.service").ReviewService(db)
    service.record("local", contracts.ReviewRequest(idempotency_key="key", items=[
        contracts.ReviewItem(canonical_question_id=question_id, status="WEAK"),
    ]))
    with pytest.raises(ValueError, match="IDEMPOTENCY_CONFLICT"):
        service.record("local", contracts.ReviewRequest(idempotency_key="key", items=[
            contracts.ReviewItem(canonical_question_id=question_id, status="MASTERED"),
        ]))


def test_unmatched_question_is_saved_without_creating_corpus_fact_then_resolved_once():
    models, db, question_id = setup_review()
    contracts = importlib.import_module("interview_intelligence.contracts")
    service = importlib.import_module("interview_intelligence.review.service").ReviewService(db)
    pending = service.record("local", contracts.ReviewRequest(idempotency_key="new-raw", items=[
        contracts.ReviewItem(raw_question="上次那道 Redis 题", status="WEAK"),
    ]))
    assert pending["items"][0]["resolution_status"] == "UNRESOLVED"
    with db.session() as session:
        assert session.scalar(select(func.count(models.QuestionOccurrence.id))) == 0
        assert session.scalar(select(func.count(models.UserQuestionState.id))) == 0
    event_id = pending["items"][0]["event_id"]
    resolved = service.record("local", contracts.ReviewRequest(
        operation="resolve", idempotency_key="resolution-1",
        resolve_event_id=event_id, canonical_question_id=question_id,
    ))
    repeated = service.record("local", contracts.ReviewRequest(
        operation="resolve", idempotency_key="resolution-2",
        resolve_event_id=event_id, canonical_question_id=question_id,
    ))
    assert resolved["items"][0]["resolution_status"] == "RESOLVED"
    assert repeated["items"][0]["resolution_status"] == "RESOLVED"
    with db.session() as session:
        assert session.scalar(select(models.UserQuestionState.review_count)) == 1
        assert session.scalar(select(func.count(models.ReviewEvent.id))) == 1


def test_batch_with_unknown_canonical_rolls_back_every_item():
    models, db, question_id = setup_review()
    contracts = importlib.import_module("interview_intelligence.contracts")
    service = importlib.import_module("interview_intelligence.review.service").ReviewService(db)
    with pytest.raises(KeyError):
        service.record("local", contracts.ReviewRequest(idempotency_key="batch", items=[
            contracts.ReviewItem(canonical_question_id=question_id, status="WEAK"),
            contracts.ReviewItem(canonical_question_id="missing", status="MASTERED"),
        ]))
    with db.session() as session:
        assert session.scalar(select(func.count(models.ReviewEvent.id))) == 0
        assert session.scalar(select(func.count(models.UserQuestionState.id))) == 0
