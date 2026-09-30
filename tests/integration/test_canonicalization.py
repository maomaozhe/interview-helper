import importlib
from types import SimpleNamespace

import pytest

from sqlalchemy import select


class TwoDimensionalEncoder:
    version = "test-embedding-v1"

    def embed(self, text):
        return [1.0, 0.0] if "Redis" in text else [0.0, 1.0]


class SemanticJudge:
    version = "test-judge-v1"

    def __init__(self):
        self.calls = []

    def judge(self, incoming, candidate):
        self.calls.append((incoming, candidate))
        if incoming == candidate or {incoming, candidate} == {"Redis为什么快？", "为什么Redis性能这么高？"}:
            return "SAME"
        if "单线程" in incoming and "Redis" in candidate:
            return "RELATED"
        return "DIFFERENT"


def test_semantic_judge_merges_equivalent_variants_but_keeps_related_separate():
    models = importlib.import_module("interview_intelligence.domain.models")
    dedup = importlib.import_module("interview_intelligence.dedup.service")
    db = models.create_database("sqlite+pysqlite:///:memory:")
    judge = SemanticJudge()
    service = dedup.DedupService(encoder=TwoDimensionalEncoder(), judge=judge, candidate_limit=10)
    with db.session() as session:
        with session.begin():
            first = service.resolve(session, "Redis为什么快？", "redis.performance", "PRINCIPLE")
            same = service.resolve(session, "为什么Redis性能这么高？", "redis.performance", "PRINCIPLE")
            related = service.resolve(session, "Redis为什么采用单线程？", "redis.performance", "PRINCIPLE")
        assert first.canonical.id == same.canonical.id
        assert related.canonical.id != first.canonical.id
        assert first.decision == "NEW"
        assert same.decision == "SAME"
        assert related.decision == "NEW"
        assert first.canonical.id in related.related_ids
        assert len(judge.calls) >= 2
        assert len(list(session.scalars(select(models.CanonicalQuestion)))) == 2


def test_identical_text_still_passes_semantic_judge():
    models = importlib.import_module("interview_intelligence.domain.models")
    dedup = importlib.import_module("interview_intelligence.dedup.service")
    db = models.create_database("sqlite+pysqlite:///:memory:")
    judge = SemanticJudge()
    service = dedup.DedupService(encoder=TwoDimensionalEncoder(), judge=judge)
    with db.session() as session:
        with session.begin():
            service.resolve(session, "Redis为什么快？", "redis.performance", "PRINCIPLE")
            service.resolve(session, "Redis为什么快？", "redis.performance", "PRINCIPLE")
    assert judge.calls == [("Redis为什么快？", "Redis为什么快？")]


class BatchJudge:
    version = "batch-judge-test"

    def __init__(self, incomplete=False):
        self.calls = []
        self.incomplete = incomplete

    def judge(self, incoming, candidate):
        raise AssertionError("batch-capable judge must not be called pair by pair")

    def judge_many(self, incoming, candidates):
        self.calls.append((incoming, candidates))
        if self.incomplete:
            return {}
        return {candidate_id: SimpleNamespace(
            decision="SAME" if "快" in text else "RELATED",
            reason_code="same_scope" if "快" in text else "different_scope", confidence=0.85,
        ) for candidate_id, text in candidates}


def test_dedup_batches_candidates_and_retains_per_candidate_evidence():
    models = importlib.import_module("interview_intelligence.domain.models")
    dedup = importlib.import_module("interview_intelligence.dedup.service")
    db = models.create_database("sqlite+pysqlite:///:memory:")
    judge = BatchJudge()
    service = dedup.DedupService(encoder=TwoDimensionalEncoder(), judge=judge)
    with db.session() as session, session.begin():
        one = models.CanonicalQuestion(canonical_text="Redis为什么快？", primary_topic_id="redis.performance",
                                       taxonomy_version="v1", question_type="PRINCIPLE")
        two = models.CanonicalQuestion(canonical_text="Redis为什么单线程？", primary_topic_id="redis.performance",
                                       taxonomy_version="v1", question_type="PRINCIPLE")
        session.add_all([one, two])
        session.flush()
        result = service.resolve(session, "为什么Redis性能高？", "redis.performance", "PRINCIPLE")
        assert len(judge.calls) == 1
        assert len(judge.calls[0][1]) == 2
        assert result.canonical.id == one.id
        assert result.related_ids == [two.id]
        assert result.confidence == 0.85
        assert len(result.evidence["judgements"]) == 2


def test_dedup_rejects_incomplete_batch_decisions():
    models = importlib.import_module("interview_intelligence.domain.models")
    dedup = importlib.import_module("interview_intelligence.dedup.service")
    db = models.create_database("sqlite+pysqlite:///:memory:")
    service = dedup.DedupService(encoder=TwoDimensionalEncoder(), judge=BatchJudge(incomplete=True))
    with db.session() as session, session.begin():
        session.add(models.CanonicalQuestion(canonical_text="Redis为什么快？", primary_topic_id="redis.performance",
                                             taxonomy_version="v1", question_type="PRINCIPLE"))
        session.flush()
        with pytest.raises(ValueError, match="candidate set"):
            service.resolve(session, "Redis为何快？", "redis.performance", "PRINCIPLE")
