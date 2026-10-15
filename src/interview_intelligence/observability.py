"""Administrator-only views over durable agent, tool, model and ingestion records.

Reading observations never recovers, retries or changes the observed execution.
Unknown historical timing and unfinished tool outcomes remain explicitly unknown.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
import re
from typing import Literal

from fastapi import HTTPException, Query, Request
from sqlalchemy import case, func, literal, or_, select, union_all

from interview_intelligence.domain.models import (
    AgentEvent, AgentTurn, ModelCall, PipelineRun, PipelineTask, ToolInvocation, now_utc,
)

Window = Literal["1h", "24h", "7d", "30d", "all"]
Kind = Literal["ALL", "QUERY", "INGEST"]
Status = Literal["ALL", "RUNNING", "QUEUED", "SUCCEEDED", "FAILED", "INTERRUPTED",
                 "CANCELLED", "PARTIAL_FAILURE", "NEEDS_REVIEW"]
FAILED = {"FAILED", "INTERRUPTED", "PARTIAL_FAILURE"}
SECRET_KEY = re.compile(r"(?:api[_-]?key|token|password|secret|authorization|cookie|signing[_-]?key|next_cursor|thinking|reasoning_content)", re.I)


def safe(value, depth=0):
    """Bound evidence previews and remove credentials and private reasoning fields."""
    if depth > 12:
        return "[内容过深，已截断]"
    if isinstance(value, dict):
        result = {}
        for key, item in list(value.items())[:100]:
            result[str(key)] = "[已隐藏]" if SECRET_KEY.fullmatch(str(key)) else safe(item, depth + 1)
        if len(value) > 100:
            result["_truncated"] = True
        return result
    if isinstance(value, list):
        result = [safe(item, depth + 1) for item in value[:50]]
        if len(value) > 50:
            result.append(f"[另外 {len(value) - 50} 项未展开]")
        return result
    if isinstance(value, str):
        value = re.sub(r"(?i)\bBearer\s+[^\s\"'<>]+|\bsk-[A-Za-z0-9_-]{8,}", "[已隐藏]", value)
        value = re.sub(r"(?i)((?:api[_-]?key|password|secret|token)\s*[=:]\s*)[^\s,;\"'<>]+", r"\1[已隐藏]", value)
        return value if len(value) <= 12000 else value[:12000] + "\n[内容已截断]"
    return value


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def iso(value):
    return aware(value).isoformat() if value else None


def elapsed(start, end):
    return max(0, round((aware(end) - aware(start)).total_seconds() * 1000)) if start and end else None


def rate(failed, finished):
    return round(failed / finished, 6) if finished else None


def percentile(values, fraction=.95):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)] if ordered else None


def model_view(call):
    return {"id": call.id, "operation": call.operation_type, "model": call.model,
        "prompt_version": call.prompt_version, "status": call.status, "error_code": safe(call.error_code),
        "created_at": iso(call.created_at), "duration_ms": call.latency_ms,
        "queue_ms": call.queue_ms, "provider_ms": call.provider_ms, "ttft_ms": call.ttft_ms,
        "input_tokens": call.input_tokens, "output_tokens": call.output_tokens,
        "usage_source": call.usage_source, "retry_count": call.retry_count}


class AgentObservability:
    def __init__(self, database):
        self.database = database

    @staticmethod
    def cohort(window="24h", kind="ALL", q="", status="ALL"):
        query = select(AgentTurn.id.label("id"), literal("QUERY").label("kind"),
            AgentTurn.message.label("message"), AgentTurn.status.label("status"),
            AgentTurn.created_at.label("created_at"), AgentTurn.finished_at.label("finished_at"),
            AgentTurn.user_id.label("user_id"), AgentTurn.request_id.label("request_id"),
            AgentTurn.conversation_id.label("conversation_id"), AgentTurn.error_code.label("error_code"))
        pipeline = select(PipelineRun.id, literal("INGEST"), literal("面经导入 / 索引任务"),
            PipelineRun.status, PipelineRun.start_time, PipelineRun.end_time,
            literal(None), PipelineRun.id, literal(None), literal(None))
        if kind == "QUERY":
            pipeline = pipeline.where(literal(False))
        if kind == "INGEST":
            query = query.where(literal(False))
        rows = union_all(query, pipeline).subquery()
        filters = []
        if window != "all":
            filters.append(rows.c.created_at >= now_utc() - timedelta(
                hours={"1h": 1, "24h": 24, "7d": 168, "30d": 720}[window]))
        if status != "ALL":
            filters.append(rows.c.status == status)
        if q:
            pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            filters.append(or_(*(rows.c[key].ilike(pattern, escape="\\") for key in
                ("id", "message", "request_id", "conversation_id", "user_id"))))
        return select(rows).where(*filters).subquery()

    def summary(self, window="24h", kind="ALL"):
        cohort = self.cohort(window, kind)
        query_ids = select(cohort.c.id).where(cohort.c.kind == "QUERY")
        pipeline_ids = select(cohort.c.id).where(cohort.c.kind == "INGEST")
        with self.database.session() as session:
            counts = dict(session.execute(select(cohort.c.status, func.count()).group_by(cohort.c.status)).all())
            failed = sum(counts.get(key, 0) for key in FAILED)
            finished = failed + counts.get("SUCCEEDED", 0)
            durations = [elapsed(start, end) for start, end in session.execute(
                select(cohort.c.created_at, cohort.c.finished_at).where(cohort.c.finished_at.is_not(None)))
                if start and end]
            tool_rows = session.execute(select(ToolInvocation.tool_name, ToolInvocation.status, func.count())
                .where(ToolInvocation.run_id.in_(query_ids))
                .group_by(ToolInvocation.tool_name, ToolInvocation.status)).all()
            tools = {}
            for name, status, count in tool_rows:
                item = tools.setdefault(name, {"name": name, "total": 0, "succeeded": 0,
                    "failed": 0, "cancelled": 0, "unknown": 0})
                item["total"] += count
                item[{"COMPLETED": "succeeded", "FAILED": "failed", "CANCELLED": "cancelled"}.get(status, "unknown")] += count
            # Rejected calls have an error event but no executable host invocation.
            errors = session.scalars(select(AgentEvent).where(AgentEvent.run_id.in_(query_ids),
                AgentEvent.kind == "tool_error", AgentEvent.payload["action_id"].as_string().is_(None)))
            for event in errors:
                payload = event.payload or {}
                name = payload.get("tool", "unknown")
                item = tools.setdefault(name, {"name": name, "total": 0, "succeeded": 0,
                    "failed": 0, "cancelled": 0, "unknown": 0})
                item["total"] += 1
                item["cancelled" if payload.get("status") == "CANCELLED" else "failed"] += 1
            for item in tools.values():
                item["failure_rate"] = rate(item["failed"], item["failed"] + item["succeeded"])
            models = []
            model_scope = or_(ModelCall.query_run_id.in_(query_ids), ModelCall.run_id.in_(pipeline_ids))
            for operation, model, total, errors, input_tokens, output_tokens, latency in session.execute(
                select(ModelCall.operation_type, ModelCall.model, func.count(),
                    func.sum(case((ModelCall.status == "FAILED", 1), else_=0)),
                    func.sum(ModelCall.input_tokens), func.sum(ModelCall.output_tokens), func.avg(ModelCall.latency_ms))
                .where(model_scope).group_by(ModelCall.operation_type, ModelCall.model)):
                models.append({"operation": operation, "model": model, "total": total, "failed": errors,
                    "failure_rate": rate(errors, total), "input_tokens": input_tokens,
                    "output_tokens": output_tokens, "average_ms": round(latency)})
            tool_failed = sum(item["failed"] for item in tools.values())
            tool_finished = sum(item["failed"] + item["succeeded"] for item in tools.values())
            model_failed = sum(item["failed"] for item in models)
            model_total = sum(item["total"] for item in models)
            return {"window": window, "kind": kind, "total": sum(counts.values()), "statuses": counts,
                "failed": failed, "finished": finished, "failure_rate": rate(failed, finished),
                "running": counts.get("RUNNING", 0), "cancelled": counts.get("CANCELLED", 0),
                "p95_ms": percentile(durations), "duration_samples": len(durations),
                "tool_calls": sum(item["total"] for item in tools.values()), "tool_failure_rate": rate(tool_failed, tool_finished),
                "model_calls": model_total, "model_failure_rate": rate(model_failed, model_total),
                "tools": sorted(tools.values(), key=lambda item: (-item["failed"], -item["total"], item["name"])),
                "models": sorted(models, key=lambda item: (-item["failed"], -item["total"])),
                "generated_at": iso(now_utc())}

    def runs(self, window="24h", kind="ALL", status="ALL", q="", page=1, page_size=20):
        cohort = self.cohort(window, kind, q, status)
        with self.database.session() as session:
            total = session.scalar(select(func.count()).select_from(cohort))
            rows = list(session.execute(select(cohort).order_by(cohort.c.created_at.desc(), cohort.c.id.desc())
                .offset((page - 1) * page_size).limit(page_size)).mappings())
            query_ids = [row["id"] for row in rows if row["kind"] == "QUERY"]
            pipeline_ids = [row["id"] for row in rows if row["kind"] == "INGEST"]
            tool_counts = dict(session.execute(select(ToolInvocation.run_id, func.count())
                .where(ToolInvocation.run_id.in_(query_ids)).group_by(ToolInvocation.run_id)).all())
            for identifier, count in session.execute(select(AgentEvent.run_id, func.count())
                .where(AgentEvent.run_id.in_(query_ids), AgentEvent.kind == "tool_error",
                    AgentEvent.payload["action_id"].as_string().is_(None)).group_by(AgentEvent.run_id)):
                tool_counts[identifier] = tool_counts.get(identifier, 0) + count
            model_counts = {}
            for query_id, pipeline_id, count in session.execute(select(ModelCall.query_run_id, ModelCall.run_id, func.count())
                .where(or_(ModelCall.query_run_id.in_(query_ids), ModelCall.run_id.in_(pipeline_ids)))
                .group_by(ModelCall.query_run_id, ModelCall.run_id)):
                key = query_id or pipeline_id
                model_counts[key] = model_counts.get(key, 0) + count
            items = [{**dict(row), "message": safe(row["message"][:220]),
                "created_at": iso(row["created_at"]), "finished_at": iso(row["finished_at"]),
                "duration_ms": elapsed(row["created_at"], row["finished_at"] or (now_utc() if row["status"] == "RUNNING" else None)),
                "error_code": safe(row["error_code"]), "tool_calls": tool_counts.get(row["id"], 0),
                "model_calls": model_counts.get(row["id"], 0)} for row in rows]
            return {"items": items, "total": total, "page": page, "page_size": page_size}

    def detail(self, kind, run_id, after=0, event_limit=100):
        with self.database.session() as session:
            if kind == "INGEST":
                return self.pipeline_detail(session, run_id, after, event_limit)
            turn = session.get(AgentTurn, run_id)
            if not turn:
                raise HTTPException(404, "RUN_NOT_FOUND")
            # Tools are bounded by the host budget; event streams use cursor pagination.
            invocations = list(session.scalars(select(ToolInvocation).where(ToolInvocation.run_id == run_id)
                .order_by(ToolInvocation.ordinal)))
            relevant = list(session.scalars(select(AgentEvent).where(AgentEvent.run_id == run_id,
                AgentEvent.kind.in_(["stage", "tool_result", "tool_error"]))
                .order_by(AgentEvent.sequence)))
            tools = []
            for invocation in invocations:
                starts = [event for event in relevant if event.kind == "stage" and
                    event.payload.get("action_id") == invocation.id]
                ends = [event for event in relevant if event.kind in {"tool_result", "tool_error"} and
                    (event.payload.get("action_id") == invocation.id or
                     (not event.payload.get("action_id") and event.payload.get("tool") == invocation.tool_name and
                      starts and event.sequence > starts[-1].sequence))]
                start = starts[-1] if starts else None
                end = ends[0] if ends else None
                duration = elapsed(start.created_at if start else None, end.created_at if end else None)
                if duration is None and invocation.status == "COMPLETED":
                    duration = ((invocation.result or {}).get("facts", {}).get("meta", {}).get("timings", {}).get("tool_ms"))
                status = invocation.status
                if status == "STARTED" and turn.status != "RUNNING":
                    status = "UNKNOWN"
                tools.append({"id": invocation.id, "ordinal": invocation.ordinal, "name": invocation.tool_name,
                    "status": status, "started_at": iso(start.created_at if start else None),
                    "duration_ms": duration, "arguments": safe(invocation.arguments), "result": safe(invocation.result)})
            for event in relevant:
                if event.kind == "tool_error" and not event.payload.get("action_id"):
                    tools.append({"id": event.id, "ordinal": None, "name": event.payload.get("tool"),
                        "status": event.payload.get("status", "FAILED"), "started_at": iso(event.created_at),
                        "duration_ms": None, "arguments": safe(event.payload.get("arguments")),
                        "result": {"error_code": safe(event.payload.get("error_code"))}})
            events = list(session.scalars(select(AgentEvent).where(AgentEvent.run_id == run_id,
                AgentEvent.sequence > after).order_by(AgentEvent.sequence).limit(event_limit)))
            calls = list(session.scalars(select(ModelCall).where(ModelCall.query_run_id == run_id).order_by(ModelCall.created_at)))
            last = events[-1].sequence if events else after
            return {"id": turn.id, "kind": "QUERY", "request_id": turn.request_id,
                "conversation_id": turn.conversation_id, "user_id": turn.user_id,
                "message": safe(turn.message), "status": turn.status, "error_code": safe(turn.error_code),
                "created_at": iso(turn.created_at), "finished_at": iso(turn.finished_at),
                "duration_ms": elapsed(turn.created_at, turn.finished_at or (now_utc() if turn.status == "RUNNING" else None)),
                "result": safe(turn.response), "tools": tools, "models": [model_view(call) for call in calls],
                "events": [{"sequence": event.sequence, "type": event.kind,
                    "created_at": iso(event.created_at), "offset_ms": elapsed(turn.created_at, event.created_at),
                    "data": safe(event.payload)} for event in events],
                "last_sequence": turn.event_sequence, "next_after": last, "has_more": last < turn.event_sequence,
                "attempts": session.scalar(select(func.count()).select_from(AgentEvent).where(
                    AgentEvent.run_id == run_id, AgentEvent.kind == "accepted"))}

    @staticmethod
    def pipeline_detail(session, run_id, after, event_limit):
        run = session.get(PipelineRun, run_id)
        if not run:
            raise HTTPException(404, "RUN_NOT_FOUND")
        task_total = session.scalar(select(func.count()).select_from(PipelineTask).where(PipelineTask.run_id == run_id))
        tasks = list(session.scalars(select(PipelineTask).where(PipelineTask.run_id == run_id)
            .order_by(PipelineTask.created_at, PipelineTask.id).offset(after).limit(event_limit)))
        calls = list(session.scalars(select(ModelCall).where(ModelCall.run_id == run_id)
            .order_by(ModelCall.created_at).limit(500)))
        return {"id": run.id, "kind": "INGEST", "request_id": run.id, "status": run.status,
            "message": "面经导入 / 索引任务", "created_at": iso(run.start_time), "finished_at": iso(run.end_time),
            "duration_ms": elapsed(run.start_time, run.end_time or (now_utc() if run.status == "RUNNING" else None)),
            "config": safe(run.config_snapshot), "versions": {"pipeline": run.pipeline_version,
                "extractor": run.extractor_version, "embedding": run.embedding_version},
            "result": {"processed_documents": run.processed_documents, "processed_questions": run.processed_questions,
                "failed_documents": run.failed_documents, "skipped_documents": run.skipped_documents,
                "needs_review_documents": run.needs_review_documents}, "tools": [],
            "models": [model_view(call) for call in calls], "models_truncated": len(calls) == 500,
            "events": [{"sequence": after + index + 1, "type": task.stage, "created_at": iso(task.created_at),
                "offset_ms": elapsed(run.start_time, task.created_at), "data": {"task_id": task.id,
                    "status": task.state, "attempt_count": task.attempt_count, "error_code": safe(task.error_code),
                    "error_detail": safe(task.error_detail), "updated_at": iso(task.updated_at),
                    "source_document_id": task.source_document_id}} for index, task in enumerate(tasks)],
            "last_sequence": task_total, "next_after": after + len(tasks), "has_more": after + len(tasks) < task_total}


def register_observability(app, database, access_service):
    service = AgentObservability(database)

    @app.get("/api/admin/agent/summary")
    def summary(request: Request, window: Window = "24h", kind: Kind = "ALL"):
        access_service.authenticate(request)
        return {"data": service.summary(window, kind)}

    @app.get("/api/admin/agent/runs")
    def runs(request: Request, window: Window = "24h", kind: Kind = "ALL", status: Status = "ALL",
             q: str = Query(default="", max_length=200), page: int = Query(default=1, ge=1),
             page_size: int = Query(default=20, ge=1, le=100)):
        access_service.authenticate(request)
        return {"data": service.runs(window, kind, status, q, page, page_size)}

    @app.get("/api/admin/agent/runs/{kind}/{run_id}")
    def detail(request: Request, kind: Literal["QUERY", "INGEST"], run_id: str,
               after: int = Query(default=0, ge=0), event_limit: int = Query(default=100, ge=1, le=200)):
        access_service.authenticate(request)
        return {"data": service.detail(kind, run_id, after, event_limit)}

    return service
