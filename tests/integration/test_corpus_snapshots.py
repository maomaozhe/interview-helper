import importlib


def test_republishing_source_replaces_active_occurrences_and_preserves_manifest():
    models = importlib.import_module("interview_intelligence.domain.models")
    repository = importlib.import_module("interview_intelligence.repository.corpus")
    db = models.create_database("sqlite+pysqlite:///:memory:")
    with db.session() as session:
        with session.begin():
            source = models.SourceDocument(source_identity="xhs:post-1", original_relative_path="one.md", source_type="xiaohongshu")
            canonical = models.CanonicalQuestion(canonical_text="Redis 为什么快？", primary_topic_id="redis.performance", taxonomy_version="v1", question_type="PRINCIPLE")
            session.add_all([source, canonical])
            session.flush()
            builds = []
            for index, question in enumerate(("Redis为什么快？", "Redis单线程为什么快？"), 1):
                revision = models.SourceRevision(source_document_id=source.id, raw_file_hash=str(index) * 64, snapshot_path=f"snapshots/{index}.md", raw_file_path="one.md")
                session.add(revision)
                session.flush()
                build = models.DocumentBuild(source_revision_id=revision.id, processing_fingerprint=f"extract-v{index}", document_kind="INTERVIEW_REPORT", processing_state="READY")
                session.add(build)
                session.flush()
                interview = models.Interview(build_id=build.id, session_key="first", session_order=1, session_kind="SINGLE", analytics_eligible=True)
                session.add(interview)
                session.flush()
                session.add(models.QuestionOccurrence(
                    interview_id=interview.id, canonical_question_id=canonical.id,
                    raw_question=question, normalized_question=question, question_order=1,
                    topic_id="redis.performance", taxonomy_version="v1", question_type="PRINCIPLE",
                    source_spans=[{"quote": question}], confidence=0.95,
                ))
                builds.append(build)
        with session.begin():
            first_revision = repository.publish_build(session, builds[0].id)
        assert [q.raw_question for q in repository.active_occurrences(session)] == ["Redis为什么快？"]
        session.rollback()  # The read opened a new SQLAlchemy transaction.
        with session.begin():
            second_revision = repository.publish_build(session, builds[1].id)
        assert second_revision == first_revision + 1
        assert [q.raw_question for q in repository.active_occurrences(session)] == ["Redis单线程为什么快？"]
        first_manifest = repository.manifest_at(session, first_revision)
        assert first_manifest["xhs:post-1"] == builds[0].id
        assert repository.manifest_at(session, second_revision)["xhs:post-1"] == builds[1].id


def test_same_build_published_twice_is_idempotent():
    models = importlib.import_module("interview_intelligence.domain.models")
    repository = importlib.import_module("interview_intelligence.repository.corpus")
    db = models.create_database("sqlite+pysqlite:///:memory:")
    with db.session() as session:
        with session.begin():
            source = models.SourceDocument(source_identity="path:one.md", original_relative_path="one.md", source_type="file")
            session.add(source)
            session.flush()
            revision = models.SourceRevision(source_document_id=source.id, raw_file_hash="a" * 64, snapshot_path="a.md", raw_file_path="one.md")
            session.add(revision)
            session.flush()
            build = models.DocumentBuild(source_revision_id=revision.id, processing_fingerprint="v1", document_kind="COMPILATION", decision="EXCLUDED", processing_state="READY")
            session.add(build)
        with session.begin():
            first = repository.publish_build(session, build.id)
        with session.begin():
            second = repository.publish_build(session, build.id)
        assert first == second == 1
        assert repository.active_occurrences(session) == []
