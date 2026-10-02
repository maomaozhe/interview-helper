"""Isolated browser verification server; never writes the user's corpus or reviews."""
import hashlib
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "integration"))
os.environ.update(DATABASE_URL="sqlite+pysqlite:///:memory:", ELASTICSEARCH_URL="",
                  MODEL_API_KEY="", MODEL_BASE_URL="")

from test_analytics import seed_corpus
from interview_intelligence.api import create_app
from interview_intelligence.config import Settings
from interview_intelligence.domain.models import CorpusState, PipelineRun, SourceDocument, SourceRevision, create_database
from sqlalchemy import select
import uvicorn


class FixtureRetriever:
    def retrieve(self, query, eligible_ids, pipeline, top_k):
        return {"data": [{"canonical_question_id": value, "score": 0.7}
                         for value in eligible_ids[:top_k]], "meta": {"pipeline": pipeline}}


def main():
    with tempfile.TemporaryDirectory(prefix="interview-web-smoke-") as folder:
        root = Path(folder)
        (root / "snapshots").mkdir()
        (root / "corpus").mkdir()
        memory_db, _, _ = seed_corpus()
        database_path = root / "smoke.db"
        connection = memory_db.engine.raw_connection()
        with sqlite3.connect(database_path) as target:
            connection.driver_connection.backup(target)
        connection.close()
        memory_db.engine.dispose()
        database_url = f"sqlite+pysqlite:///{database_path.as_posix()}"
        db = create_database(database_url)
        with db.session() as session:
            with session.begin():
                for revision, document in session.execute(select(SourceRevision, SourceDocument)
                        .join(SourceDocument, SourceRevision.source_document_id == SourceDocument.id)):
                    raw = f'# 验证面经\n\nRedis为什么快？\n<script>alert("escaped")</script>\n'.encode()
                    digest = hashlib.sha256(raw).hexdigest()
                    revision.raw_file_hash = digest
                    (root / "snapshots" / f"{digest}.md").write_bytes(raw)
                    (root / "corpus" / document.original_relative_path).write_bytes(raw)
                (root / "corpus" / "OOM待导入.md").write_text("# OOM 面经\n发生 OOM 如何排查？", encoding="utf-8")
                session.get(CorpusState, 1).current_revision = 1
                session.get(CorpusState, 1).indexed_revision = 1
                session.add(PipelineRun(pipeline_version="v1", extractor_version="v1", taxonomy_version="v1",
                    embedding_version="v1", status="PARTIAL_FAILURE", processed_documents=1, failed_documents=1,
                    config_snapshot={"paths": ["1.md", "OOM待导入.md"], "failed_paths": ["OOM待导入.md"],
                                     "last_error": "ExampleImportError"}))
        settings = Settings(database_url=database_url, corpus_root=root / "corpus",
                            snapshot_root=root / "snapshots", local_user_id="isolated-web-smoke")
        try:
            uvicorn.run(create_app(db, settings, FixtureRetriever()), host="127.0.0.1", port=8011, log_level="warning")
        finally:
            db.engine.dispose()


if __name__ == "__main__":
    main()
