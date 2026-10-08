import pytest
from sqlalchemy import func, select

from evals.project_quality.benchmark_dedup_chain import apply_source_pair_shadow
from interview_intelligence.domain.models import CanonicalQuestion, EmbeddingCache, create_database


@pytest.mark.parametrize("same_canonical", [False, True])
def test_source_pair_shadow_resolves_occurrence_identity_and_rolls_back(same_canonical):
    database = create_database("sqlite:///:memory:")
    with database.session() as session:
        session.add(CanonicalQuestion(id="left", canonical_text="incoming representative",
            primary_topic_id="other.open_question", taxonomy_version="v1", question_type="OTHER"))
        if not same_canonical:
            session.add(CanonicalQuestion(id="right", canonical_text="target representative",
                primary_topic_id="other.open_question", taxonomy_version="v1", question_type="OTHER"))
        session.commit()
    target_id = "left" if same_canonical else "right"
    case = {"left": {"id": "occurrence-left", "text": "incoming wording"},
        "right": {"id": "occurrence-right", "text": "reviewed target wording"},
        "left_canonical": {"id": "left"}, "right_canonical": {"id": target_id}}
    with database.session() as session:
        apply_source_pair_shadow(session, case, "test-encoder", [1.0, 0.0])
        assert session.get(CanonicalQuestion, target_id).canonical_text == "reviewed target wording"
        assert session.get(CanonicalQuestion, target_id).lifecycle == "ACTIVE"
        assert session.get(CanonicalQuestion, "left").lifecycle == ("ACTIVE" if same_canonical else "INACTIVE")
        assert session.scalar(select(func.count()).select_from(EmbeddingCache)) == 1
        session.rollback()
    with database.session() as session:
        assert session.get(CanonicalQuestion, "left").canonical_text == "incoming representative"
        assert session.get(CanonicalQuestion, "left").lifecycle == "ACTIVE"
        if not same_canonical:
            assert session.get(CanonicalQuestion, "right").canonical_text == "target representative"
        assert session.scalar(select(func.count()).select_from(EmbeddingCache)) == 0
