"""Validate a source-reviewed amendment against an offline facts snapshot.

No provider, Settings, service or database URL is accepted. This prepares a local
candidate and SQL evidence, never changes the source snapshot or deployed corpus.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from interview_intelligence.analytics.semantic_audit import build_amendment, project_amendment
from interview_intelligence.analytics.semantic_scope import TASK, _digest, load_semantic_scope
from interview_intelligence.analytics.listing import ListRequest, list_questions
from interview_intelligence.analytics.counts import count_questions
from interview_intelligence.contracts import FilterSpec
from interview_intelligence.ingestion.snapshot import decode_source


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save(path, value):
    # An exclusive create prevents replacing an earlier candidate/evidence file.
    with Path(path).open("x", encoding="utf-8", newline="\n") as target:
        json.dump(value, target, ensure_ascii=False, indent=2, allow_nan=False)
        target.write("\n")
        target.flush()
        os.fsync(target.fileno())


def hydrate(snapshot):
    """Restore frozen exported columns; materialize only local source locations."""
    from sqlalchemy import Date, DateTime
    from interview_intelligence.domain.models import (create_database, SourceDocument,
        SourceRevision, DocumentBuild, Interview, CanonicalQuestion, QuestionOccurrence,
        OccurrenceTaskAnnotation, CorpusState)

    snapshot = Path(snapshot).resolve()
    facts = read(snapshot / "facts.json")
    db = create_database("sqlite+pysqlite:///:memory:")
    source_paths, source_hashes = {}, {}
    def entity(model, row):
        values = {}
        for col in model.__table__.columns:
            if col.name not in row:
                continue
            value = row[col.name]
            if isinstance(value, str) and isinstance(col.type, DateTime):
                value = datetime.fromisoformat(value)
            elif isinstance(value, str) and isinstance(col.type, Date):
                value = date.fromisoformat(value)
            values[col.name] = value
        return model(**values)
    with db.session() as session, session.begin():
        for doc in facts["documents"]:
            path = (snapshot / doc["source_path"]).resolve()
            if not path.is_relative_to(snapshot):
                raise ValueError("SNAPSHOT_SOURCE_OUTSIDE_ROOT")
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != doc["source_hash"]:
                raise ValueError("SNAPSHOT_SOURCE_HASH_MISMATCH")
            source_paths[doc["revision_id"]] = path
            source_hashes[doc["source_path"]] = doc["source_hash"]
            session.add(SourceDocument(id=doc["id"], source_identity="offline:" + doc["id"],
                source_type="MARKDOWN", original_relative_path=doc["path"], aliases=[]))
        session.flush()
        for doc in facts["documents"]:
            path = source_paths[doc["revision_id"]]
            session.add(SourceRevision(id=doc["revision_id"], source_document_id=doc["id"],
                raw_file_hash=doc["source_hash"], snapshot_path=str(path), raw_file_path=str(path),
                decoded_text_hash=hashlib.sha256(decode_source(path.read_bytes()).encode()).hexdigest()))
        session.flush()
        for doc in facts["documents"]:
            session.add(DocumentBuild(id=doc["build_id"], source_revision_id=doc["revision_id"],
                processing_fingerprint=doc["processing_fingerprint"], document_kind=doc["document_kind"],
                decision=doc["decision"], processing_state="COMPLETE", publication_state="PUBLISHED"))
        session.flush()
        for doc in facts["documents"]:
            session.get(SourceDocument, doc["id"]).active_build_id = doc["build_id"]
        session.add_all(entity(CanonicalQuestion, row) for row in facts["canonicals"])
        session.add_all(entity(Interview, row) for row in facts["interviews"])
        session.flush()
        session.add_all(entity(QuestionOccurrence, row) for row in facts["questions"])
        session.flush()
        session.add_all(entity(OccurrenceTaskAnnotation, row["task_annotation"])
                        for row in facts["questions"] if row.get("task_annotation"))
        state = session.get(CorpusState, 1)
        state.current_revision = facts["snapshot"]["corpus_revision"]
        state.indexed_revision = facts["snapshot"]["indexed_revision"]
        state.task_annotation_revision = facts["snapshot"]["task_annotation_revision"]
        # The exporter omits canonical_revision; do not pretend to recover it.
        state.canonical_revision = 0
    return db, facts, source_paths, source_hashes


def sql_evidence(db, filters, as_of):
    with db.session() as session:
        scope = load_semantic_scope(session, filters)
        counts = count_questions(session, filters, occurrence_ids=scope.accepted_occurrence_ids)
        cursor, rows, pages = None, [], []
        while True:
            result = list_questions(session, ListRequest(**filters.model_dump(), semantic_task=TASK,
                page_size=13, cursor=cursor), signing_key="offline-projection", as_of=date.fromisoformat(as_of))
            rows.extend(result["data"])
            pages.append(result["meta"]["pagination"])
            cursor = result["meta"]["pagination"]["next_cursor"]
            if not cursor:
                break
        ids = [row["canonical_question_id"] for row in rows]
        if len(ids) != len(set(ids)) or len(ids) != counts["canonical_questions"]:
            raise ValueError("SQL_PAGINATION_COUNT_MISMATCH")
        return {"scope": scope.metadata(), "counts": counts, "rows": rows, "pages": pages,
                "accepted_occurrence_ids": list(scope.accepted_occurrence_ids)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--review-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        raise ValueError("OUTPUT_ALREADY_EXISTS")
    review = read(args.review_file)
    filters = FilterSpec.model_validate(review["filters"])
    def blocked(*args, **kwargs):
        raise RuntimeError("OFFLINE_NETWORK_FORBIDDEN")
    with patch.object(socket.socket, "connect", blocked), patch.object(socket, "create_connection", blocked):
        db, facts, paths, hashes = hydrate(args.snapshot)
        source_reader = lambda revision: paths[revision.id].read_bytes()
        before = sql_evidence(db, filters, facts["snapshot"]["as_of"])
        with db.session() as session:
            amendment = build_amendment(session, filters, review["reviews"], source_reader=source_reader,
                reviewer=review["reviewer"], reviewed_at=review["reviewed_at"])
        args.output.mkdir(parents=True)
        save(args.output / "amendment.json", amendment)
        save(args.output / "before-sql.json", before)
        receipt = project_amendment(db, amendment, source_reader=source_reader,
            before_commit=lambda result: save(args.output / "projection-receipt.json", result))
        after = sql_evidence(db, filters, facts["snapshot"]["as_of"])
        save(args.output / "after-sql.json", after)
        save(args.output / "manifest.json", {"schema": "offline_semantic_projection_evidence_v1",
            "source_snapshot": facts["snapshot"], "facts_sha256": _digest(facts),
            "facts_file_sha256": hashlib.sha256((args.snapshot / "facts.json").read_bytes()).hexdigest(),
            "source_files": hashes, "review_file_sha256": hashlib.sha256(args.review_file.read_bytes()).hexdigest(),
            "amendment_sha256": amendment["sha256"], "projection_status": receipt["status"],
            "canonical_revision_note": "Not exported by snapshot; local sentinel 0, unchanged by projection.",
            "model_calls": 0, "service_calls": 0, "production_changed": False,
            "limits": "In-memory candidate only; no shared-database publisher or runtime/index binding supplied."})
        db.engine.dispose()
    print(json.dumps({"status": "LOCAL_CANDIDATE_ONLY", "before": before["counts"], "after": after["counts"],
        "amendment_sha256": amendment["sha256"], "model_calls": 0, "service_calls": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
