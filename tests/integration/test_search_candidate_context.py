import hashlib

import pytest

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
    assert item["original_question_complete"] is True
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
            assert all(item["original_question_complete"] is False for item in items)
            return {"data": [{"canonical_question_id": question_id}], "meta": {}}
    from interview_intelligence.config import Settings
    monkeypatch.setattr("interview_intelligence.config.load_settings", lambda: Settings(snapshot_root=tmp_path))
    with db.session() as session:
        assert search_questions(session, Retriever(), "工程系统设计", FilterSpec(), pipeline="HYBRID_RERANK")["data"]


def test_verified_long_mixed_task_is_marked_incomplete_without_expanding_text_budget(tmp_path):
    db, question_id, _ = seed_corpus()
    raw = "介绍Redis的原理是什么？" + "这里还有更多原文条件。" * 40 + "如果发生缓存击穿，如何解决？"
    data = ("# 面试\n" + raw + "\n").encode()
    digest = hashlib.sha256(data).hexdigest()
    (tmp_path / f"{digest}.md").write_bytes(data)
    with db.session() as session, session.begin():
        from interview_intelligence.domain.models import Interview, DocumentBuild
        occurrence = session.scalar(select(QuestionOccurrence).where(
            QuestionOccurrence.canonical_question_id == question_id))
        interview = session.get(Interview, occurrence.interview_id)
        filters = FilterSpec(company=interview.company_normalized, round=interview.round)
        revision = session.get(SourceRevision, session.get(DocumentBuild, interview.build_id).source_revision_id)
        revision.raw_file_hash = digest
        occurrence.raw_question = raw
        start = data.decode().index(raw)
        occurrence.source_spans = [{"start_char": start, "end_char": start + len(raw), "quote": raw}]
    with db.session() as session:
        context = candidate_source_context(session, [question_id], filters, snapshot_root=tmp_path)
    assert context[question_id]["source_context_version"] == "candidate_source_context_v4"
    item = context[question_id]["source_context"][0]
    assert item["source_context_status"] == "VERIFIED"
    assert item["original_question_complete"] is False
    assert item["original_question"].startswith("介绍Redis的原理是什么？")
    assert "如何解决" not in item["original_question"]
    assert sum(len(item[key].encode()) for key in ("original_question", "context_before", "context_after")) <= 500


def _bind_source(session, occurrence, raw, snapshot_root, *, invalid_span=False):
    from interview_intelligence.domain.models import Interview, DocumentBuild
    interview = session.get(Interview, occurrence.interview_id)
    revision = session.get(SourceRevision, session.get(DocumentBuild, interview.build_id).source_revision_id)
    # This neighboring construction task must never be joined into raw_question.
    text = "上一题：设计支付系统。\n" + raw + "\n下一题：设计消息队列。\n"
    digest = hashlib.sha256(text.encode()).hexdigest()
    (snapshot_root / f"{digest}.md").write_bytes(text.encode())
    revision.raw_file_hash = digest
    start = text.index(raw)
    occurrence.raw_question = raw
    occurrence.source_spans = [{"start_char": start, "end_char": start + len(raw),
                                "quote": "错误引用" if invalid_span else raw}]
    return FilterSpec(company=interview.company_normalized, round=interview.round)


def test_current_question_task_tail_precedes_neighbor_context(tmp_path):
    db, question_id, _ = seed_corpus()
    raw = "不同租户各有独立的业务能力及权限需求。" * 6 + "如何设计按需隔离与交付方案？"
    assert 200 < len(raw.encode()) < 500
    with db.session() as session, session.begin():
        occurrence = session.scalar(select(QuestionOccurrence).where(
            QuestionOccurrence.canonical_question_id == question_id))
        filters = _bind_source(session, occurrence, raw, tmp_path)
        identity = occurrence.id
    with db.session() as session:
        result = candidate_source_context(session, [question_id], filters, snapshot_root=tmp_path)
    item = result[question_id]["source_context"][0]
    assert item["occurrence_id"] == identity
    assert item["source_context_status"] == "VERIFIED"
    assert item["original_question_complete"] is True
    assert item["original_question"] == raw
    assert "如何设计按需隔离与交付方案？" in item["original_question"]
    assert "上一题" not in item["original_question"] and "下一题" not in item["original_question"]
    assert sum(len(item[k].encode()) for k in ("original_question", "context_before", "context_after")) <= 500


def test_two_occurrences_donate_short_original_budget_without_joining_sources(tmp_path):
    db, question_id, _ = seed_corpus()
    originals = ["缓存的原理是什么？", "租户具有不同的权限和交付要求。" * 7 + "如何设计隔离方案？"]
    assert len(originals[1].encode()) > 250 and sum(len(s.encode()) for s in originals) < 500
    with db.session() as session, session.begin():
        rows = list(session.scalars(select(QuestionOccurrence).where(
            QuestionOccurrence.canonical_question_id == question_id).order_by(QuestionOccurrence.id)))
        identities = []
        for occurrence, raw in zip(rows, originals):
            _bind_source(session, occurrence, raw, tmp_path)
            identities.append(occurrence.id)
    with db.session() as session:
        items = candidate_source_context(session, [question_id], FilterSpec(), snapshot_root=tmp_path)[question_id]["source_context"]
    assert [i["occurrence_id"] for i in items] == identities
    assert [i["original_question"] for i in items] == originals
    assert all(i["original_question_complete"] and i["source_context_status"] == "VERIFIED" for i in items)
    assert sum(len(i[k].encode()) for i in items for k in ("original_question", "context_before", "context_after")) <= 500


@pytest.mark.parametrize("originals", [
    ["中" * 167], ["🙂" * 126], ["a" * 500],
    ["中" * 170, "x" * 9], ["🙂" * 150, "中" * 300],
    ["abc" * 100, "中" * 100], ["短题", "🙂" * 120],
])
def test_original_budget_preserves_utf8_prefix_and_truthful_completeness(originals):
    from interview_intelligence.search.evidence import _bound_occurrences
    items = [{"original_question": raw, "context_before": "前题建设任务" * 100,
              "context_after": "后题建设任务" * 100} for raw in originals]
    _bound_occurrences(items)
    assert sum(len(i[k].encode()) for i in items for k in ("original_question", "context_before", "context_after")) <= 500
    for raw, item in zip(originals, items):
        visible = item["original_question"]
        assert raw.startswith(visible)
        assert "�" not in visible
        assert item["original_question_complete"] == (visible == raw)
    if sum(len(raw.encode()) for raw in originals) <= 500:
        assert all(i["original_question_complete"] for i in items)
    else:
        assert any(not i["original_question_complete"] for i in items)


def test_invalid_span_never_supplies_neighbor_task(tmp_path):
    db, question_id, _ = seed_corpus()
    with db.session() as session, session.begin():
        occurrence = session.scalar(select(QuestionOccurrence).where(
            QuestionOccurrence.canonical_question_id == question_id))
        filters = _bind_source(session, occurrence, "缓存的原理是什么？", tmp_path, invalid_span=True)
    with db.session() as session:
        item = candidate_source_context(session, [question_id], filters, snapshot_root=tmp_path)[question_id]["source_context"][0]
    assert item["source_context_status"] == "INVALID_SPANS"
    assert item["context_before"] == item["context_after"] == ""
    assert item["original_question"] == "缓存的原理是什么？"


def test_context_selection_remains_same_occurrence_filtered_and_at_most_two(tmp_path):
    db, question_id, other_id = seed_corpus()
    with db.session() as session:
        # Both values exist for the canonical, but on different occurrences.
        assert candidate_source_context(session, [question_id], FilterSpec(company="字节", round="SECOND"), snapshot_root=tmp_path) == {}
    with db.session() as session, session.begin():
        other = session.scalar(select(QuestionOccurrence).where(QuestionOccurrence.canonical_question_id == other_id))
        other.canonical_question_id = question_id
        session.flush()
        all_ids = list(session.scalars(select(QuestionOccurrence.id).where(
            QuestionOccurrence.canonical_question_id == question_id).order_by(QuestionOccurrence.id)))
        assert len(all_ids) == 3
    with db.session() as session:
        items = candidate_source_context(session, [question_id], FilterSpec(), snapshot_root=tmp_path)[question_id]["source_context"]
    assert [item["occurrence_id"] for item in items] == all_ids[:2]


@pytest.mark.parametrize("count", [1, 2])
def test_short_originals_do_not_expand_neighbor_question_budget(count):
    from interview_intelligence.search.evidence import _bound_occurrences
    items = [{"original_question": "原理是什么？", "context_before": "a" * 1000,
              "context_after": "b" * 1000} for _ in range(count)]
    _bound_occurrences(items)
    assert all(i["original_question_complete"] for i in items)
    assert all(len(i["context_before"].encode()) <= (500 // count) // 2 for i in items)
    assert all(len(i["context_after"].encode()) <= (500 // count) // 10 for i in items)


def test_unused_after_budget_keeps_available_inline_prefix():
    from interview_intelligence.search.evidence import _bound_occurrences
    item = {"original_question": "q" * 300, "context_before": "p" * 190, "context_after": ""}
    _bound_occurrences([item])
    assert item["original_question_complete"] is True
    assert item["context_before"] == "p" * 190
    assert item["context_after"] == ""
