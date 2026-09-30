import hashlib

import pytest

from interview_intelligence.domain.models import (
    CanonicalQuestion, DocumentBuild, EmbeddingCache, Interview,
    QuestionOccurrence, SourceDocument, SourceRevision, create_database,
)
from interview_intelligence.search.elasticsearch import rebuild_index


QUESTION = "Redis为什么快？"


def corpus_with_cache(*, version="test-vector:2", dimension=2, vector=None):
    database = create_database("sqlite+pysqlite:///:memory:")
    with database.session() as session, session.begin():
        canonical = CanonicalQuestion(canonical_text=QUESTION, primary_topic_id="redis.performance",
                                      taxonomy_version="v1", question_type="PRINCIPLE")
        source = SourceDocument(source_identity="cache-test", source_type="file",
                                original_relative_path="cache-test.md")
        session.add_all([canonical, source])
        session.flush()
        revision = SourceRevision(source_document_id=source.id, raw_file_hash="a" * 64,
                                  snapshot_path="snapshot.md", raw_file_path="cache-test.md")
        session.add(revision)
        session.flush()
        build = DocumentBuild(source_revision_id=revision.id, processing_fingerprint="v1",
                              document_kind="INTERVIEW_REPORT", decision="INCLUDED",
                              processing_state="READY", publication_state="ACTIVE")
        session.add(build)
        session.flush()
        source.active_build_id = build.id
        interview = Interview(build_id=build.id, session_key="round", session_order=1,
                              session_kind="SINGLE", analytics_eligible=True)
        session.add(interview)
        session.flush()
        session.add(QuestionOccurrence(
            interview_id=interview.id, canonical_question_id=canonical.id,
            raw_question=QUESTION, normalized_question=QUESTION, question_order=1,
            source_spans=[{"quote": QUESTION}], topic_id="redis.performance",
            taxonomy_version="v1", question_type="PRINCIPLE", confidence=0.9,
        ))
        session.add(EmbeddingCache(
            text_hash=hashlib.sha256(QUESTION.encode("utf-8")).hexdigest(),
            embedding_version=version, dimension=dimension,
            vector=[0.6, 0.8] if vector is None else vector,
        ))
    return database


class CountingEncoder:
    dimension = 2
    version = "test-vector:2"

    def __init__(self, vector=None):
        self.calls = []
        self.vector = [0.8, 0.6] if vector is None else vector

    def embed(self, text):
        self.calls.append(text)
        return self.vector


class RecordingRetriever:
    alias = "cache_rebuild_test"

    def __init__(self, encoder):
        self.encoder = encoder
        self.calls = []
        self.documents = []

    def _request(self, method, path, **kwargs):
        self.calls.append((method, path))
        if "/_doc/" in path:
            self.documents.append(kwargs["json"])
        if path.endswith("/_count"):
            return {"count": len(self.documents)}
        return {}


def test_rebuild_reuses_exact_cached_embedding_without_model_call():
    database = corpus_with_cache()
    encoder = CountingEncoder()
    retriever = RecordingRetriever(encoder)

    assert rebuild_index(database, retriever) == 0
    assert encoder.calls == []
    assert retriever.documents[0]["embedding"] == [0.6, 0.8]
    assert retriever.documents[0]["embedding_version"] == encoder.version


@pytest.mark.parametrize("cache", [
    {"version": "old-vector:2"},
    {"dimension": 3, "vector": [0.5, 0.5, 0.5]},
    {"vector": [0.5]},
    {"vector": [0.0, 0.0]},
    {"vector": [float("nan"), 1.0]},
])
def test_rebuild_embeds_when_cache_is_incompatible_or_invalid(cache):
    database = corpus_with_cache(**cache)
    encoder = CountingEncoder()
    retriever = RecordingRetriever(encoder)

    rebuild_index(database, retriever)
    assert encoder.calls == [QUESTION]
    assert retriever.documents[0]["embedding"] == [0.8, 0.6]


def test_rebuild_does_not_publish_invalid_generated_embedding():
    database = corpus_with_cache(version="old-vector:2")
    retriever = RecordingRetriever(CountingEncoder(vector=[0.0, 0.0]))

    with pytest.raises(ValueError, match="INVALID_INDEX_EMBEDDING"):
        rebuild_index(database, retriever)
    assert retriever.documents == []
    assert ("post", "/_aliases") not in retriever.calls
