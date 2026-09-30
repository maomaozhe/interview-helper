import httpx
import pytest

from interview_intelligence.domain.models import ModelCall, PipelineRun, PipelineTask, create_database
from interview_intelligence.worker import process_queued_run, recover_orphaned_runs


class StubOutcome:
    def __init__(self, status, question_count):
        self.status = status
        self.question_count = question_count


class StubIngestor:
    def __init__(self):
        self.paths = []

    def ingest_file(self, path):
        self.paths.append(path)
        return StubOutcome("SUCCEEDED", 2)


class PartiallyFailingIngestor(StubIngestor):
    def ingest_file(self, path):
        if path == "b.md":
            raise ValueError("bad source")
        return super().ingest_file(path)


def test_worker_persists_batch_progress_and_does_not_repeat_completed_run():
    db = create_database("sqlite+pysqlite:///:memory:")
    with db.session() as session:
        with session.begin():
            run = PipelineRun(pipeline_version="v1", extractor_version="v1",
                              taxonomy_version="v1", embedding_version="v1",
                              config_snapshot={"paths": ["a.md", "b.md"], "mode": "changed"},
                              status="QUEUED")
            session.add(run)
        run_id = run.id
    ingestor = StubIngestor()
    assert process_queued_run(db, run_id, ingestor, indexer=lambda: 1) == "SUCCEEDED"
    assert ingestor.paths == ["a.md", "b.md"]
    assert process_queued_run(db, run_id, ingestor, indexer=lambda: 1) == "SUCCEEDED"
    assert ingestor.paths == ["a.md", "b.md"]
    with db.session() as session:
        saved = session.get(PipelineRun, run_id)
        assert saved.processed_documents == 2
        assert saved.processed_questions == 4


def test_worker_keeps_failed_paths_for_targeted_retry():
    db = create_database("sqlite+pysqlite:///:memory:")
    with db.session() as session:
        with session.begin():
            run = PipelineRun(pipeline_version="v1", extractor_version="v1",
                              taxonomy_version="v1", embedding_version="v1",
                              config_snapshot={"paths": ["a.md", "b.md"], "mode": "changed"},
                              status="QUEUED")
            session.add(run)
        run_id = run.id
    assert process_queued_run(db, run_id, PartiallyFailingIngestor(), indexer=lambda: 1) == "PARTIAL_FAILURE"
    with db.session() as session:
        assert session.get(PipelineRun, run_id).config_snapshot["failed_paths"] == ["b.md"]


def test_worker_restart_requeues_orphaned_run_once():
    db = create_database("sqlite+pysqlite:///:memory:")
    with db.session() as session:
        with session.begin():
            run = PipelineRun(pipeline_version="v1", extractor_version="v1",
                              taxonomy_version="v1", embedding_version="v1",
                              config_snapshot={"paths": ["a.md"], "mode": "changed"},
                              status="RUNNING")
            session.add(run)
        run_id = run.id
    assert recover_orphaned_runs(db) == 1
    assert recover_orphaned_runs(db) == 0
    with db.session() as session:
        saved = session.get(PipelineRun, run_id)
        assert saved.status == "QUEUED"
        assert saved.config_snapshot["recovery_count"] == 1
    ingestor = StubIngestor()
    assert process_queued_run(db, run_id, ingestor, indexer=lambda: 1) == "SUCCEEDED"
    assert ingestor.paths == ["a.md"]


def test_worker_restart_resumes_after_durably_finished_document():
    db = create_database("sqlite+pysqlite:///:memory:")
    with db.session() as session:
        with session.begin():
            run = PipelineRun(pipeline_version="v1", extractor_version="v1",
                              taxonomy_version="v1", embedding_version="v1",
                              config_snapshot={"paths": ["a.md", "b.md"], "finished_paths": ["a.md"]},
                              processed_documents=1, processed_questions=2, status="RUNNING")
            session.add(run)
        run_id = run.id
    recover_orphaned_runs(db)
    ingestor = StubIngestor()
    assert process_queued_run(db, run_id, ingestor, lambda: 1) == "SUCCEEDED"
    assert ingestor.paths == ["b.md"]
    with db.session() as session:
        saved = session.get(PipelineRun, run_id)
        assert saved.processed_documents == 2
        assert saved.processed_questions == 4
        assert saved.config_snapshot["finished_paths"] == ["a.md", "b.md"]


def test_budget_stop_keeps_remaining_paths_and_does_not_call_indexer():
    db = create_database("sqlite+pysqlite:///:memory:")
    with db.session() as session:
        with session.begin():
            run = PipelineRun(pipeline_version="v1", extractor_version="v1",
                              taxonomy_version="v1", embedding_version="v1",
                              config_snapshot={"paths": ["a.md", "b.md", "c.md"]}, status="QUEUED")
            session.add(run)
        run_id = run.id

    class Exhausted(StubIngestor):
        def ingest_file(self, path):
            self.paths.append(path)
            raise ValueError("MODEL_CALL_BUDGET_EXCEEDED")

    ingestor = Exhausted()
    def forbidden_index():
        raise AssertionError("indexer must not consume more model calls")
    assert process_queued_run(db, run_id, ingestor, forbidden_index) == "FAILED"
    assert ingestor.paths == ["a.md"]
    with db.session() as session:
        saved = session.get(PipelineRun, run_id)
        assert saved.config_snapshot["failed_paths"] == ["a.md", "b.md", "c.md"]
        assert saved.config_snapshot["stopped_on_model_error"] is True


def test_restart_fails_document_child_without_enqueuing_it_as_batch():
    db = create_database("sqlite+pysqlite:///:memory:")
    with db.session() as session:
        with session.begin():
            child = PipelineRun(pipeline_version="v1", extractor_version="v1",
                                taxonomy_version="v1", embedding_version="v1",
                                config_snapshot={"processing_fingerprint": "fingerprint"}, status="RUNNING")
            session.add(child)
            session.flush()
            task = PipelineTask(run_id=child.id, stage="EXTRACT", task_key="orphan-child", state="PENDING")
            session.add(task)
        child_id, task_id = child.id, task.id
    assert recover_orphaned_runs(db) == 1
    with db.session() as session:
        assert session.get(PipelineRun, child_id).status == "FAILED"
        assert session.get(PipelineTask, task_id).error_code == "WORKER_RESTART"


@pytest.mark.parametrize("status_code", [401, 429])
def test_ark_embedding_http_error_stops_batch_before_next_document(status_code):
    db = create_database("sqlite+pysqlite:///:memory:")
    with db.session() as session:
        with session.begin():
            run = PipelineRun(pipeline_version="v1", extractor_version="v1",
                              taxonomy_version="v1", embedding_version="v1",
                              config_snapshot={"paths": ["a.md", "b.md"]}, status="QUEUED")
            session.add(run)
        run_id = run.id

    class UnavailableEmbedding(StubIngestor):
        def ingest_file(self, path):
            self.paths.append(path)
            request = httpx.Request("POST", "https://example.invalid/embeddings/multimodal")
            httpx.Response(status_code, request=request).raise_for_status()

    ingestor = UnavailableEmbedding()
    indexed = []
    assert process_queued_run(db, run_id, ingestor, lambda: indexed.append(True)) == "FAILED"
    assert ingestor.paths == ["a.md"]
    assert indexed == []
    with db.session() as session:
        saved = session.get(PipelineRun, run_id)
        assert saved.config_snapshot["stopped_on_model_error"] is True
        assert saved.config_snapshot["failed_paths"] == ["a.md", "b.md"]


def test_completed_calls_are_durable_before_document_finishes_and_resume_budget(monkeypatch, tmp_path):
    from interview_intelligence import worker
    from interview_intelligence.config import Settings
    from sqlalchemy import select
    db = create_database("sqlite+pysqlite:///:memory:")
    with db.session() as session:
        with session.begin():
            run = PipelineRun(pipeline_version="v1", extractor_version="v1", taxonomy_version="v1",
                              embedding_version="v1", config_snapshot={"paths": ["a.md"]}, status="RUNNING")
            session.add(run)
        run_id = run.id

    class Adapter:
        version = "test-v1"
        prompt_hash = "test"
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    for name in ("OpenAICompatibleEncoder", "OpenAICompatibleExtractor", "OpenAICompatibleJudge"):
        monkeypatch.setattr(worker, name, Adapter)
    monkeypatch.setattr(worker, "ElasticsearchRetriever", lambda *args, **kwargs: object())
    settings = Settings(database_url="sqlite+pysqlite:///:memory:", corpus_root=tmp_path,
                        model_api_key="test", model_base_url="https://example.invalid/v3",
                        embedding_model="embedding-test", reranker_model=None,
                        max_model_calls=10, max_model_tokens=10000)
    ingestor, _, calls = worker.build_worker_services(db, settings, run_id=run_id)
    ingestor.extractor.on_call({"operation_type": "EXTRACTION", "model": "test",
                               "prompt_version": "v1", "input_tokens": 50, "output_tokens": 20,
                               "latency_ms": 100, "status": "SUCCEEDED", "retry_count": 0,
                               "error_code": None})
    assert calls == []
    with db.session() as session:
        assert session.scalar(select(ModelCall).where(ModelCall.run_id == run_id)).input_tokens == 50
        assert session.get(PipelineRun, run_id).input_tokens == 50
    resumed, _, _ = worker.build_worker_services(db, settings, run_id=run_id)
    assert resumed.extractor.budget.used_calls == 1
    assert resumed.extractor.budget.used_tokens == 70
