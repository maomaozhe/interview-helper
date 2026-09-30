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


def _source_refs(occurrence, interview, document, revision) -> list[dict]:
    return [{
        "occurrence_id": occurrence.id,
        "interview_id": interview.id,
        "source_document_id": document.id,
        "revision_id": revision.id,
        "raw_file_hash": revision.raw_file_hash,
        "source_url": document.source_url,
        "start_line": span.get("start_line", 1),
        "end_line": span.get("end_line", span.get("start_line", 1)),
        "quote": span["quote"],
        "source_api_url": f"/api/sources/{revision.id}",
    } for span in occurrence.source_spans]


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
        items.append({
            "canonical_question_id": occurrence.canonical_question_id,
            "raw_question": occurrence.raw_question,
            "company": interview.company_normalized,
            "round": interview.round,
            "interview_date": interview.interview_date.isoformat() if interview.interview_date else None,
            "sources": _source_refs(occurrence, interview, document, revision),
            **_source_refs(occurrence, interview, document, revision)[0],
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
    refs = [ref for row in rows for ref in _source_refs(*row)]
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
        "corpus_revision": session.get(CorpusState, 1).current_revision,
    }
