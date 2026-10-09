import json
import hashlib
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from interview_intelligence.api import create_app
from interview_intelligence.config import Settings
from interview_intelligence.domain.models import CorpusState, PipelineRun, PipelineTask, SourceDocument, SourceRevision
from sqlalchemy import select
from test_analytics import seed_corpus


@pytest.fixture
def workspace(tmp_path):
    db, question_id, _ = seed_corpus()
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    for number in range(1, 5):
        (corpus / f"{number}.md").write_text(f"# 面经 {number}\nRedis 为什么快？", encoding="utf-8")
    with db.session() as session:
        with session.begin():
            for revision, document in session.execute(select(SourceRevision, SourceDocument)
                    .join(SourceDocument, SourceRevision.source_document_id == SourceDocument.id)):
                revision.raw_file_hash = hashlib.sha256((corpus / document.original_relative_path).read_bytes()).hexdigest()
    settings = Settings(database_url="sqlite+pysqlite:///:memory:", corpus_root=corpus,
                        snapshot_root=tmp_path / "snapshots")
    return TestClient(create_app(db, settings)), db, question_id, settings


def test_web_shell_and_assets_are_served_with_proxy_prefix(workspace):
    _, db, _, settings = workspace
    settings.api_root_path = "/interview"
    client = TestClient(create_app(db, settings))
    shell = client.get("/")
    assert shell.status_code == 200
    assert 'text/html' in shell.headers["content-type"]
    assert '<base href="/interview/">' in shell.text
    assert "题库与检索" in shell.text
    for name, content_type in (("app.js", "javascript"), ("app.css", "text/css")):
        asset = client.get(f"/interview/assets/{name}")
        assert asset.status_code == 200
        assert content_type in asset.headers["content-type"]


def test_workspace_counts_and_facets_use_published_occurrences(workspace):
    client, _, _, _ = workspace
    response = client.get("/api/workspace")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["counts"]["occurrences"] == 3
    assert data["counts"]["canonical_questions"] == 2
    assert data["counts"]["included_documents"] == 3
    assert data["counts"]["local_documents"] == 4
    assert data["companies"] == ["字节", "腾讯"]


def test_versioned_assets_revalidate_and_match_the_proxy_shell(workspace):
    import re
    client,_,_,_=workspace
    shell=client.get("/")
    version=re.search(r'name="workspace-version" content="([0-9a-f]+)"',shell.text).group(1)
    response=client.get("/api/workspace/version")
    assert response.json() == {"version":version}
    assert response.headers["cache-control"] == "no-store"
    for name in ("app.js","core.js","query-stream.js","workspace.css","sidebar.js",
                 "markdown.js","markdown.css","answer-stream.js","vendor/markdown-it.umd.min.js"):
        asset=re.search(r'assets/'+re.escape(name)+r'\?v=([0-9a-f]+)',shell.text).group(1)
        body=client.get(f"/assets/{name}?v={asset}")
        assert hashlib.sha256(body.content).hexdigest().startswith(asset)
        assert body.headers["cache-control"] == "no-cache, must-revalidate"


def test_document_inventory_distinguishes_pending_files(workspace):
    client, _, _, _ = workspace
    response = client.get("/api/corpus/documents")
    assert response.status_code == 200
    documents = {item["path"]: item for item in response.json()["data"]}
    assert documents["1.md"]["status"] == "INCLUDED"
    assert documents["1.md"]["revision_id"]
    assert documents["4.md"]["status"] == "PENDING"


def test_issue_log_is_excluded_from_inventory_and_ingest_queue(workspace):
    client, _, _, settings = workspace
    (settings.corpus_root / "issue.md").write_text("# 问题记录", encoding="utf-8")
    assert len(client.get("/api/corpus/documents").json()["data"]) == 4
    settings.model_api_key = "test-key"
    settings.model_base_url = "https://example.com/v1"
    settings.max_model_calls = 10
    settings.max_model_tokens = 10000
    response = client.post("/api/ingest", json={"idempotency_key": "ignore-issue"})
    assert response.status_code == 202
    assert response.json()["data"]["total_documents"] == 4
    reserved = client.post("/api/ingest", json={"paths": ["issue.md"], "idempotency_key": "reject-issue"})
    assert reserved.status_code == 400


def test_local_source_is_plain_text_and_cannot_escape_corpus(workspace, tmp_path):
    client, _, _, settings = workspace
    (tmp_path / "secret.md").write_text("private", encoding="utf-8")
    source = client.get("/api/corpus/source", params={"path": "4.md"})
    assert source.status_code == 200
    assert source.json()["data"]["markdown"].startswith("# 面经 4")
    escape = client.get("/api/corpus/source", params={"path": "../secret.md"})
    assert escape.status_code == 400
    other_file = settings.corpus_root / "secret.env"
    other_file.write_text("private", encoding="utf-8")
    assert client.get("/api/corpus/source", params={"path": "secret.env"}).status_code == 400


def test_recent_runs_report_total_and_failed_paths(workspace):
    client, db, _, _ = workspace
    with db.session() as session:
        with session.begin():
            run = PipelineRun(pipeline_version="v1", extractor_version="v1", taxonomy_version="v1",
                              embedding_version="v1", status="PARTIAL_FAILURE", processed_documents=1,
                              failed_documents=1, config_snapshot={"paths": ["1.md", "4.md"],
                                                                   "failed_paths": ["4.md"]})
            session.add(run)
    response = client.get("/api/ingest/runs")
    assert response.status_code == 200
    run_data = response.json()["data"][0]
    assert run_data["total_documents"] == 2
    assert run_data["failed_paths"] == ["4.md"]


def test_document_inventory_reports_execution_queue_failure_and_active_version(workspace):
    client, db, _, _ = workspace
    with db.session() as session, session.begin():
        source = session.scalar(select(SourceDocument).where(SourceDocument.original_relative_path == "3.md"))
        child = PipelineRun(pipeline_version="v1", extractor_version="v1", taxonomy_version="v1",
                            embedding_version="v1", status="FAILED", config_snapshot={})
        session.add(child)
        session.flush()
        session.add(PipelineTask(run_id=child.id, source_document_id=source.id, stage="EXTRACT",
                                task_key="failed-source", state="FAILED", error_code="ValueError",
                                error_detail="quote not found in immutable source"))
        session.add(PipelineRun(pipeline_version="v1", extractor_version="v1", taxonomy_version="v1",
            embedding_version="v1", status="RUNNING", config_snapshot={"paths": ["2.md", "4.md"],
                "finished_paths": ["2.md"], "current_path": "4.md"}))
        session.add(PipelineRun(pipeline_version="v1", extractor_version="v1", taxonomy_version="v1",
            embedding_version="v1", status="QUEUED", config_snapshot={"paths": ["1.md"]}))
    docs = {row["path"]: row for row in client.get("/api/corpus/documents").json()["data"]}
    assert docs["1.md"]["status"] == "QUEUED"
    assert docs["1.md"]["active_status"] == "INCLUDED"
    assert docs["2.md"]["status"] == "INCLUDED"
    assert docs["4.md"]["status"] == "RUNNING"
    assert docs["3.md"]["status"] == "FAILED"
    assert docs["3.md"]["active_status"] == "INCLUDED"
    assert docs["3.md"]["error_detail"] == "quote not found in immutable source"


def test_later_queued_run_does_not_hide_the_current_running_document(workspace):
    client, db, _, _ = workspace
    with db.session() as session, session.begin():
        session.add(PipelineRun(pipeline_version="v1", extractor_version="v1", taxonomy_version="v1",
            embedding_version="v1", status="RUNNING", start_time=datetime(2026, 10, 1, tzinfo=timezone.utc),
            config_snapshot={"paths": ["1.md", "4.md"], "current_path": "4.md"}))
        session.add(PipelineRun(pipeline_version="v1", extractor_version="v1", taxonomy_version="v1",
            embedding_version="v1", status="QUEUED", start_time=datetime(2026, 10, 2, tzinfo=timezone.utc),
            config_snapshot={"paths": ["1.md", "4.md"]}))
    docs = {row["path"]: row for row in client.get("/api/corpus/documents").json()["data"]}
    assert docs["4.md"]["status"] == "RUNNING"
    assert docs["1.md"]["status"] == "QUEUED"
    assert docs["1.md"]["active_status"] == "INCLUDED"


def test_queued_retry_does_not_hide_an_active_child_task(workspace):
    client, db, _, _ = workspace
    with db.session() as session, session.begin():
        source = session.scalar(select(SourceDocument).where(SourceDocument.original_relative_path == "3.md"))
        child = PipelineRun(pipeline_version="v1", extractor_version="v1", taxonomy_version="v1",
                            embedding_version="v1", status="RUNNING", config_snapshot={})
        session.add(child)
        session.flush()
        session.add(PipelineTask(run_id=child.id, source_document_id=source.id, stage="DEDUP",
                                task_key="active-source", state="RUNNING"))
        session.add(PipelineRun(pipeline_version="v1", extractor_version="v1", taxonomy_version="v1",
            embedding_version="v1", status="QUEUED", config_snapshot={"paths": ["3.md"]}))
    docs = {row["path"]: row for row in client.get("/api/corpus/documents").json()["data"]}
    assert docs["3.md"]["status"] == "RUNNING"
    assert docs["3.md"]["active_status"] == "INCLUDED"


def test_document_alias_is_recognized_as_the_same_published_source(workspace):
    client, db, _, settings = workspace
    (settings.corpus_root / "alias.md").write_bytes((settings.corpus_root / "1.md").read_bytes())
    with db.session() as session, session.begin():
        source = session.scalar(select(SourceDocument).where(SourceDocument.original_relative_path == "1.md"))
        source.aliases = ["alias.md"]
    docs = {row["path"]: row for row in client.get("/api/corpus/documents").json()["data"]}
    assert docs["alias.md"]["status"] == "INCLUDED"
    assert docs["alias.md"]["revision_id"] == docs["1.md"]["revision_id"]


def test_run_progress_counts_unique_finished_files_and_exposes_current_file(workspace):
    client, db, _, _ = workspace
    with db.session() as session, session.begin():
        session.add(PipelineRun(pipeline_version="v1", extractor_version="v1", taxonomy_version="v1",
            embedding_version="v1", status="RUNNING", processed_documents=2, failed_documents=2,
            config_snapshot={"paths": ["1.md", "2.md"], "finished_paths": ["1.md", "1.md"],
                             "current_path": "2.md", "current_stage": "EXTRACT"}))
    row = client.get("/api/ingest/runs").json()["data"][0]
    assert row["completed_documents"] == 1
    assert row["current_path"] == "2.md"
    assert row["current_stage"] == "EXTRACT"


def test_issue_feedback_survives_app_restart(workspace):
    client, db, question_id, settings = workspace
    response = client.post("/api/feedback", json={"category": "IRRELEVANT", "note": "OOM 检索出现偏题",
        "query": "oom有哪些常见的问法？", "canonical_question_id": question_id,
        "context": {"pipeline": "HYBRID", "filters": {"topic_l1": None}}})
    assert response.status_code == 201
    saved = response.json()["data"]
    assert saved["id"] and saved["created_at"]
    restarted = TestClient(create_app(db, settings))
    rows = restarted.get("/api/feedback").json()["data"]
    assert len(rows) == 1
    assert rows[0]["query"] == "oom有哪些常见的问法？"
    assert rows[0]["context"]["pipeline"] == "HYBRID"
    assert "MODEL_API_KEY" not in json.dumps(rows)


def test_feedback_rejects_invalid_categories_and_oversized_context(workspace):
    client, _, _, _ = workspace
    assert client.post("/api/feedback", json={"category": "unknown", "note": "x"}).status_code == 422
    assert client.post("/api/feedback", json={"category": "OTHER", "note": "x",
                                            "context": {"value": "x" * 40_000}}).status_code == 422


def test_review_state_returns_existing_note_score_and_version_for_editing(workspace):
    client, _, question_id, _ = workspace
    response = client.post("/api/review", json={"idempotency_key": "web-note",
        "items": [{"canonical_question_id": question_id, "status": "WEAK", "score": 2,
                   "note": "补充线程模型与 IO 多路复用"}]})
    assert response.status_code == 201
    state = client.get("/api/review/state", params={"canonical_question_ids": question_id}).json()
    saved = state["data"]["states"][question_id]
    assert saved["note"] == "补充线程模型与 IO 多路复用"
    assert saved["last_score"] == 2
    assert saved["version"] == 1


def test_document_inventory_detects_changes_after_publication(workspace):
    client, _, _, settings = workspace
    (settings.corpus_root / "1.md").write_text("# 新增 OOM 问题", encoding="utf-8")
    documents = {row["path"]: row for row in client.get("/api/corpus/documents").json()["data"]}
    assert documents["1.md"]["status"] == "CHANGED"
    assert documents["1.md"]["active_status"] == "INCLUDED"


def test_run_list_does_not_offer_empty_retries_for_internal_file_runs(workspace):
    client, db, _, _ = workspace
    with db.session() as session:
        with session.begin():
            session.add(PipelineRun(pipeline_version="v1", extractor_version="v1", taxonomy_version="v1",
                embedding_version="v1", status="QUEUED", config_snapshot={"paths": ["4.md"]},
                start_time=datetime(2026, 10, 1, tzinfo=timezone.utc)))
            session.add(PipelineRun(pipeline_version="v1", extractor_version="v1", taxonomy_version="v1",
                embedding_version="v1", status="FAILED", config_snapshot={"extractor": "v1"},
                start_time=datetime(2026, 10, 2, tzinfo=timezone.utc)))
    runs = client.get("/api/ingest/runs", params={"limit": 1}).json()["data"]
    assert len(runs) == 1
    assert runs[0]["total_documents"] == 1
    assert runs[0]["status"] == "QUEUED"


@pytest.mark.parametrize("status", ["RUNNING", "QUEUED"])
def test_older_active_import_remains_visible_after_many_completed_repairs(workspace, status):
    client, db, _, _ = workspace
    with db.session() as session, session.begin():
        active = PipelineRun(pipeline_version="v1", extractor_version="v1", taxonomy_version="v1",
            embedding_version="v1", status=status, config_snapshot={"paths": ["4.md"]},
            start_time=datetime(2026, 9, 30, tzinfo=timezone.utc))
        session.add(active)
        for hour in range(12):
            session.add(PipelineRun(pipeline_version="v1", extractor_version="v1", taxonomy_version="v1",
                embedding_version="v1", status="SUCCEEDED", config_snapshot={"paths": ["4.md"]},
                start_time=datetime(2026, 10, 1, hour, tzinfo=timezone.utc)))
        session.flush()
        active_id = active.id
    rows = client.get("/api/ingest/runs", params={"limit": 8}).json()["data"]
    assert len(rows) == 8
    assert rows[0]["run_id"] == active_id
    assert rows[0]["status"] == status


def test_pending_imports_are_shown_in_the_workers_queue_order(workspace):
    client, db, _, _ = workspace
    with db.session() as session, session.begin():
        ids = []
        for status, hour in (("QUEUED", 1), ("QUEUED", 2), ("RUNNING", 0)):
            run = PipelineRun(pipeline_version="v1", extractor_version="v1", taxonomy_version="v1",
                embedding_version="v1", status=status, config_snapshot={"paths": ["4.md"]},
                start_time=datetime(2026, 10, 1, hour, tzinfo=timezone.utc))
            session.add(run)
            session.flush()
            ids.append(run.id)
    rows = client.get("/api/ingest/runs", params={"limit": 3}).json()["data"]
    assert [row["run_id"] for row in rows] == [ids[2], ids[0], ids[1]]


def test_search_applies_company_and_round_filters_to_the_same_occurrence(workspace):
    _, db, _, settings = workspace
    with db.session() as session:
        with session.begin():
            session.get(CorpusState, 1).current_revision = 1
            session.get(CorpusState, 1).indexed_revision = 1

    class ScopeRetriever:
        def retrieve(self, query, eligible_ids, pipeline, top_k):
            return {"data": [{"canonical_question_id": item, "score": 1.0} for item in eligible_ids],
                    "meta": {"pipeline": pipeline}}

    client = TestClient(create_app(db, settings, ScopeRetriever()))
    result = client.get("/api/questions/search", params={"query": "Redis", "company": "字节",
                                                        "round": "SECOND", "pipeline": "BM25"})
    assert result.status_code == 200
    body = result.json()
    assert body["meta"]["applied_filters"]["company"] == "字节"
    assert body["meta"]["applied_filters"]["round"] == "SECOND"
    assert len(body["data"]) == 1
    assert body["data"][0]["canonical_text"] == "Redis持久化方式？"
    assert body["data"][0]["occurrence_count"] == 1
