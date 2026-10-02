import hashlib
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from interview_intelligence.contracts import ExtractedFollowup, ExtractionResult
from interview_intelligence.domain.models import (
    CanonicalQuestion, CorpusState, DocumentBuild, PipelineTask, StageArtifact, create_database,
)
from interview_intelligence.ingestion.pipeline import IngestService
from interview_intelligence.taxonomy import Taxonomy


class CountingExtractor:
    version = "cache-test-v1"
    model = "test-model"
    prompt_hash = "prompt-v1"
    resolved_model = "test-resolved-model"

    def __init__(self):
        self.calls = 0
        self.fail = False
        self.invalid = None
        self.cache_configuration = {"temperature": 0, "schema": "test-v1"}

    def extract(self, *, text, revision_id):
        self.calls += 1
        if self.fail:
            raise TimeoutError("extractor failed")
        quote = text.splitlines()[-1]
        start = text.rindex(quote)
        result = ExtractionResult.model_validate({
            "schema_version": "v1", "document_kind": "INTERVIEW_REPORT",
            "interviews": [{
                "local_id": "first", "session_kind": "SINGLE",
                "questions": [{
                    "local_id": "q1", "raw_question": quote, "normalized_question": quote,
                    "topic_l1": "Redis", "topic_l2": "性能优化", "question_type": "PRINCIPLE",
                    "source_spans": [{
                        "revision_id": revision_id, "start_char": start, "end_char": start + len(quote),
                        "start_line": text.count("\n", 0, start) + 1,
                        "end_line": text.count("\n", 0, start + len(quote) - 1) + 1,
                        "quote": quote, "origin": "TEXT",
                    }],
                    "evidence_kind": "INTERVIEW_QUESTION", "confidence": 0.95,
                }],
            }],
        })
        question = result.interviews[0].questions[0]
        if self.invalid == "span":
            question.source_spans[0].quote = "not present in source"
        elif self.invalid == "taxonomy":
            question.topic_l2 = "UNKNOWN"
        elif self.invalid == "schema":
            result.schema_version = "unsupported"
        elif self.invalid == "no_questions":
            question.evidence_kind = "ANSWER"
        elif self.invalid == "followup_order":
            second = question.model_copy(deep=True)
            second.local_id = "q2"
            result.interviews[0].questions.append(second)
            result.interviews[0].followups.append(ExtractedFollowup(
                source_local_id="q2", target_local_id="q1",
                evidence_spans=question.source_spans, confidence=0.9))
        elif self.invalid == "duplicate_sessions":
            result.interviews.append(result.interviews[0].model_copy(deep=True))
        return result


class SwitchableDeduper:
    def __init__(self):
        self.encoder = SimpleNamespace(version="vector-v1")
        self.judge = SimpleNamespace(version="judge-v1", model="test-model", prompt_hash="judge-prompt-v1")
        self.fail = True

    def resolve(self, session, question, topic_id, question_type):
        canonical = CanonicalQuestion(canonical_text=question, primary_topic_id=topic_id,
                                      taxonomy_version="v1", question_type=question_type)
        session.add(canonical)
        session.flush()
        if self.fail:
            raise RuntimeError("downstream failed")
        return SimpleNamespace(canonical=canonical, decision="NEW", candidate_ids=[], related_ids=[],
                               confidence=1.0, evidence={})


def setup(tmp_path):
    corpus = tmp_path / "md"
    corpus.mkdir()
    (corpus / "post.md").write_text("# Interview\nRedis为什么快？", encoding="utf-8")
    url = f"sqlite+pysqlite:///{tmp_path / 'test.db'}"
    db = create_database(url)
    extractor, deduper = CountingExtractor(), SwitchableDeduper()
    service = IngestService(db, corpus, tmp_path / "snapshots", extractor, deduper)
    return db, service, extractor, deduper, url


def fail_downstream(service):
    with pytest.raises(RuntimeError, match="downstream failed"):
        service.ingest_file("post.md")


def artifact_record(db):
    with db.session() as session:
        artifact = session.scalar(select(StageArtifact))
        assert artifact is not None
        return artifact


def artifact_path(tmp_path, artifact):
    return tmp_path / "processed" / "extract" / f"{artifact.cache_key}.json"


def test_downstream_failure_retains_validated_extraction_and_links_both_tasks(tmp_path):
    db, service, extractor, deduper, _ = setup(tmp_path)
    fail_downstream(service)
    with db.session() as session:
        assert session.scalar(select(func.count(StageArtifact.id))) == 1
        assert session.scalar(select(func.count(DocumentBuild.id))) == 0
        assert session.scalar(select(func.count(CanonicalQuestion.id))) == 0
        assert session.get(CorpusState, 1).current_revision == 0
        assert session.scalar(select(PipelineTask)).artifact_id is not None
    artifact = artifact_record(db)
    assert hashlib.sha256(artifact_path(tmp_path, artifact).read_bytes()).hexdigest() == artifact.output_hash
    deduper.fail = False
    assert service.ingest_file("post.md").status == "SUCCEEDED"
    assert extractor.calls == 1
    with db.session() as session:
        assert set(session.scalars(select(PipelineTask.artifact_id))) == {artifact.id}
        assert session.get(CorpusState, 1).current_revision == 1


def test_recreated_service_reuses_cache_and_preserves_resolved_model(tmp_path):
    db, service, _, _, url = setup(tmp_path)
    fail_downstream(service)
    db.engine.dispose()
    db = create_database(url)
    extractor, deduper = CountingExtractor(), SwitchableDeduper()
    extractor.fail = True
    extractor.resolved_model = None
    deduper.fail = False
    recreated = IngestService(db, tmp_path / "md", tmp_path / "snapshots", extractor, deduper)
    assert recreated.ingest_file("post.md").status == "SUCCEEDED"
    assert extractor.calls == 0
    with db.session() as session:
        build = session.scalar(select(DocumentBuild))
        assert build.config_snapshot["resolved_extraction_model"] == "test-resolved-model"


@pytest.mark.parametrize("changed", ["version", "model", "prompt_hash", "parameters", "schema", "source", "taxonomy"])
def test_extraction_inputs_invalidate_cache_and_completed_build(tmp_path, changed):
    db, service, extractor, deduper, _ = setup(tmp_path)
    deduper.fail = False
    assert service.ingest_file("post.md").status == "SUCCEEDED"
    if changed in {"version", "model", "prompt_hash"}:
        setattr(extractor, changed, "changed")
    elif changed == "parameters":
        extractor.cache_configuration["temperature"] = 0.5
    elif changed == "schema":
        extractor.cache_configuration["schema"] = "test-v2"
    elif changed == "source":
        (tmp_path / "md" / "post.md").write_text("# Interview\nRedis为什么单线程？", encoding="utf-8")
    elif changed == "taxonomy":
        topics = deepcopy(service.taxonomy.topics)
        topics["其他"]["新分类"] = "other.new"
        service.taxonomy = Taxonomy(version="v1", topics=topics)
    assert service.ingest_file("post.md").status == "SUCCEEDED"
    assert extractor.calls == 2
    with db.session() as session:
        assert session.scalar(select(func.count(StageArtifact.id))) == 2


def test_judge_change_reuses_extraction_but_rebuilds_assignments(tmp_path):
    db, service, extractor, deduper, _ = setup(tmp_path)
    deduper.fail = False
    first = service.ingest_file("post.md")
    deduper.judge.version = "judge-v2"
    second = service.ingest_file("post.md")
    assert second.status == "SUCCEEDED"
    assert second.corpus_revision == first.corpus_revision + 1
    assert extractor.calls == 1
    with db.session() as session:
        assert session.scalar(select(func.count(StageArtifact.id))) == 1


@pytest.mark.parametrize("damage", ["missing", "hash", "json", "span", "taxonomy", "schema"])
def test_invalid_cached_payload_is_reextracted_and_repaired(tmp_path, damage):
    db, service, extractor, deduper, _ = setup(tmp_path)
    fail_downstream(service)
    artifact = artifact_record(db)
    path = artifact_path(tmp_path, artifact)
    if damage == "missing":
        path.unlink()
    elif damage == "hash":
        path.write_text("changed bytes", encoding="utf-8")
    else:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if damage == "span":
            payload["result"]["interviews"][0]["questions"][0]["source_spans"][0]["revision_id"] = "wrong-revision"
        elif damage == "taxonomy":
            payload["result"]["interviews"][0]["questions"][0]["topic_l2"] = "UNKNOWN"
        elif damage == "schema":
            payload["result"]["schema_version"] = "unsupported"
        raw = b"{not json" if damage == "json" else json.dumps(payload, ensure_ascii=False).encode()
        path.write_bytes(raw)
        with db.session() as session, session.begin():
            session.get(StageArtifact, artifact.id).output_hash = hashlib.sha256(raw).hexdigest()
    deduper.fail = False
    assert service.ingest_file("post.md").status == "SUCCEEDED"
    assert extractor.calls == 2
    assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact_record(db).output_hash
    with db.session() as session:
        assert session.scalar(select(func.count(StageArtifact.id))) == 1


def test_cache_uses_current_root_instead_of_database_absolute_path(tmp_path):
    db, service, extractor, deduper, _ = setup(tmp_path)
    fail_downstream(service)
    artifact = artifact_record(db)
    outside = tmp_path / "do-not-read.json"
    outside.write_text("historical path is not trusted", encoding="utf-8")
    with db.session() as session, session.begin():
        session.get(StageArtifact, artifact.id).artifact_path = str(outside)
    deduper.fail = False
    assert service.ingest_file("post.md").status == "SUCCEEDED"
    assert extractor.calls == 1
    assert outside.read_text(encoding="utf-8") == "historical path is not trusted"


@pytest.mark.parametrize("failure", [
    "request", "span", "taxonomy", "schema", "no_questions", "followup_order", "duplicate_sessions",
])
def test_failed_or_invalid_extraction_is_never_cached(tmp_path, failure):
    db, service, extractor, deduper, _ = setup(tmp_path)
    deduper.fail = False
    extractor.fail = failure == "request"
    extractor.invalid = failure
    with pytest.raises((TimeoutError, ValueError)):
        service.ingest_file("post.md")
    with db.session() as session:
        assert session.scalar(select(func.count(StageArtifact.id))) == 0
        assert session.scalar(select(func.count(DocumentBuild.id))) == 0
    extractor.fail = False
    extractor.invalid = None
    assert service.ingest_file("post.md").status == "SUCCEEDED"
    assert extractor.calls == 2


def test_production_extractor_exposes_effective_request_schema_and_parameters():
    from interview_intelligence.extraction.provider import DraftResult, OpenAICompatibleExtractor
    extractor = OpenAICompatibleExtractor(client=SimpleNamespace(), model="test-model", stream=True)
    config = extractor.cache_configuration
    assert config["schema"] == DraftResult.model_json_schema()
    assert config["temperature"] == 0
    assert config["stream"] is True
    assert config["taxonomy"]["topics"] == extractor.taxonomy.topics


def test_transient_artifact_replace_denial_keeps_validated_extraction(tmp_path, monkeypatch):
    from interview_intelligence.ingestion import extraction_cache as cache
    original_replace = cache.os.replace
    denied = []

    def replace(source, target):
        if str(target).endswith(".json") and len(denied) < 2:
            denied.append(str(target))
            raise PermissionError("transient shared-filesystem denial")
        return original_replace(source, target)

    monkeypatch.setattr(cache.os, "replace", replace)
    db, service, extractor, deduper, _ = setup(tmp_path)
    fail_downstream(service)
    assert len(denied) == 2
    artifact = artifact_record(db)
    assert hashlib.sha256(artifact_path(tmp_path, artifact).read_bytes()).hexdigest() == artifact.output_hash
    deduper.fail = False
    assert service.ingest_file("post.md").status == "SUCCEEDED"
    assert extractor.calls == 1


def test_permanent_artifact_replace_denial_does_not_publish_or_claim_cached(tmp_path, monkeypatch):
    from interview_intelligence.ingestion import extraction_cache as cache
    original_replace = cache.os.replace
    denied = []

    def replace(source, target):
        if str(target).endswith(".json"):
            denied.append(str(target))
            raise PermissionError("permanent shared-filesystem denial")
        return original_replace(source, target)

    monkeypatch.setattr(cache.os, "replace", replace)
    db, service, _, deduper, _ = setup(tmp_path)
    deduper.fail = False
    with pytest.raises(PermissionError):
        service.ingest_file("post.md")
    assert len(denied) == 5
    with db.session() as session:
        assert session.scalar(select(func.count(StageArtifact.id))) == 0
        assert session.scalar(select(func.count(DocumentBuild.id))) == 0
    assert not list((tmp_path / "processed" / "extract").glob(".extract-*"))
