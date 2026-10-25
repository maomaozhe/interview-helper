"""Audited engineering occurrences form a complete SQL list beyond Top50."""
from copy import deepcopy
from datetime import date
import hashlib

import pytest

from interview_intelligence.analytics.counts import count_questions
from interview_intelligence.analytics.listing import ListRequest, StatsListRequest, list_questions
from interview_intelligence.analytics.semantic_scope import (
    ENGINEERING_DESIGN_TASK as TASK, ENGINEERING_DESIGN_VERSION as VERSION,
    VERSION as ALGORITHM_VERSION, SemanticScopeIncomplete, load_semantic_scope,
    semantic_input_hash,
)
from interview_intelligence.contracts import FilterSpec
from interview_intelligence.domain.models import (
    CanonicalQuestion, CorpusState, DocumentBuild, Interview, OccurrenceTaskAnnotation,
    QuestionOccurrence, SourceDocument, SourceRevision, UserQuestionState,
    create_database,
)


def engineering_corpus(*, extra=0):
    db = create_database("sqlite+pysqlite:///:memory:")
    items = [
        ("cache", "字节", "FIRST", "REJECT", "解释缓存淘汰原理", "ALGORITHM"),
        ("cache", "字节", "SECOND", "ACCEPT", "设计一个支持高并发读写的缓存服务", "UNKNOWN"),
        ("cache", "字节", "SECOND", "ACCEPT", "设计可扩展的分布式缓存服务", "NONE"),
        ("orders", "字节", "FIRST", "ACCEPT", "设计一个订单系统", "UNKNOWN"),
        ("agent", "字节", "FIRST", "ACCEPT", "设计客服 Agent 应用的工作流和状态管理", "NONE"),
        ("project", "字节", "FIRST", "REJECT", "介绍以前项目使用的技术栈", "ENGINEERING"),
        ("other", "腾讯", "SECOND", None, "设计一个消息系统", "ENGINEERING"),
    ] + [(f"extra-{i:03}", "字节", "FIRST", "ACCEPT",
          f"设计第 {i} 个工程服务的扩展方案", "UNKNOWN") for i in range(extra)]
    with db.session() as session, session.begin():
        seen = set()
        for index, (cid, company, round_name, decision, raw, focus) in enumerate(items):
            if cid not in seen:
                session.add(CanonicalQuestion(id=cid, canonical_text=raw,
                    primary_topic_id="fundamentals.data_structures", taxonomy_version="v1",
                    question_type="KNOWLEDGE"))
                seen.add(cid)
                session.flush()
            source = SourceDocument(id=f"s-{index}", source_identity=f"engineering:{index}",
                source_type="file", original_relative_path=f"engineering-{index}.md")
            session.add(source)
            session.flush()
            digest = hashlib.sha256(raw.encode()).hexdigest()
            revision = SourceRevision(id=f"r-{index}", source_document_id=source.id,
                raw_file_hash=digest, decoded_text_hash=digest,
                snapshot_path=f"{digest}.md", raw_file_path=f"engineering-{index}.md")
            session.add(revision)
            session.flush()
            build = DocumentBuild(id=f"b-{index}", source_revision_id=revision.id,
                processing_fingerprint="v1", document_kind="INTERVIEW_REPORT",
                processing_state="READY", publication_state="ACTIVE")
            session.add(build)
            session.flush()
            source.active_build_id = build.id
            interview = Interview(id=f"i-{index}", build_id=build.id, session_key="round",
                session_order=1, session_kind="SINGLE", analytics_eligible=True,
                company_normalized=company, round=round_name, interview_date=date(2026, 8, 1))
            session.add(interview)
            session.flush()
            occurrence = QuestionOccurrence(id=f"o-{index:03}", interview_id=interview.id,
                canonical_question_id=cid, raw_question=raw, normalized_question=raw,
                question_order=1, source_spans=[{"revision_id": revision.id, "quote": raw,
                    "start_char": 0, "end_char": len(raw)}],
                topic_id="fundamentals.data_structures", taxonomy_version="v1",
                question_type="KNOWLEDGE", confidence=.9)
            session.add(occurrence)
            session.flush()
            scopes = {"ALGORITHM_PROBLEM": {"version": ALGORITHM_VERSION,
                "decision": "REJECT", "input_hash": semantic_input_hash(occurrence, revision),
                "quote": raw, "reason": "此原问不是算法求解任务"}}
            if decision is not None:
                scopes[TASK] = {"version": VERSION, "decision": decision,
                    "input_hash": semantic_input_hash(occurrence, revision), "quote": raw,
                    "reason": "当前原问要求工程建设交付" if decision == "ACCEPT" else "原问是原理或项目复述"}
            session.add(OccurrenceTaskAnnotation(occurrence_id=occurrence.id,
                response_form="UNKNOWN", coding_focus=focus,
                classification_status="NEEDS_REVIEW", producer_version="fixture",
                evidence={"semantic_scopes": scopes, "legacy_evidence": "keep"}))
    return db


def listing(db, **changes):
    with db.session() as session:
        return list_questions(session, ListRequest(company="字节", semantic_task=TASK, **changes),
            signing_key="engineering-test", user_id="learner", as_of=date(2026, 10, 16))


def change_decision(db, change, identity="o-005"):
    with db.session() as session, session.begin():
        row = session.get(OccurrenceTaskAnnotation, identity)
        evidence = deepcopy(row.evidence)
        change(evidence["semantic_scopes"][TASK])
        row.evidence = evidence


def test_engineering_scope_counts_sources_and_keeps_algorithm_decisions_independent():
    db = engineering_corpus()
    result = listing(db)
    assert [(row["canonical_question_id"], row["occurrence_count"])
            for row in result["data"]] == [("cache", 2), ("agent", 1), ("orders", 1)]
    assert result["data"][0]["matched_question"] == "设计一个支持高并发读写的缓存服务"
    assert result["data"][0]["source_summary"]["revision_id"] == "r-1"
    assert result["meta"]["sample_counts"] == {"occurrences": 4, "selected_occurrences": 4}
    assert result["meta"]["semantic_scope"] == {
        "task": TASK, "version": VERSION, "scope_hash": result["meta"]["semantic_scope"]["scope_hash"],
        "occurrences": 6, "accepted": 4, "rejected": 2, "complete": True}
    assert result["meta"]["semantic_task"] == TASK
    assert all(row["semantic_task"] == TASK for row in result["data"])
    with db.session() as session:
        scope = load_semantic_scope(session, FilterSpec(company="字节"), task=TASK)
        counts = count_questions(session, FilterSpec(company="字节"),
            occurrence_ids=scope.accepted_occurrence_ids)
        assert counts == {"canonical_questions": 3, "occurrences": 4, "interviews": 4,
                          "source_documents": 4, "known_companies": 1}
        algorithm = load_semantic_scope(session, FilterSpec(company="字节"))
        assert algorithm.accepted_occurrence_ids == ()
        assert algorithm.version == ALGORITHM_VERSION
        annotation = session.get(OccurrenceTaskAnnotation, "o-001")
        assert annotation.coding_focus == "UNKNOWN"
        assert annotation.evidence["legacy_evidence"] == "keep"


def test_engineering_paginates_complete_sql_scope_beyond_fifty_without_duplicates():
    db = engineering_corpus(extra=61)
    cursor, identities, ranks = None, [], []
    while True:
        result = listing(db, page_size=13, cursor=cursor)
        assert result["meta"]["pagination"]["total"] == 64
        assert result["meta"]["sample_counts"]["occurrences"] == 65
        identities.extend(row["canonical_question_id"] for row in result["data"])
        ranks.extend(row["rank"] for row in result["data"])
        cursor = result["meta"]["pagination"]["next_cursor"]
        if not cursor:
            break
    assert len(identities) == len(set(identities)) == 64
    assert set(identities) == {"cache", "orders", "agent", *[f"extra-{i:03}" for i in range(61)]}
    assert ranks == list(range(1, 65))


def test_combined_filters_apply_to_the_same_occurrence_before_semantic_decisions():
    db = engineering_corpus()
    first = listing(db, round="FIRST")
    assert {row["canonical_question_id"] for row in first["data"]} == {"agent", "orders"}
    assert first["meta"]["semantic_scope"]["occurrences"] == 4
    second = listing(db, round="SECOND")
    assert [(row["canonical_question_id"], row["occurrence_count"])
            for row in second["data"]] == [("cache", 2)]
    # The unaudited Tencent interview must neither block nor join Byte's scope.
    assert second["meta"]["semantic_scope"]["occurrences"] == 2


def test_engineering_group_stats_and_review_count_use_only_accepted_occurrences():
    db = engineering_corpus()
    with db.session() as session, session.begin():
        session.add(UserQuestionState(user_id="learner", canonical_question_id="cache", status="MASTERED"))
    with db.session() as session:
        request = StatsListRequest(company="字节", semantic_task=TASK, group_by="round")
        result = list_questions(session, request, signing_key="engineering-test")
        assert {row["key"]: row["occurrence_count"] for row in result["data"]} == {"FIRST": 2, "SECOND": 2}
        scope = load_semantic_scope(session, FilterSpec(company="字节"), task=TASK)
        counts = count_questions(session, FilterSpec(company="字节"), user_id="learner",
            review_statuses=["MASTERED"], occurrence_ids=scope.accepted_occurrence_ids)
        assert counts["canonical_questions"] == 1
        assert counts["occurrences"] == 2


@pytest.mark.parametrize("change,category", [
    (lambda value: value.clear(), "stale"),
    (lambda value: value.update(version=ALGORITHM_VERSION), "stale"),
    (lambda value: value.update(input_hash="0" * 64), "stale"),
    (lambda value: value.update(decision="UNKNOWN"), "invalid"),
    (lambda value: value.update(quote="另一个题目的工程建设目标"), "invalid"),
    (lambda value: value.update(reason=""), "invalid"),
])
def test_stale_or_invalid_negative_remains_visible_pending_without_false_complete(change, category):
    db = engineering_corpus()
    change_decision(db, change)
    result = listing(db)
    scope = result["meta"]["semantic_scope"]
    assert scope[category] == 1 and scope["pending"] == 1 and scope["complete"] is False
    assert scope["task"] == TASK and scope["version"] == VERSION
    pending = next(row for row in result["data"] if row["canonical_question_id"] == "project")
    assert pending["semantic_verification"] == "PENDING"
    assert pending["occurrence_count"] == pending["confirmed_occurrence_count"] == 0
    assert pending["pending_occurrence_count"] == pending["selected_occurrence_count"] == 1
    assert pending["importance_score"] == pending["gap_score"] == 0
    assert pending["pending_sources"][0]["status"] == category.upper()
    assert pending["pending_sources"][0]["raw_question"] == "介绍以前项目使用的技术栈"
    assert pending["pending_sources"][0]["input_hash"]
    assert pending["pending_sources"][0]["source_summary"]["revision_id"] == "r-5"
    assert result["meta"]["pagination"]["result_total"] == 4
    assert result["meta"]["semantic_counts"]["confirmed_canonical_questions"] == 3
    assert result["meta"]["semantic_counts"]["pending_only_canonical_questions"] == 1


@pytest.mark.parametrize("delete_annotation", [False, True])
def test_algorithm_audit_or_legacy_engineering_label_cannot_fill_missing_engineering_decision(delete_annotation):
    db = engineering_corpus()
    with db.session() as session, session.begin():
        row = session.get(OccurrenceTaskAnnotation, "o-005")
        if delete_annotation:
            session.delete(row)
        else:
            evidence = deepcopy(row.evidence)
            evidence["semantic_scopes"].pop(TASK)
            row.evidence = evidence
    result = listing(db)
    assert result["meta"]["semantic_scope"]["missing"] == 1
    assert result["meta"]["semantic_scope"]["complete"] is False
    assert next(row for row in result["data"] if row["canonical_question_id"] == "project")["semantic_verification"] == "PENDING"


def test_new_matching_occurrence_needs_its_own_engineering_decision():
    db = engineering_corpus()
    with db.session() as session, session.begin():
        session.get(Interview, "i-6").company_normalized = "字节"
    result = listing(db)
    assert result["meta"]["semantic_scope"]["occurrences"] == 7
    assert result["meta"]["semantic_scope"]["missing"] == 1
    assert next(row for row in result["data"] if row["canonical_question_id"] == "other")["semantic_verification"] == "PENDING"


@pytest.mark.parametrize("excluded_by", ["active_build", "included_document", "eligible_interview"])
def test_unaudited_inactive_or_excluded_occurrence_does_not_block_current_facts(excluded_by):
    db = engineering_corpus()
    with db.session() as session, session.begin():
        session.get(Interview, "i-6").company_normalized = "字节"
        if excluded_by == "active_build":
            session.get(SourceDocument, "s-6").active_build_id = None
        elif excluded_by == "included_document":
            session.get(DocumentBuild, "b-6").decision = "EXCLUDED"
        else:
            session.get(Interview, "i-6").analytics_eligible = False
    assert listing(db)["meta"]["semantic_scope"]["occurrences"] == 6


@pytest.mark.parametrize("field", ["raw_question", "source_spans", "decoded_text_hash"])
def test_changed_source_binding_invalidates_engineering_decision(field):
    db = engineering_corpus()
    with db.session() as session, session.begin():
        row = session.get(SourceRevision, "r-1") if field == "decoded_text_hash" else session.get(QuestionOccurrence, "o-001")
        if field == "source_spans":
            row.source_spans = [{**row.source_spans[0], "end_char": 1}]
        elif field == "decoded_text_hash":
            row.decoded_text_hash = "f" * 64
        else:
            row.raw_question += "，并支持故障恢复"
    result = listing(db)
    assert result["meta"]["semantic_scope"]["stale"] == 1
    cache = next(row for row in result["data"] if row["canonical_question_id"] == "cache")
    assert cache["confirmed_occurrence_count"] == cache["pending_occurrence_count"] == 1
    assert cache["selected_occurrence_count"] == 2
    assert cache["matched_question"] == "设计可扩展的分布式缓存服务"
    assert cache["source_summary"]["revision_id"] == "r-2"
    assert cache["pending_sources"][0]["status"] == "STALE"
    if field == "raw_question":
        assert cache["pending_sources"][0]["raw_question"].endswith("并支持故障恢复")


def test_cursor_binds_negative_decisions_even_without_revision_change():
    db = engineering_corpus()
    cursor = listing(db, page_size=1)["meta"]["pagination"]["next_cursor"]
    change_decision(db, lambda value: value.update(reason="重新核对原文，仍为项目复述"))
    with pytest.raises(ValueError, match="SNAPSHOT_CHANGED"):
        listing(db, page_size=1, cursor=cursor)


def test_cursor_binds_task_and_annotation_revision():
    db = engineering_corpus()
    cursor = listing(db, page_size=1)["meta"]["pagination"]["next_cursor"]
    with db.session() as session, pytest.raises(ValueError, match="INVALID_CURSOR_SCOPE"):
        list_questions(session, ListRequest(company="字节", semantic_task="ALGORITHM_PROBLEM",
            page_size=1, cursor=cursor), signing_key="engineering-test", user_id="learner")
    with db.session() as session, session.begin():
        session.get(CorpusState, 1).task_annotation_revision += 1
    with pytest.raises(ValueError, match="SNAPSHOT_CHANGED"):
        listing(db, page_size=1, cursor=cursor)


def test_empty_or_all_rejected_scope_returns_exact_zero_without_next_page():
    db = engineering_corpus()
    for identity in ("o-001", "o-002", "o-003", "o-004"):
        change_decision(db, lambda value: value.update(decision="REJECT"), identity)
    result = listing(db)
    assert result["data"] == []
    assert result["meta"]["pagination"]["result_total"] == 0
    assert result["meta"]["pagination"]["next_cursor"] is None
    assert result["meta"]["semantic_scope"]["complete"] is True
    with db.session() as session:
        empty = load_semantic_scope(session, FilterSpec(company="百度"), task=TASK)
        assert empty.occurrences == 0
        assert empty.metadata()["complete"] is True


def test_explicit_legacy_task_filters_intersect_on_same_engineering_occurrence():
    db = engineering_corpus()
    with db.session() as session, session.begin():
        first = session.get(OccurrenceTaskAnnotation, "o-001")
        first.coding_focus, first.response_form, first.classification_status = "ENGINEERING", "CODE", "NEEDS_REVIEW"
        second = session.get(OccurrenceTaskAnnotation, "o-002")
        second.coding_focus, second.response_form, second.classification_status = "NONE", "CODE", "VERIFIED"
    # The canonical has both labels, but no one occurrence satisfies this pair.
    result = listing(db, coding_focus="ENGINEERING", annotation_status="VERIFIED")
    assert result["data"] == [] and result["meta"]["semantic_scope"]["occurrences"] == 0
    result = listing(db, coding_focus="ENGINEERING", annotation_status="KNOWN")
    assert [(row["canonical_question_id"], row["confirmed_occurrence_count"]) for row in result["data"]] == [("cache", 1)]
    result = listing(db, coding_focus="NONE", annotation_status="VERIFIED")
    assert [(row["canonical_question_id"], row["confirmed_occurrence_count"]) for row in result["data"]] == [("cache", 1)]


def test_unresolved_scope_has_separate_occurrence_and_canonical_counts():
    db = engineering_corpus()
    change_decision(db, lambda value: value.update(decision="UNRESOLVED", reason="原文未说明任务形态"), "o-001")
    change_decision(db, lambda value: value.update(decision="UNRESOLVED", reason="只记录题目标题"), "o-005")
    result = listing(db)
    assert result["meta"]["semantic_scope"]["unresolved"] == 2
    assert result["meta"]["semantic_counts"] == {
        "confirmed_canonical_questions": 3, "pending_only_canonical_questions": 1,
        "pending_canonical_questions": 2, "selected_canonical_questions": 4,
        "confirmed_occurrences": 3, "pending_occurrences": 2, "selected_occurrences": 5}
    cache = next(row for row in result["data"] if row["canonical_question_id"] == "cache")
    assert cache["occurrence_count"] == cache["pending_occurrence_count"] == 1
    assert cache["semantic_verification"] == "CONFIRMED"
    assert cache["pending_sources"][0]["reason"] == "原文未说明任务形态"
    with db.session() as session:
        scope = load_semantic_scope(session, FilterSpec(company="字节"), task=TASK)
        assert set(scope.pending_occurrence_ids) == {"o-001", "o-005"}
        assert len(scope.accepted_occurrence_ids) == 3 and len(scope.selected_occurrence_ids) == 5


def test_pending_only_questions_are_pageable_beyond_fifty_and_do_not_gain_frequency():
    db = engineering_corpus(extra=55)
    for index in range(7, 62):
        change_decision(db, lambda value: value.update(decision="UNRESOLVED", reason="具体要求尚待核对"), f"o-{index:03}")
    cursor, seen = None, []
    while True:
        result = listing(db, page_size=11, cursor=cursor)
        assert result["meta"]["pagination"]["result_total"] == 58
        for row in result["data"]:
            seen.append(row["canonical_question_id"])
            if row["canonical_question_id"].startswith("extra-"):
                assert row["occurrence_count"] == row["importance_score"] == 0
                assert row["pending_occurrence_count"] == 1 and row["pending_sources"]
        cursor = result["meta"]["pagination"]["next_cursor"]
        if not cursor:
            break
    assert len(seen) == len(set(seen)) == 58


@pytest.mark.parametrize("initial,change", [
    ({"decision": "UNRESOLVED", "reason": "任务未说明"}, {"reason": "源文仍缺需求"}),
    ({"quote": "不存在的原文引文"}, {"quote": "另一段错误引文"}),
    ({"input_hash": "0" * 64}, {"reason": "更新陈旧审核说明"}),
])
def test_pending_audit_changes_invalidate_cursor_without_revision_bump(initial, change):
    db = engineering_corpus()
    change_decision(db, lambda value: value.update(initial))
    cursor = listing(db, page_size=1)["meta"]["pagination"]["next_cursor"]
    change_decision(db, lambda value: value.update(change))
    with pytest.raises(ValueError, match="SNAPSHOT_CHANGED"):
        listing(db, page_size=1, cursor=cursor)


def test_engineering_pending_does_not_soften_algorithm_incomplete_gate():
    db = engineering_corpus()
    with db.session() as session, session.begin():
        row = session.get(OccurrenceTaskAnnotation, "o-005")
        evidence = deepcopy(row.evidence)
        evidence["semantic_scopes"]["ALGORITHM_PROBLEM"]["decision"] = "UNRESOLVED"
        row.evidence = evidence
    with db.session() as session, pytest.raises(SemanticScopeIncomplete) as failure:
        load_semantic_scope(session, FilterSpec(company="字节"))
    assert failure.value.coverage["invalid"] == 1


def test_group_stats_retain_pending_source_questions_without_counting_them_confirmed():
    db = engineering_corpus()
    change_decision(db, lambda value: value.update(decision="UNRESOLVED", reason="需补充原问"))
    with db.session() as session:
        result = list_questions(session, StatsListRequest(company="字节", semantic_task=TASK, group_by="round"),
            signing_key="engineering-test")
    first = next(row for row in result["data"] if row["key"] == "FIRST")
    assert first["confirmed_occurrence_count"] == 2
    assert first["pending_occurrence_count"] == 1
    assert first["pending_sources"][0]["occurrence_id"] == "o-005"


def test_semantic_counts_apply_personal_review_filter_without_mixing_occurrences():
    db = engineering_corpus()
    change_decision(db, lambda value: value.update(decision="UNRESOLVED", reason="原问未确定"), "o-001")
    change_decision(db, lambda value: value.update(decision="UNRESOLVED", reason="项目题仍需核对"), "o-005")
    with db.session() as session, session.begin():
        session.add(UserQuestionState(user_id="learner", canonical_question_id="cache", status="MASTERED"))
    result = listing(db, review_statuses=["MASTERED"])
    assert [row["canonical_question_id"] for row in result["data"]] == ["cache"]
    assert result["meta"]["semantic_counts"] == {
        "confirmed_canonical_questions": 1, "pending_only_canonical_questions": 0,
        "pending_canonical_questions": 1, "selected_canonical_questions": 1,
        "confirmed_occurrences": 1, "pending_occurrences": 1, "selected_occurrences": 2}


def test_invalid_current_source_binding_is_pending_and_cannot_reuse_old_rejection():
    db = engineering_corpus()
    with db.session() as session, session.begin():
        session.get(QuestionOccurrence, "o-005").source_spans = 1
    result = listing(db)
    pending = next(row for row in result["data"] if row["canonical_question_id"] == "project")
    assert pending["semantic_verification"] == "PENDING"
    assert pending["pending_sources"][0]["input_hash"] is None
    assert pending["pending_sources"][0]["status"] == "INVALID"
    assert pending["pending_sources"][0]["verification"] == "CURRENT_SQL_SOURCE_PENDING_SEMANTIC_REVIEW"
    assert result["meta"]["semantic_scope"]["complete"] is False
