import hashlib
from sqlalchemy import select, func

from interview_intelligence.contracts import FilterSpec
from interview_intelligence.domain.models import (
    QuestionOccurrence, QuestionRelation, Interview, DocumentBuild, SourceRevision,
)
from interview_intelligence.search.evidence import candidate_source_context
from interview_intelligence.search.referents import validated_reference_view, validate_reference_claim
from test_analytics import seed_corpus


def seed_referential_source(tmp_path):
    db, target_id, other_id = seed_corpus()
    questions = ["设计预约系统，用户完成预约和取消。", "数据库压力如何处理？", "你提到使用缓存，具体准备如何组合？", "设计另一个无关系统。"]
    text = "\n".join(questions)
    digest = hashlib.sha256(text.encode()).hexdigest()
    (tmp_path / f"{digest}.md").write_bytes(text.encode())
    with db.session() as session, session.begin():
        current = session.scalar(select(QuestionOccurrence).where(
            QuestionOccurrence.canonical_question_id == target_id).order_by(QuestionOccurrence.id))
        interview = session.get(Interview, current.interview_id)
        revision = session.get(SourceRevision, session.get(DocumentBuild, interview.build_id).source_revision_id)
        revision.raw_file_hash = digest
        current.question_order = 3
        session.flush()
        offset, ids = 0, []
        for index, raw in enumerate(questions):
            if index == 2:
                occurrence = current
                occurrence.raw_question = raw
                occurrence.question_order = index + 1
            else:
                occurrence = QuestionOccurrence(interview_id=interview.id, canonical_question_id=other_id,
                    raw_question=raw, normalized_question=raw, question_order=index + 1,
                    topic_id="redis.persistence", taxonomy_version="v1", question_type="KNOWLEDGE", confidence=1)
                session.add(occurrence)
            occurrence.source_spans = [{"revision_id": revision.id, "start_char": offset,
                "end_char": offset + len(raw), "quote": raw}]
            offset += len(raw) + 1
            session.flush()
            ids.append(occurrence.id)
        filters = FilterSpec(company=interview.company_normalized, round=interview.round,
            question_type=current.question_type)
    return db, target_id, questions, ids, filters


def test_live_loader_uses_full_active_interview_not_filtered_preceding_topic(tmp_path):
    db, cid, questions, ids, filters = seed_referential_source(tmp_path)
    with db.session() as session:
        result = candidate_source_context(session, [cid], filters, snapshot_root=tmp_path,
            occurrence_ids=[ids[2]], include_coverage=True)[cid]
        assert session.scalar(select(func.count()).select_from(QuestionRelation)) == 0
    candidate = {"canonical_question_id": cid, **result}
    view = validated_reference_view(candidate)
    assert [row["occurrence_id"] for row in view["slots"].values()] == [ids[1], ids[0]]
    assert [row["original_question"] for row in view["slots"].values()] == [questions[1], questions[0]]
    assert result["source_context"][0]["original_question"] == questions[2]
    assert result["source_context_coverage"]["qualified_occurrence_ids"] == [ids[2]]
    assert validate_reference_claim(candidate, {"a": "s0o0", "r": ["s0r0", "s0r1"], "u": "UNIQUE"})["valid"]
    # A source relationship is not fabricated for physical adjacency.
    assert all(row["relation_provenance"] == "CANDIDATE_REFERENCE" for row in view["slots"].values())


def test_inactive_or_invalid_nearest_source_cannot_be_skipped_for_farther_business(tmp_path):
    db, cid, _, ids, filters = seed_referential_source(tmp_path)
    with db.session() as session, session.begin():
        nearest = session.get(QuestionOccurrence, ids[1])
        # JSON columns require a fresh value before mutation.
        nearest.source_spans = [{**nearest.source_spans[0], "quote": "wrong"}]
    with db.session() as session:
        result = candidate_source_context(session, [cid], filters, snapshot_root=tmp_path,
            occurrence_ids=[ids[2]])[cid]
    frame = result["source_context"][0]["reference_frame"]
    assert frame["status"] == "UNRESOLVED" and frame["antecedents"] == []
    assert frame["reason"] == "INVALID_PRECEDING_SOURCE"


def test_two_current_occurrences_share_one_500byte_envelope_including_references(tmp_path):
    db, cid, _, ids, filters = seed_referential_source(tmp_path)
    with db.session() as session:
        result = candidate_source_context(session, [cid], FilterSpec(), snapshot_root=tmp_path)[cid]
    total = 0
    for source in result["source_context"]:
        total += sum(len(source[key].encode()) for key in ("original_question", "context_before", "context_after"))
        total += source.get("reference_frame", {}).get("frame_text_bytes", 0)
    assert len(result["source_context"]) == 2 and total <= 500
