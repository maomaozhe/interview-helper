"""Local browser workspace and small, durable diagnostic feedback records."""

from __future__ import annotations

import html
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field, field_validator
from sqlalchemy import case, select

from interview_intelligence.analytics.stats import _active_from, _conditions, query_question_stats
from interview_intelligence.config import resolve_corpus_path, discover_corpus_documents
from interview_intelligence.contracts import StatsRequest, StrictModel
from interview_intelligence.domain.models import (
    CanonicalQuestion, CorpusState, DocumentBuild, Interview, PipelineRun, PipelineTask,
    SourceDocument, SourceRevision,
)
from interview_intelligence.ingestion.snapshot import decode_source


WEB_ROOT = Path(__file__).with_name("web")
WEB_ASSETS = ("app.css", "workspace.css", "core.js", "query-stream.js", "app.js")


def workspace_version():
    return hashlib.sha256(b"".join((WEB_ROOT / "assets" / name).read_bytes()
                                   for name in WEB_ASSETS)).hexdigest()[:12]


class RevalidatedStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


class FeedbackRequest(StrictModel):
    category: Literal["IRRELEVANT", "TAG", "DEDUP", "SOURCE", "OTHER"]
    note: str = Field(min_length=1, max_length=2000)
    query: str = Field(default="", max_length=2000)
    canonical_question_id: str | None = Field(default=None, max_length=36)
    context: dict = Field(default_factory=dict)
    run_id: str | None = Field(default=None, max_length=36)
    request_id: str | None = Field(default=None, max_length=128)

    @field_validator("context")
    @classmethod
    def bounded_context(cls, value):
        if len(json.dumps(value, ensure_ascii=False)) > 30_000:
            raise ValueError("feedback context is too large")
        return value


def local_documents(settings) -> list[Path]:
    return discover_corpus_documents(settings.corpus_root)


def document_inventory(database, settings) -> list[dict]:
    with database.session() as session:
        published = session.execute(
            select(SourceDocument, DocumentBuild, SourceRevision)
            .join(DocumentBuild, SourceDocument.active_build_id == DocumentBuild.id)
            .join(SourceRevision, DocumentBuild.source_revision_id == SourceRevision.id)
        ).all()
        known = {path.replace("\\", "/"): (build, revision, document.id)
                 for document, build, revision in published
                 for path in [document.original_relative_path, *document.aliases]}
        sources = {path.replace("\\", "/"): document.id
                   for document in session.scalars(select(SourceDocument))
                   for path in [document.original_relative_path, *document.aliases]}
        tasks = {}
        for task, child in session.execute(select(PipelineTask, PipelineRun)
                .join(PipelineRun, PipelineTask.run_id == PipelineRun.id)
                .order_by(PipelineTask.created_at.desc())):
            tasks.setdefault(task.source_document_id, (task, child))
        work = {}
        for run in session.scalars(select(PipelineRun).order_by(PipelineRun.start_time.desc())):
            config = run.config_snapshot
            if "paths" not in config or config.get("mode") == "reindex":
                continue
            finished = set(config.get("finished_paths", []))
            remaining = [path for path in config["paths"] if path not in finished]
            current = config.get("current_path") or (remaining[0] if remaining and run.status == "RUNNING" else None)
            if run.status in {"QUEUED", "RUNNING"}:
                for path in remaining:
                    relative = path.replace("\\", "/")
                    state = "RUNNING" if run.status == "RUNNING" and path == current else "QUEUED"
                    if state == "RUNNING" or relative not in work:
                        work[relative] = state
        root = settings.corpus_root.resolve()
        result = []
        for path in local_documents(settings):
            relative = path.relative_to(root).as_posix()
            pair = known.get(relative)
            build, revision, source_id = pair if pair else (None, None, sources.get(relative))
            changed = bool(revision and hashlib.sha256(path.read_bytes()).hexdigest() != revision.raw_file_hash)
            task, child = tasks.get(source_id, (None, None))
            status = "CHANGED" if changed else build.decision if build else "PENDING"
            if task and (not build or task.updated_at > build.created_at):
                if task.state == "FAILED":
                    status = "FAILED"
                elif child.status == "NEEDS_REVIEW":
                    status = "NEEDS_REVIEW"
                elif child.status == "RUNNING" and task.state in {"PENDING", "RUNNING"}:
                    status = "RUNNING"
            if status != "RUNNING":
                status = work.get(relative, status)
            result.append({"path": relative, "title": path.stem,
                           "status": status, "content_changed": changed,
                           "active_status": build.decision if build else None,
                           "revision_id": revision.id if revision else None,
                           "exclusion_reason": build.exclusion_reason if build else None,
                           "stage": ("DEDUP" if task and task.artifact_id and task.stage == "EXTRACT"
                                     else task.stage if task else None),
                           "error_code": task.error_code if task else None,
                           "error_detail": task.error_detail if task else None})
        return result


def run_summary(run) -> dict:
    config = run.config_snapshot
    paths = set(config.get("paths", []))
    completed = (len(paths.intersection(config["finished_paths"])) if "finished_paths" in config else
                 min(len(paths), run.processed_documents + run.skipped_documents
                     + run.failed_documents + run.needs_review_documents))
    remaining = [path for path in config.get("paths", []) if path not in config.get("finished_paths", [])]
    current = (config.get("current_path") or (remaining[0] if remaining else None)) if run.status == "RUNNING" else None
    return {"run_id": run.id, "status": run.status,
            "start_time": run.start_time.isoformat(),
            "end_time": run.end_time.isoformat() if run.end_time else None,
            "mode": config.get("mode", "changed"),
            "total_documents": len(config.get("paths", [])),
            "completed_documents": completed,
            "current_path": current, "current_stage": config.get("current_stage"),
            "processed_documents": run.processed_documents,
            "processed_questions": run.processed_questions,
            "skipped_documents": run.skipped_documents,
            "failed_documents": run.failed_documents,
            "excluded_documents": run.excluded_documents,
            "needs_review_documents": run.needs_review_documents,
            "input_tokens": run.input_tokens, "output_tokens": run.output_tokens,
            "failed_paths": config.get("failed_paths", []),
            "last_error": config.get("last_error"), "index_error": config.get("index_error")}


def register_web(app, database, settings, respond):
    app.mount("/assets", RevalidatedStaticFiles(directory=WEB_ROOT / "assets"), name="web-assets")

    @app.get("/api/workspace/version", include_in_schema=False)
    def version():
        from fastapi.responses import JSONResponse
        return JSONResponse({"version":workspace_version()}, headers={"Cache-Control":"no-store"})

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def home(request: Request):
        prefix = request.scope.get("root_path", "").rstrip("/") + "/"
        page = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
        for asset in WEB_ASSETS:
            fingerprint = hashlib.sha256((WEB_ROOT / "assets" / asset).read_bytes()).hexdigest()[:12]
            page = page.replace(f'assets/{asset}"', f'assets/{asset}?v={fingerprint}"')
        page = page.replace("</head>", f'<meta name="workspace-version" content="{workspace_version()}"></head>')
        return HTMLResponse(page.replace("__BASE_PATH__", html.escape(prefix, quote=True)),
                            headers={"Cache-Control": "no-cache"})

    @app.get("/api/workspace")
    def workspace(request: Request):
        inventory = document_inventory(database, settings)
        with database.session() as session:
            stats = query_question_stats(session, StatsRequest(limit=1))
            ids = list(session.scalars(select(CanonicalQuestion.id).select_from(_active_from())
                                      .where(*_conditions(StatsRequest())).distinct()))
            companies = list(session.scalars(select(Interview.company_normalized)
                             .select_from(_active_from()).where(*_conditions(StatsRequest()),
                             Interview.company_normalized.is_not(None)).distinct()))
            state = session.get(CorpusState, 1)
            data = {"counts": {"local_documents": len(inventory),
                "included_documents": stats["meta"]["sample_counts"]["source_documents"],
                "excluded_documents": sum(item["active_status"] == "EXCLUDED" for item in inventory),
                "pending_documents": sum(item["status"] in {"PENDING", "CHANGED", "QUEUED", "RUNNING", "FAILED", "NEEDS_REVIEW"} for item in inventory),
                "occurrences": stats["meta"]["sample_counts"]["occurrences"],
                "canonical_questions": len(ids),
                "interviews": stats["meta"]["sample_counts"]["interviews"]},
                "companies": sorted(companies), "corpus_revision": state.current_revision,
                "indexed_revision": state.indexed_revision}
        return respond(request, database, data)

    @app.get("/api/corpus/documents")
    def documents(request: Request):
        return respond(request, database, document_inventory(database, settings))

    @app.get("/api/corpus/source")
    def local_source(request: Request, path: str = Query(min_length=1, max_length=512)):
        source = resolve_corpus_path(settings.corpus_root, path)
        if source.suffix.lower() != ".md":
            raise ValueError("only Markdown corpus files can be read")
        if not source.is_file():
            raise KeyError("SOURCE_FILE_NOT_FOUND")
        if source.stat().st_size > 2_000_000:
            raise ValueError("SOURCE_TOO_LARGE")
        return respond(request, database, {"path": path, "markdown": decode_source(source.read_bytes()),
                                          "line_start": 1})

    @app.get("/api/ingest/runs")
    def runs(request: Request, limit: int = Query(20, ge=1, le=100)):
        with database.session() as session:
            data = []
            priority = case((PipelineRun.status == "RUNNING", 0),
                            (PipelineRun.status == "QUEUED", 1), else_=2)
            queue_time = case((PipelineRun.status == "QUEUED", PipelineRun.start_time), else_=None)
            for run in session.scalars(select(PipelineRun).order_by(
                    priority, queue_time.asc(), PipelineRun.start_time.desc(), PipelineRun.id)):
                if "paths" in run.config_snapshot:
                    data.append(run_summary(run))
                    if len(data) == limit:
                        break
        return respond(request, database, data)

    feedback_root = settings.snapshot_root.parent / "feedback"

    @app.post("/api/feedback", status_code=201)
    def save_feedback(request: Request, payload: FeedbackRequest):
        if payload.canonical_question_id:
            with database.session() as session:
                if session.get(CanonicalQuestion, payload.canonical_question_id) is None:
                    raise KeyError("QUESTION_NOT_FOUND")
        from interview_intelligence.domain.models import AgentTurn
        record = {"id": str(uuid4()), "created_at": datetime.now(timezone.utc).isoformat(),
                  **payload.model_dump(mode="json"), "user_id": settings.local_user_id}
        run_id = payload.run_id or payload.context.get("run_id")
        request_id = payload.request_id or (payload.context.get("meta") or {}).get("request_id")
        if run_id or request_id:
            with database.session() as session:
                turn = session.scalar(select(AgentTurn).where(AgentTurn.user_id == settings.local_user_id,
                    AgentTurn.id == run_id if run_id else AgentTurn.request_id == request_id))
                if not turn:
                    raise KeyError("QUERY_RECEIPT_NOT_FOUND")
                record.update(run_id=turn.id, request_id=turn.request_id, conversation_id=turn.conversation_id)
                record["context"] = {**record["context"], "receipt": {"run_id": turn.id,
                    "request_id": turn.request_id, "conversation_id": turn.conversation_id,
                    "message": turn.message, "status": turn.status, "result": turn.response}}
        feedback_root.mkdir(parents=True, exist_ok=True)
        temporary = feedback_root / f"{record['id']}.tmp"
        target = feedback_root / f"{record['id']}.json"
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)
        return respond(request, database, record, status_code=201)

    @app.get("/api/feedback")
    def feedback(request: Request, limit: int = Query(100, ge=1, le=200)):
        records = []
        if feedback_root.is_dir():
            for path in feedback_root.glob("*.json"):
                try:
                    record = json.loads(path.read_text(encoding="utf-8"))
                    if (isinstance(record, dict) and "created_at" in record and "id" in record
                            and record.get("user_id", "local") == settings.local_user_id):
                        records.append(record)
                except (OSError, ValueError):
                    continue
        records.sort(key=lambda item: item["created_at"], reverse=True)
        return respond(request, database, records[:limit], extra={"total": len(records)})
