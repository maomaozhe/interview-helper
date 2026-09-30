import hashlib
import importlib

import pytest
from sqlalchemy import func, select


def modules():
    return (
        importlib.import_module("interview_intelligence.domain.models"),
        importlib.import_module("interview_intelligence.ingestion.pipeline"),
        importlib.import_module("interview_intelligence.repository.corpus"),
    )


class CountingExtractor:
    version = "test-v1"

    def __init__(self):
        self.calls = 0
        self.fail = False

    def extract(self, *, text, revision_id):
        from interview_intelligence.contracts import ExtractionResult
        self.calls += 1
        if self.fail:
            raise TimeoutError("model timed out")
        question = text.split("\n")[-1]
        start = text.rindex(question)
        return ExtractionResult.model_validate({
            "schema_version": "v1", "document_kind": "INTERVIEW_REPORT",
            "interviews": [{
                "local_id": "first", "session_kind": "SINGLE",
                "metadata": {"company_raw": "字节", "position_raw": "Java后端", "round_raw": "一面"},
                "questions": [{
                    "local_id": "q1", "raw_question": question,
                    "normalized_question": question,
                    "source_spans": [{
                        "revision_id": revision_id, "start_char": start,
                        "end_char": start + len(question), "start_line": 5,
                        "end_line": 5, "quote": question, "origin": "TEXT",
                    }],
                    "evidence_kind": "INTERVIEW_QUESTION", "confidence": 0.95,
                }],
            }],
        })


def source_text(question):
    return f"# 字节Java一面\n- 作者：测试\n- 日期：08-16\n- 链接：https://www.xiaohongshu.com/explore/abc123\n{question}"


def test_repeated_ingest_never_duplicates_or_calls_extractor(tmp_path):
    models, pipeline, repository = modules()
    corpus = tmp_path / "md"
    corpus.mkdir()
    path = corpus / "post.md"
    path.write_text(source_text("Redis为什么快？"), encoding="utf-8")
    db = models.create_database(f"sqlite+pysqlite:///{tmp_path / 'test.db'}")
    extractor = CountingExtractor()
    service = pipeline.IngestService(db, corpus, tmp_path / "snapshots", extractor)
    assert service.ingest_file("post.md").status == "SUCCEEDED"
    assert service.ingest_file("post.md").status == "SKIPPED"
    assert extractor.calls == 1
    with db.session() as session:
        assert session.scalar(select(func.count(models.Interview.id))) == 1
        assert session.scalar(select(func.count(models.QuestionOccurrence.id))) == 1
        assert len(repository.active_occurrences(session)) == 1
        revision = session.scalar(select(models.SourceRevision))
        assert revision.raw_file_hash == hashlib.sha256(path.read_bytes()).hexdigest()
        assert (tmp_path / "snapshots" / f"{revision.raw_file_hash}.md").read_bytes() == path.read_bytes()


def test_changed_document_failure_keeps_old_active_build(tmp_path):
    models, pipeline, repository = modules()
    corpus = tmp_path / "md"
    corpus.mkdir()
    path = corpus / "post.md"
    path.write_text(source_text("Redis为什么快？"), encoding="utf-8")
    db = models.create_database(f"sqlite+pysqlite:///{tmp_path / 'test.db'}")
    extractor = CountingExtractor()
    service = pipeline.IngestService(db, corpus, tmp_path / "snapshots", extractor)
    service.ingest_file("post.md")
    path.write_text(source_text("Spring事务为什么失效？"), encoding="utf-8")
    extractor.fail = True
    with pytest.raises(TimeoutError):
        service.ingest_file("post.md")
    with db.session() as session:
        assert [q.raw_question for q in repository.active_occurrences(session)] == ["Redis为什么快？"]
        assert session.scalar(select(func.count(models.PipelineTask.id)).where(models.PipelineTask.state == "FAILED")) == 1


def test_same_post_at_another_path_is_an_alias(tmp_path):
    models, pipeline, _ = modules()
    corpus = tmp_path / "md"
    corpus.mkdir()
    data = source_text("Redis为什么快？")
    (corpus / "one.md").write_text(data, encoding="utf-8")
    (corpus / "copy.md").write_text(data, encoding="utf-8")
    db = models.create_database(f"sqlite+pysqlite:///{tmp_path / 'test.db'}")
    extractor = CountingExtractor()
    service = pipeline.IngestService(db, corpus, tmp_path / "snapshots", extractor)
    service.ingest_file("one.md")
    assert service.ingest_file("copy.md").status == "SKIPPED"
    with db.session() as session:
        assert session.scalar(select(func.count(models.SourceDocument.id))) == 1
        source = session.scalar(select(models.SourceDocument))
        assert source.aliases == ["copy.md"]


def test_invalid_model_span_is_never_published(tmp_path):
    models, pipeline, repository = modules()
    corpus = tmp_path / "md"
    corpus.mkdir()
    (corpus / "post.md").write_text(source_text("Redis为什么快？"), encoding="utf-8")
    db = models.create_database(f"sqlite+pysqlite:///{tmp_path / 'test.db'}")
    extractor = CountingExtractor()
    service = pipeline.IngestService(db, corpus, tmp_path / "snapshots", extractor)

    original_extract = extractor.extract

    def invalid_extract(**kwargs):
        result = original_extract(**kwargs)
        result.interviews[0].questions[0].source_spans[0].quote = "not in source"
        return result

    extractor.extract = invalid_extract
    with pytest.raises(ValueError, match="span"):
        service.ingest_file("post.md")
    with db.session() as session:
        assert repository.active_occurrences(session) == []


def test_span_must_reference_the_current_immutable_revision(tmp_path):
    models, pipeline, repository = modules()
    corpus = tmp_path / "md"
    corpus.mkdir()
    (corpus / "post.md").write_text(source_text("Redis为什么快？"), encoding="utf-8")
    db = models.create_database(f"sqlite+pysqlite:///{tmp_path / 'test.db'}")
    extractor = CountingExtractor()
    original = extractor.extract

    def wrong_revision(**kwargs):
        result = original(**kwargs)
        result.interviews[0].questions[0].source_spans[0].revision_id = "old-revision"
        return result

    extractor.extract = wrong_revision
    with pytest.raises(ValueError, match="span"):
        pipeline.IngestService(db, corpus, tmp_path / "snapshots", extractor).ingest_file("post.md")
    with db.session() as session:
        assert repository.active_occurrences(session) == []


def test_model_topic_must_be_existing_taxonomy_leaf(tmp_path):
    _, pipeline, _ = modules()
    corpus = tmp_path / "md"
    corpus.mkdir()
    (corpus / "post.md").write_text(source_text("Redis为什么快？"), encoding="utf-8")
    models, _, _ = modules()
    db = models.create_database(f"sqlite+pysqlite:///{tmp_path / 'test.db'}")
    extractor = CountingExtractor()
    real_extract = extractor.extract

    def invalid_topic(**kwargs):
        result = real_extract(**kwargs)
        result.interviews[0].questions[0].topic_l1 = "Redis"
        result.interviews[0].questions[0].topic_l2 = "高级缓存"
        return result

    extractor.extract = invalid_topic
    with pytest.raises(ValueError, match="taxonomy"):
        pipeline.IngestService(db, corpus, tmp_path / "snapshots", extractor).ingest_file("post.md")


def test_question_order_restarts_for_each_session(tmp_path):
    models, pipeline, _ = modules()
    corpus = tmp_path / "md"
    corpus.mkdir()
    text = "# 字节一面二面\nRedis为什么快？\nSpring事务为什么失效？"
    (corpus / "post.md").write_text(text, encoding="utf-8")
    db = models.create_database(f"sqlite+pysqlite:///{tmp_path / 'test.db'}")

    class MultiRoundExtractor:
        version = "test-multiround-v1"

        def extract(self, *, text, revision_id):
            from interview_intelligence.contracts import ExtractionResult
            interviews = []
            for local_id, round_raw, quote, line in (
                ("first", "一面", "Redis为什么快？", 2),
                ("second", "二面", "Spring事务为什么失效？", 3),
            ):
                start = text.index(quote)
                interviews.append({
                    "local_id": local_id, "session_kind": "SPECIFIC_ROUND",
                    "metadata": {"round_raw": round_raw},
                    "questions": [{
                        "local_id": "q1", "raw_question": quote, "normalized_question": quote,
                        "source_spans": [{"revision_id": revision_id, "start_char": start,
                                         "end_char": start + len(quote), "start_line": line,
                                         "end_line": line, "quote": quote, "origin": "TEXT"}],
                        "evidence_kind": "INTERVIEW_QUESTION", "confidence": 0.95,
                    }],
                })
            return ExtractionResult(schema_version="v1", document_kind="INTERVIEW_REPORT", interviews=interviews)

    service = pipeline.IngestService(db, corpus, tmp_path / "snapshots", MultiRoundExtractor())
    assert service.ingest_file("post.md").question_count == 2
    with db.session() as session:
        interviews = list(session.scalars(select(models.Interview).order_by(models.Interview.session_order)))
        assert [item.round for item in interviews] == ["FIRST", "SECOND"]
        assert [session.scalar(select(models.QuestionOccurrence.question_order).where(
            models.QuestionOccurrence.interview_id == item.id)) for item in interviews] == [1, 1]


def test_unknown_reextraction_requires_review_and_preserves_old_build(tmp_path):
    models, pipeline, repository = modules()
    corpus = tmp_path / "md"
    corpus.mkdir()
    path = corpus / "post.md"
    path.write_text(source_text("Redis为什么快？"), encoding="utf-8")
    db = models.create_database(f"sqlite+pysqlite:///{tmp_path / 'test.db'}")
    extractor = CountingExtractor()
    service = pipeline.IngestService(db, corpus, tmp_path / "snapshots", extractor)
    first = service.ingest_file("post.md")
    path.write_text(source_text("内容难以判定"), encoding="utf-8")

    def unknown(**kwargs):
        from interview_intelligence.contracts import ExtractionResult
        return ExtractionResult(schema_version="v1", document_kind="UNKNOWN")

    extractor.extract = unknown
    second = service.ingest_file("post.md")
    assert second.status == "NEEDS_REVIEW"
    assert second.corpus_revision == first.corpus_revision
    with db.session() as session:
        assert [item.raw_question for item in repository.active_occurrences(session)] == ["Redis为什么快？"]


def test_prompt_hash_change_forces_new_build_even_with_same_model_name(tmp_path):
    models, pipeline, _ = modules()
    corpus = tmp_path / "md"
    corpus.mkdir()
    (corpus / "post.md").write_text(source_text("Redis为什么快？"), encoding="utf-8")
    db = models.create_database(f"sqlite+pysqlite:///{tmp_path / 'test.db'}")
    extractor = CountingExtractor()
    extractor.prompt_hash = "a" * 64
    service = pipeline.IngestService(db, corpus, tmp_path / "snapshots", extractor)
    first = service.ingest_file("post.md")
    extractor.prompt_hash = "b" * 64
    second = service.ingest_file("post.md")
    assert extractor.calls == 2
    assert second.corpus_revision == first.corpus_revision + 1
