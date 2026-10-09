import hashlib

from sqlalchemy import select

from interview_intelligence.contracts import FilterSpec
from interview_intelligence.domain.models import QuestionOccurrence, SourceRevision
from interview_intelligence.search.evidence import candidate_source_context
from interview_intelligence.search.service import search_questions
from test_analytics import seed_corpus


def test_context_preserves_inline_project_prefix_and_same_occurrence_scope(tmp_path):
    db, question_id, _ = seed_corpus()
    original = "权限系统介绍，这里我没有用框架，手搓了一个，让讲设计过程。"
    raw = "让讲设计过程。"
    text = "# 项目经历\n" + original + "\n下一道：设计一个消息队列。\n"
    digest = hashlib.sha256(text.encode()).hexdigest()
    (tmp_path / f"{digest}.md").write_bytes(text.encode())
    with db.session() as session, session.begin():
        occurrence = session.scalar(select(QuestionOccurrence).where(
            QuestionOccurrence.canonical_question_id == question_id))
        # Bind only the occurrence selected by the current company/round.
        from interview_intelligence.domain.models import Interview, DocumentBuild
        interview = session.get(Interview, occurrence.interview_id)
        company, round_name = interview.company_normalized, interview.round
        revision = session.get(SourceRevision, session.get(DocumentBuild, interview.build_id).source_revision_id)
        revision.raw_file_hash = digest
        start = text.index(raw)
        occurrence.raw_question = raw
        occurrence.source_spans = [{"start_char": start, "end_char": start + len(raw), "quote": raw}]
        occurrence_id = occurrence.id
    with db.session() as session:
        context = candidate_source_context(session, [question_id], FilterSpec(company=company, round=round_name), snapshot_root=tmp_path)
    item = context[question_id]["source_context"][0]
    assert item["occurrence_id"] == occurrence_id
    assert item["source_context_status"] == "VERIFIED"
    assert "手搓了一个" in item["context_before"]
    assert item["original_question"] == raw
    assert len(context[question_id]["source_context"]) == 1


def test_unverified_source_cannot_supply_neighboring_context(tmp_path):
    db, question_id, _ = seed_corpus()
    with db.session() as session:
        revision = session.scalar(select(SourceRevision))
        # Even an existing file must match its immutable hash.
        (tmp_path / f"{revision.raw_file_hash}.md").write_text("伪造的项目上下文", encoding="utf-8")
        context = candidate_source_context(session, [question_id], FilterSpec(), snapshot_root=tmp_path)
    assert all(item["source_context_status"] == "UNAVAILABLE" and not item["context_before"]
               for item in context[question_id]["source_context"])


def test_source_context_is_bounded_and_does_not_change_sql_eligibility(tmp_path, monkeypatch):
    db, question_id, other = seed_corpus()
    with db.session() as session, session.begin():
        for occurrence in session.scalars(select(QuestionOccurrence)):
            occurrence.raw_question = "长原文" * 1000
    class Retriever:
        supports_candidate_context = True
        def retrieve(self, query, eligible, pipeline, top_k, *, candidate_context_loader):
            assert set(eligible) == {question_id, other}
            context = candidate_context_loader([question_id])
            assert set(context) == {question_id}
            items = context[question_id]["source_context"]
            assert sum(len(item[key].encode()) for item in items for key in
                       ("original_question", "context_before", "context_after")) <= 500
            return {"data": [{"canonical_question_id": question_id}], "meta": {}}
    from interview_intelligence.config import Settings
    monkeypatch.setattr("interview_intelligence.config.load_settings", lambda: Settings(snapshot_root=tmp_path))
    with db.session() as session:
        assert search_questions(session, Retriever(), "工程系统设计", FilterSpec(), pipeline="HYBRID_RERANK")["data"]
