"""SQL eligibility and exact facts around a replaceable retriever."""

from __future__ import annotations

import math

from sqlalchemy import select
from sqlalchemy.orm import Session

from interview_intelligence.analytics.detail import get_question_detail
from interview_intelligence.analytics.stats import _active_from, _conditions
from interview_intelligence.contracts import FilterSpec
from interview_intelligence.domain.models import QuestionOccurrence


def rrf(*rankings: list[tuple[str, float]], k: int = 60, weights: tuple[float, ...] | None = None) -> list[dict]:
    weights = tuple(1.0 for _ in rankings) if weights is None else weights
    if (len(weights) != len(rankings) or any(isinstance(weight, bool) or not isinstance(weight, (int, float))
            or not math.isfinite(weight) or weight < 0 for weight in weights)
            or rankings and not any(weight > 0 for weight in weights)):
        raise ValueError("invalid RRF weights")
    scores: dict[str, float] = {}
    stage_scores: dict[str, dict] = {}
    for index, ranking in enumerate(rankings):
        for rank, (canonical_id, score) in enumerate(ranking, 1):
            scores[canonical_id] = scores.get(canonical_id, 0.0) + weights[index] / (k + rank)
            stage_scores.setdefault(canonical_id, {})[f"stage_{index + 1}"] = score
    return [
        {"canonical_question_id": canonical_id, "rrf_score": score,
         "stage_scores": stage_scores[canonical_id]}
        for canonical_id, score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    ]


def eligible_canonical_ids(session: Session, filters: FilterSpec) -> list[str]:
    ids = list(session.scalars(
        select(QuestionOccurrence.canonical_question_id)
        .select_from(_active_from()).where(*_conditions(filters)).distinct()
    ))
    if len(ids) > 50_000:
        raise ValueError("FILTER_CAPACITY_EXCEEDED")
    return sorted(canonical_id for canonical_id in ids if canonical_id)


def search_questions(
    session: Session, retriever, query: str, filters: FilterSpec,
    *, pipeline: str = "HYBRID", top_k: int = 10, relevance_query: str | None = None,
    lexical_facets: list[str] | None = None, preferred_question_type: str | None = None, on_progress=None,
) -> dict:
    if not 1 <= len(query) <= 500 or not 1 <= top_k <= 50:
        raise ValueError("invalid search query or top_k")
    if on_progress:
        on_progress({"stage":"eligibility"})
    eligible = eligible_canonical_ids(session, filters)
    if not eligible:
        return {"data": [], "meta": {"pipeline": pipeline, "eligible_count": 0}}
    options = {}
    if on_progress:
        if getattr(retriever, "supports_progress", False):
            options["on_progress"] = on_progress
        else:
            on_progress({"stage":"retrieving"})
    if getattr(retriever, "supports_relevance_query", False):
        options["relevance_query"] = relevance_query
    if lexical_facets and getattr(retriever, "supports_lexical_facets", False):
        options["lexical_facets"] = lexical_facets
    if pipeline == "HYBRID_RERANK" and getattr(retriever, "supports_candidate_context", False):
        from interview_intelligence.search.evidence import candidate_source_context
        options["candidate_context_loader"] = lambda ids: candidate_source_context(session, ids, filters)
    if (preferred_question_type is not None and filters.question_type is None
            and pipeline in {"HYBRID", "HYBRID_RERANK"}
            and getattr(retriever, "supports_question_type_preference", False)):
        kind = FilterSpec(question_type=preferred_question_type).question_type
        preferred = eligible_canonical_ids(session, filters.model_copy(update={"question_type": kind}))
        options.update(preferred_question_type=kind.value,
                       preferred_eligible_ids=sorted(set(preferred).intersection(eligible)))
    retrieval = retriever.retrieve(query, eligible, pipeline, top_k, **options)
    if on_progress:
        on_progress({"stage":"assembling", "count":len(retrieval["data"])})
    data = []
    for match in retrieval["data"]:
        canonical_id = match["canonical_question_id"]
        if canonical_id not in eligible:
            raise ValueError("retriever returned an ineligible canonical ID")
        detail = get_question_detail(session, canonical_id, filters)
        data.append({
            "canonical_question_id": canonical_id,
            "canonical_text": detail["canonical_text"],
            "topic_id": detail["topic_id"],
            "question_type": detail["question_type"],
            "variants": detail["variants"],
            "occurrence_count": detail["occurrence_count"],
            "sources": detail["sources"][:3],
            "retrieval": match,
        })
    return {"data": data, "meta": {**retrieval.get("meta", {}), "eligible_count": len(eligible)}}
