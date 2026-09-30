"""Live counts from committed database facts, without estimated quality scores."""

from __future__ import annotations

from sqlalchemy import func, select

from interview_intelligence.domain.models import (
    CorpusState, ModelCall, PipelineRun, SourceDocument,
)
from interview_intelligence.repository.corpus import active_occurrences


def corpus_status(database) -> dict:
    with database.session() as session:
        state = session.get(CorpusState, 1)
        occurrences = active_occurrences(session)
        canonical_ids = {item.canonical_question_id for item in occurrences if item.canonical_question_id}
        interviews = {item.interview_id for item in occurrences}
        topics = {item.topic_id for item in occurrences}
        active_source_count = session.scalar(select(func.count(SourceDocument.id)).where(
            SourceDocument.active_build_id.is_not(None))) or 0
        calls = list(session.scalars(select(ModelCall)))
        runs = list(session.scalars(select(PipelineRun)))
        estimated_costs = [call.estimated_cost for call in calls if call.estimated_cost is not None]
        count = len(occurrences)
        return {
            "corpus_revision": state.current_revision,
            "indexed_revision": state.indexed_revision,
            "source_documents_with_active_build": active_source_count,
            "interview_sessions_with_questions": len(interviews),
            "active_occurrences": count,
            "active_canonical_questions": len(canonical_ids),
            "active_topics": len(topics),
            "dedup_ratio": 1 - len(canonical_ids) / count if count else None,
            "model_calls_recorded": len(calls),
            "input_tokens_recorded": sum(call.input_tokens or 0 for call in calls),
            "output_tokens_recorded": sum(call.output_tokens or 0 for call in calls),
            "estimated_cost_known_sum": sum(estimated_costs) if estimated_costs else None,
            "model_calls_with_unknown_usage": sum(call.input_tokens is None for call in calls),
            "failed_model_calls": sum(call.status == "FAILED" for call in calls),
            "model_retries": sum(call.retry_count for call in calls),
            "pipeline_runs": len(runs),
            "failed_pipeline_runs": sum(run.status in {"FAILED", "PARTIAL_FAILURE"} for run in runs),
            "quality_metrics_status": "UNVERIFIED_WITHOUT_FROZEN_GOLD",
        }
