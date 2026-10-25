"""Question facts and immutable source references within one filtered scope."""

from __future__ import annotations

from collections import Counter

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from interview_intelligence.analytics.stats import _active_from, _conditions
from interview_intelligence.contracts import FilterSpec
from interview_intelligence.domain.models import (
    AlgorithmMatch, CanonicalQuestion, CorpusState, Interview, QuestionOccurrence,
    QuestionRelation, SourceDocument, SourceRevision,
)
from interview_intelligence.taxonomy import load_taxonomy


def _matching_rows(session: Session, filters: FilterSpec, canonical_id: str | None = None):
    conditions = _conditions(filters)
    if canonical_id is not None:
        conditions.append(QuestionOccurrence.canonical_question_id == canonical_id)
    return list(session.execute(
        select(QuestionOccurrence, Interview, SourceDocument, SourceRevision)
        .select_from(_active_from()).where(*conditions)
        .order_by(Interview.interview_date.desc().nullslast(), QuestionOccurrence.id)
    ))


def _source_state(occurrence, interview, document, revision):
    """Separate usable citation shapes from current SQL facts with bad spans.

    This does not verify snapshot bytes or semantic membership. In particular,
    a malformed span must never become a line-1 citation to keep a UI working.
    """
    spans = occurrence.source_spans
    refs, issues = [], []

    def issue(reason, *, index=None, missing=False):
        issues.append({"occurrence_id": occurrence.id, "interview_id": interview.id,
            "source_document_id": document.id, "revision_id": revision.id,
            "raw_question": occurrence.raw_question, "source_path": document.original_relative_path,
            "status": "MISSING_SOURCE_SPANS" if missing else "INVALID_SOURCE_SPANS",
            "reason": reason, "source_evidence_verified": False,
            **({"span_index": index} if index is not None else {})})

    if not isinstance(spans, list) or not spans:
        issue("原文引用位置缺失；保留当前原问，不能声称已核验来源位置。" if spans is None or spans == []
            else "原文引用位置结构无效；保留当前原问，不能将其作为已核验原文引用。",
            missing=spans is None or spans == [])
        return refs, issues
    for index, span in enumerate(spans):
        if not isinstance(span, dict) or not isinstance(span.get("quote"), str) or not span["quote"].strip():
            issue("该引用缺少有效的连续原文文本；未生成来源定位按钮。", index=index)
            continue
        start_line, end_line = span.get("start_line", 1), span.get("end_line", span.get("start_line", 1))
        if (type(start_line) is not int or type(end_line) is not int
                or start_line < 1 or end_line < start_line):
            issue("该引用的原文行号无效；未生成来源定位按钮。", index=index)
            continue
        if "start_char" in span or "end_char" in span:
            start, end = span.get("start_char"), span.get("end_char")
            if (type(start) is not int or type(end) is not int or start < 0
                    or end <= start or end - start != len(span["quote"])):
                issue("该引用的字符位置与原文文本不一致；未生成来源定位按钮。", index=index)
                continue
        if span.get("revision_id", revision.id) != revision.id:
            issue("该引用绑定了其他来源版本；未生成来源定位按钮。", index=index)
            continue
        refs.append({
        "occurrence_id": occurrence.id,
        "interview_id": interview.id,
        "source_document_id": document.id,
        "revision_id": revision.id,
        "raw_file_hash": revision.raw_file_hash,
        "source_url": document.source_url,
        "start_line": start_line,
        "end_line": end_line,
        "quote": span["quote"],
        "source_api_url": f"/api/sources/{revision.id}",
        })
    return refs, issues


def _source_refs(occurrence, interview, document, revision) -> list[dict]:
    return _source_state(occurrence, interview, document, revision)[0]


def list_occurrences(
    session: Session, filters: FilterSpec, *, canonical_id: str | None = None,
    offset: int = 0, limit: int = 20, group_by: str | None = None,
    group_key: str | None = None, topic_level: str = "L2",
) -> dict:
    rows = _matching_rows(session, filters, canonical_id)
    if group_by and group_key:
        if group_by == "question":
            rows = [row for row in rows if row[0].canonical_question_id == group_key]
        elif group_by == "company":
            rows = [row for row in rows if (row[1].company_normalized or "UNKNOWN") == group_key]
        elif group_by == "round":
            rows = [row for row in rows if (row[1].round or "UNKNOWN") == group_key]
        elif group_by == "topic":
            if topic_level == "L2":
                rows = [row for row in rows if row[0].topic_id == group_key]
            else:
                topic_ids = set(load_taxonomy().topics[group_key].values())
                rows = [row for row in rows if row[0].topic_id in topic_ids]
        else:
            raise ValueError("invalid occurrence scope")
    items = []
    for occurrence, interview, document, revision in rows[offset:offset + limit]:
        refs, issues = _source_state(occurrence, interview, document, revision)
        items.append({
            "canonical_question_id": occurrence.canonical_question_id,
            "raw_question": occurrence.raw_question,
            "company": interview.company_normalized,
            "round": interview.round,
            "interview_date": interview.interview_date.isoformat() if interview.interview_date else None,
            "sources": refs,
            **(refs[0] if refs else {"occurrence_id": occurrence.id, "interview_id": interview.id,
                "source_document_id": document.id, "revision_id": revision.id}),
            **({"source_issues": issues, "source_evidence_verified": False} if issues else {}),
        })
    return {"data": items, "total": len(rows), "next_offset": offset + limit if offset + limit < len(rows) else None}


def get_question_detail(session: Session, canonical_id: str, filters: FilterSpec) -> dict:
    canonical = session.get(CanonicalQuestion, canonical_id)
    if canonical is None:
        raise KeyError("QUESTION_NOT_FOUND")
    if canonical.lifecycle == "REDIRECT" and canonical.redirect_to_id:
        return {"canonical_question_id": canonical.id, "redirect_to_id": canonical.redirect_to_id}
    rows = _matching_rows(session, filters, canonical_id)
    occurrence_ids = [occurrence.id for occurrence, *_ in rows]
    company_counts = Counter(interview.company_normalized or "UNKNOWN" for _, interview, *_ in rows)
    round_counts = Counter(interview.round or "UNKNOWN" for _, interview, *_ in rows)
    followups = []
    if occurrence_ids:
        relations = session.execute(
            select(QuestionRelation, QuestionOccurrence)
            .join(QuestionOccurrence, QuestionRelation.target_occurrence_id == QuestionOccurrence.id)
            .where(QuestionRelation.relation_type == "OBSERVED_FOLLOWUP",
                   QuestionRelation.source_occurrence_id.in_(occurrence_ids))
        )
        counts: dict[str, dict] = {}
        for relation, target in relations:
            key = target.canonical_question_id
            item = counts.setdefault(key, {
                "canonical_question_id": key, "canonical_text": target.normalized_question,
                "observed_edge_count": 0, "supporting_interview_ids": set(),
            })
            item["observed_edge_count"] += 1
            item["supporting_interview_ids"].add(relation.source_interview_id)
        for item in counts.values():
            item["supporting_interview_count"] = len(item.pop("supporting_interview_ids"))
            followups.append(item)
        followups.sort(key=lambda item: (-item["supporting_interview_count"], item["canonical_question_id"]))
    algorithms = []
    if occurrence_ids:
        algorithms = [{"occurrence_id": match.occurrence_id, "platform": match.platform,
                       "problem_id": match.problem_id, "title": match.title,
                       "match_status": match.match_status, "confidence": match.confidence}
                      for match in session.scalars(select(AlgorithmMatch).where(
                          AlgorithmMatch.occurrence_id.in_(occurrence_ids)))]
    inferred = [{"relation_type": relation.relation_type,
                 "target_canonical_id": relation.target_canonical_id, "confidence": relation.confidence}
                for relation in session.scalars(select(QuestionRelation).where(
                    QuestionRelation.source_canonical_id == canonical.id,
                    QuestionRelation.relation_type.in_(["RELATED", "SIMILAR"]),
                ))]
    source_states = [_source_state(*row) for row in rows]
    refs = [ref for row_refs, _ in source_states for ref in row_refs]
    source_issues = [issue for _, row_issues in source_states for issue in row_issues]
    return {
        "canonical_question_id": canonical.id,
        "canonical_text": canonical.canonical_text,
        "topic_id": canonical.primary_topic_id,
        "question_type": canonical.question_type,
        "variants": sorted({occurrence.raw_question for occurrence, *_ in rows}),
        "occurrence_count": len(rows),
        "interview_count": len({interview.id for _, interview, *_ in rows}),
        "source_document_count": len({document.id for _, _, document, _ in rows}),
        "company_distribution": dict(sorted(company_counts.items())),
        "round_distribution": dict(sorted(round_counts.items())),
        "observed_followups": followups,
        "inferred_relations": inferred,
        "algorithm_matches": algorithms,
        "sources": refs[:20],
        "sources_total": len(refs),
        **({"source_issues": source_issues, "source_evidence_verified": False} if source_issues else {}),
        "corpus_revision": session.get(CorpusState, 1).current_revision,
    }
