"""Bounded, resumable task-label backfill without re-extraction or re-deduplication."""
from __future__ import annotations

import hashlib
import json
import time
from typing import Literal
from uuid import uuid4

import httpx
from pydantic import Field, ValidationError
from sqlalchemy import select

from interview_intelligence.analytics.stats import _active_from, _conditions
from interview_intelligence.contracts import FilterSpec, StrictModel
from interview_intelligence.domain.models import CorpusState, ModelCall, OccurrenceTaskAnnotation, QuestionOccurrence
from interview_intelligence.providers.budget import CallBudget
from interview_intelligence.providers.gate import ModelCallGate
from interview_intelligence.resources import resource_path


class TaskLabel(StrictModel):
    occurrence_id: str
    response_form: Literal["VERBAL", "CODE", "SQL", "UNKNOWN"]
    coding_focus: Literal["ALGORITHM", "ENGINEERING", "MIXED", "NONE", "UNKNOWN"]
    confidence: float = Field(ge=0, le=1)
    evidence_quote: str


class TaskLabels(StrictModel):
    items: list[TaskLabel]


class CompactTaskItem(StrictModel):
    i: int = Field(ge=0, le=99)
    f: Literal["VERBAL", "CODE", "SQL", "UNKNOWN"]
    c: Literal["ALGORITHM", "ENGINEERING", "MIXED", "NONE", "UNKNOWN"]
    p: float = Field(ge=0, le=1)


class CompactTaskLabels(StrictModel):
    items: list[CompactTaskItem] = Field(max_length=100)


def validate_labels(labels, batch):
    original = {row.id: row for row in batch}
    if len(labels.items) != len(batch) or {i.occurrence_id for i in labels.items} != set(original):
        raise ValueError("ANNOTATION_ID_MISMATCH")
    for item in labels.items:
        row = original[item.occurrence_id]
        quotes = [row.raw_question, *[s.get("quote", "") for s in row.source_spans]]
        if not item.evidence_quote or not any(item.evidence_quote in q for q in quotes):
            raise ValueError("ANNOTATION_UNGROUNDED")


def model_labels(database, settings, gate, budget, prompt, payload, batch, prompt_version):
    if not settings.model_api_key or not settings.model_base_url:
        raise ValueError("MODEL_CONFIGURATION_INCOMPLETE")
    compact_payload = [{"i": i, **{k: v for k, v in item.items() if k != "occurrence_id"}} for i, item in enumerate(payload)]
    messages = [{"role": "system", "content": prompt}, {"role": "user", "content": json.dumps(compact_payload, ensure_ascii=False)}]
    for attempt in range(2):
        body = {"model": settings.query_model or settings.judge_model, "temperature": 0,
            "messages": messages,
            "response_format": {"type": "json_schema", "json_schema": {"name": "TaskLabels", "strict": False, "schema": CompactTaskLabels.model_json_schema()}},
            "max_tokens": 8192}
        if "ark.cn-" in settings.model_base_url:
            body["thinking"] = {"type": "disabled"}
        budget.before_call(estimated_input_tokens=len(json.dumps(body, ensure_ascii=False).encode()) + 8192)
        started, provider_started, phase, data, error = time.perf_counter(), None, {}, {}, None
        try:
            with gate.call() as phase, httpx.Client(timeout=settings.model_request_timeout_seconds, trust_env=False) as client:
                provider_started = time.perf_counter()
                response = client.post(settings.model_base_url.rstrip("/") + "/chat/completions", json=body,
                                       headers={"Authorization": f"Bearer {settings.model_api_key}"})
                response.raise_for_status()
                data = response.json()
                budget.after_call(input_tokens=data.get("usage", {}).get("prompt_tokens"),
                                  output_tokens=data.get("usage", {}).get("completion_tokens"))
                if data["choices"][0]["finish_reason"] == "length":
                    raise ValueError("MODEL_OUTPUT_TRUNCATED")
                compact = CompactTaskLabels.model_validate_json(data["choices"][0]["message"]["content"])
                if len(compact.items) != len(batch) or {item.i for item in compact.items} != set(range(len(batch))):
                    raise ValueError("ANNOTATION_ID_MISMATCH")
                labels = TaskLabels(items=[TaskLabel(occurrence_id=batch[item.i].id, response_form=item.f,
                    coding_focus=item.c, confidence=item.p, evidence_quote=batch[item.i].raw_question)
                    for item in compact.items])
                validate_labels(labels, batch)
                return labels
        except ValueError as failure:
            error = failure
            if attempt or not isinstance(failure, ValidationError) and str(failure) not in {"ANNOTATION_ID_MISMATCH", "ANNOTATION_UNGROUNDED"}:
                raise
            details = (failure.errors(include_url=False, include_context=False, include_input=False)
                       if isinstance(failure, ValidationError) else str(failure))
            messages.append({"role": "user", "content": "上一批格式或证据校验失败。重新输出完整批次，保持序号，严格区分作答形式与任务焦点。校验错误：" + json.dumps(details, ensure_ascii=False)})
        except BaseException as failure:
            error = failure
            raise
        finally:
            usage = data.get("usage", {})
            with database.session() as session, session.begin():
                session.add(ModelCall(request_id=f"annotation:{uuid4()}", operation_type="TASK_ANNOTATION",
                    model=body["model"], model_revision=data.get("model"), prompt_version=prompt_version,
                    input_tokens=usage.get("prompt_tokens"), output_tokens=usage.get("completion_tokens"),
                    latency_ms=int((time.perf_counter() - started) * 1000), **phase,
                    provider_ms=int((time.perf_counter() - provider_started) * 1000) if provider_started else 0,
                    status="FAILED" if error else "SUCCEEDED", retry_count=attempt,
                    error_code=type(error).__name__ if error else None,
                    usage_source="PROVIDER" if usage.get("prompt_tokens") is not None else "UNAVAILABLE"))


def annotate_tasks(database, settings, *, limit=1000, batch_size=40, max_calls=30,
                   max_tokens=300_000, dry_run=False, classifier=None):
    if not 1 <= limit <= 100_000 or not 1 <= batch_size <= 100:
        raise ValueError("invalid annotation batch bounds")
    with database.session() as session:
        rows = list(session.scalars(select(QuestionOccurrence).select_from(_active_from()).where(
            *_conditions(FilterSpec()), ~select(OccurrenceTaskAnnotation.occurrence_id).where(
                OccurrenceTaskAnnotation.occurrence_id == QuestionOccurrence.id).exists())
            .order_by(QuestionOccurrence.id).limit(limit)))
    if dry_run:
        return {"pending_in_batch": len(rows), "batches": (len(rows) + batch_size - 1) // batch_size,
                "model_calls": 0, "changed": 0}
    gate = ModelCallGate(settings.model_lock_path, minimum_interval_seconds=settings.model_min_interval_seconds)
    budget = CallBudget(max_calls=max_calls, max_tokens=max_tokens)
    prompt_version = "task_annotation_v4" if classifier is None else "task_annotation_v1"
    prompt = resource_path(f"prompts/{prompt_version}.md").read_text(encoding="utf-8")
    changed = 0
    for offset in range(0, len(rows), batch_size):
        batch = rows[offset:offset + batch_size]
        payload = [{"occurrence_id": row.id, "raw_question": row.raw_question,
                    "normalized_question": row.normalized_question, "context_before": row.context_before,
                    "context_after": row.context_after,
                    "source_quotes": [s.get("quote", "") for s in row.source_spans]} for row in batch]
        if classifier:
            labels = TaskLabels.model_validate(classifier(payload))
        else:
            labels = model_labels(database, settings, gate, budget, prompt, payload, batch, prompt_version)
        original = {row.id: row for row in batch}
        validate_labels(labels, batch)
        with database.session() as session, session.begin():
            for item in labels.items:
                if session.get(OccurrenceTaskAnnotation, item.occurrence_id):
                    continue
                session.add(OccurrenceTaskAnnotation(occurrence_id=item.occurrence_id,
                    classification_status="UNKNOWN" if item.confidence<0.7 or "UNKNOWN" in {
                        item.response_form,item.coding_focus} else "NEEDS_REVIEW",
                    response_form=item.response_form if item.confidence >= 0.7 else "UNKNOWN",
                    coding_focus=item.coding_focus if item.confidence >= 0.7 else "UNKNOWN",
                    producer_version=prompt_version, evidence={"quote": item.evidence_quote,
                        "quote_origin": "host_original" if classifier is None else "classifier",
                        "confidence": item.confidence, "input_hash": hashlib.sha256(original[item.occurrence_id].raw_question.encode()).hexdigest()}))
                changed += 1
            state = session.get(CorpusState, 1, with_for_update=True)
            state.task_annotation_revision += 1
    return {"changed": changed, "model_calls": budget.used_calls, "used_tokens": budget.used_tokens}
