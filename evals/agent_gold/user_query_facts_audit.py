"""Read-only post-replay audit of frozen r293 facts, source bytes and actor writes.

This module makes no API/model/Elasticsearch calls and never imports the API app.
The live CLI requires PostgreSQL and an eval-prefixed runtime-sealed local actor.
An offline comparison is explicitly labelled OFFLINE_REFERENCE_MATCH, not AFTER.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from eval.common import digest, inside, read_json

COUNTS = {"questions": 2771, "canonicals": 2452, "documents": 184, "interviews": 187}
SNAPSHOT = {"corpus_revision": 293, "indexed_revision": 293,
            "task_annotation_revision": 30, "as_of": "2026-10-05",
            "task_annotation_policy": "KNOWN"}
FACTS_SHA256 = "0b8fd1b94127c90f0c0c463295c1dc8045184d73eb8475bb91f6eca88c67501a"


class AuditError(ValueError):
    """Only fixed, credential-free error codes may cross the CLI boundary."""


def require(condition, code):
    if not condition:
        raise AuditError(code)


def load_reference(dataset):
    manifest = read_json(dataset / "manifest.json")
    freeze = read_json(dataset / "freeze.json")
    require(isinstance(freeze.get("files"), dict), "FREEZE_FILE_LIST_INVALID")
    for name, expected in freeze["files"].items():
        path = inside(dataset, name)
        require(path.is_file() and digest(path.read_bytes()) == expected, "FROZEN_FILE_CHANGED")
    require("manifest.json" in freeze["files"], "MANIFEST_NOT_FROZEN")
    facts_path = inside(dataset, manifest["source_facts_path"])
    require(manifest["source_facts_path"] in freeze["files"], "FACTS_NOT_FROZEN")
    facts = read_json(facts_path)
    require(digest(facts) == manifest["facts_sha256"] == FACTS_SHA256, "FROZEN_FACTS_DIGEST_MISMATCH")
    require(digest(facts_path.read_bytes()) == manifest["facts_file_sha256"], "FROZEN_FACTS_BYTES_MISMATCH")
    require(facts["snapshot"] == manifest["snapshot"] == SNAPSHOT, "EXPECTED_R293_SNAPSHOT_MISMATCH")
    require(all(len(facts[name]) == count for name, count in COUNTS.items()), "EXPECTED_R293_COUNTS_MISMATCH")
    return facts, {"facts_sha256": digest(facts), "facts_file_sha256": digest(facts_path.read_bytes()),
                   "manifest_sha256": digest((dataset / "manifest.json").read_bytes()),
                   "freeze_sha256": digest((dataset / "freeze.json").read_bytes()),
                   "frozen_files_verified": len(freeze["files"])}


def compare_table(expected, actual):
    """Compare complete rows, never reducing equality to counts or selected fields."""
    def indexed(rows):
        require(all(isinstance(row, dict) and isinstance(row.get("id"), str) for row in rows),
                "CORE_ROW_ID_INVALID")
        ids = [row["id"] for row in rows]
        require(len(ids) == len(set(ids)), "DUPLICATE_CORE_ROW_ID")
        return {row["id"]: row for row in rows}
    left, right = indexed(expected), indexed(actual)
    missing, added = sorted(left.keys() - right.keys()), sorted(right.keys() - left.keys())
    changed = []
    for key in sorted(left.keys() & right.keys()):
        if left[key] != right[key]:
            changed.append({"id": key, "changed_fields": sorted(
                k for k in left[key].keys() | right[key].keys()
                if k not in left[key] or k not in right[key] or left[key][k] != right[key][k]),
                "expected_row_sha256": digest(left[key]), "actual_row_sha256": digest(right[key])})
    return {"count": len(expected), "current_count": len(actual),
            "match": not missing and not added and not changed,
            "expected_sha256": digest(sorted(expected, key=lambda row: row["id"])),
            "actual_sha256": digest(sorted(actual, key=lambda row: row["id"])),
            "missing_ids": missing, "added_ids": added, "changed_rows": changed}


def compare_facts(expected, actual):
    tables = {name: compare_table(expected[name], actual[name]) for name in COUNTS}
    # Relations have no exported ID; retain multiplicity and complete evidence spans.
    ordered = lambda rows: sorted(rows, key=lambda row: digest(row))
    relations = {"expected_count": len(expected["followups"]), "actual_count": len(actual["followups"]),
                 "expected_sha256": digest(ordered(expected["followups"])),
                 "actual_sha256": digest(ordered(actual["followups"]))}
    relations["match"] = relations["expected_sha256"] == relations["actual_sha256"]
    snapshot = {"expected": expected["snapshot"], "actual": actual["snapshot"],
                "match": expected["snapshot"] == actual["snapshot"]}
    return {"tables": tables, "followups": relations, "snapshot": snapshot,
            "match": all(table["match"] for table in tables.values()) and relations["match"] and snapshot["match"],
            "excluded_from_core_comparison": ["physical_indices (index alias metadata; no Elasticsearch calls)"]}


def verify_sources(documents, root, *, exported):
    failures = []
    verified = []
    for row in sorted(documents, key=lambda row: row["id"]):
        source_hash = row["source_hash"]
        relative = row["source_path"] if exported else source_hash + ".md"
        path = inside(root, relative)
        actual = digest(path.read_bytes()) if path.is_file() else None
        verified.append({"id": row["id"], "source_hash": source_hash, "actual_sha256": actual})
        if actual != source_hash:
            failures.append({"document_id": row["id"], "source_hash": source_hash,
                             "reason": "MISSING" if actual is None else "HASH_MISMATCH"})
    return {"count": len(documents), "verified": len(documents) - len(failures),
            "match": not failures, "failures": failures, "verification_sha256": digest(verified)}


def check_actor(actor, seal, settings):
    require(isinstance(actor, str) and actor.startswith("eval-"), "EVALUATION_ACTOR_REQUIRED")
    require(seal.get("actor") == actor, "ACTOR_SEAL_MISMATCH")
    require(settings.local_user_id == actor, "ACTOR_SETTINGS_MISMATCH")
    require(settings.multi_tenant_enabled is False, "DEDICATED_LOCAL_ACTOR_REQUIRED")
    require(seal.get("as_of") == SNAPSHOT["as_of"], "RUNTIME_AS_OF_MISMATCH")


@contextmanager
def readonly_session(engine):
    """Server-enforced read-only, one repeatable snapshot, always rollback.

    SQLite support exists only for local safety tests; the live CLI rejects it.
    """
    postgres = engine.dialect.name == "postgresql"
    require(postgres or engine.dialect.name == "sqlite", "UNSUPPORTED_DATABASE_DIALECT")
    options = {"isolation_level": "REPEATABLE READ"} if postgres else {}
    with engine.connect().execution_options(**options) as connection:
        transaction = connection.begin()
        try:
            if postgres:
                connection.execute(text("SET TRANSACTION READ ONLY"))
                connection.execute(text("SET LOCAL statement_timeout = '30s'"))
                require(connection.scalar(text("SHOW transaction_read_only")) == "on", "READ_ONLY_NOT_ENFORCED")
                require(connection.scalar(text("SHOW transaction_isolation")) == "repeatable read",
                        "REPEATABLE_READ_NOT_ENFORCED")
            else:
                connection.execute(text("PRAGMA query_only=ON"))
            with Session(bind=connection, autoflush=False, expire_on_commit=False) as session:
                yield session
        finally:
            if transaction.is_active:
                transaction.rollback()


def collect_live(session, settings, *, actor, as_of):
    from interview_intelligence.domain.models import (CanonicalQuestion, CorpusState, DocumentBuild,
        IdempotencyReceipt, Interview, OccurrenceTaskAnnotation, QuestionRelation, ReviewEvent,
        SourceDocument, SourceRevision, UserQuestionState, UserRevision)
    from interview_intelligence.repository.corpus import active_occurrences
    from interview_intelligence.taxonomy import load_taxonomy

    def record(entity):
        return {column.name: value.isoformat() if hasattr(value, "isoformat") else value
                for column in entity.__table__.columns for value in [getattr(entity, column.name)]}
    state = session.get(CorpusState, 1)
    require(state is not None, "CORPUS_STATE_MISSING")
    snapshot = {"corpus_revision": state.current_revision, "indexed_revision": state.indexed_revision,
                "task_annotation_revision": state.task_annotation_revision, "as_of": as_of,
                "task_annotation_policy": settings.task_annotation_policy}
    builds = list(session.execute(select(SourceDocument, DocumentBuild, SourceRevision)
        .join(DocumentBuild, SourceDocument.active_build_id == DocumentBuild.id)
        .join(SourceRevision, DocumentBuild.source_revision_id == SourceRevision.id)))
    documents = [{"id": doc.id, "path": doc.original_relative_path, "source_hash": revision.raw_file_hash,
                  "revision_id": revision.id, "build_id": build.id, "decision": build.decision,
                  "document_kind": build.document_kind, "source_path": "sources/" + revision.raw_file_hash + ".md",
                  "processing_fingerprint": build.processing_fingerprint} for doc, build, revision in builds]
    interviews = [record(i) for i in session.scalars(select(Interview).where(
        Interview.build_id.in_([b.id for _, b, _ in builds])))]
    taxonomy = load_taxonomy()
    questions = []
    for question in active_occurrences(session):
        row = record(question)
        row["topic_l1"], row["topic_l2"] = taxonomy.labels(question.topic_id)
        annotation = session.get(OccurrenceTaskAnnotation, question.id)
        row["task_annotation"] = record(annotation) if annotation else None
        questions.append(row)
    canonical_ids = {q["canonical_question_id"] for q in questions}
    occurrence_ids = {q["id"] for q in questions}
    canonicals = [record(c) for c in session.scalars(select(CanonicalQuestion).where(
        CanonicalQuestion.id.in_(canonical_ids)))]
    followups = [{"source_id": row.source_occurrence_id, "target_id": row.target_occurrence_id,
                  "evidence_spans": row.evidence_spans}
                 for row in session.scalars(select(QuestionRelation).where(
                     QuestionRelation.relation_type == "OBSERVED_FOLLOWUP"))
                 if row.source_occurrence_id in occurrence_ids and row.target_occurrence_id in occurrence_ids]
    actor_counts = {"user_question_state": session.scalar(select(func.count()).select_from(UserQuestionState).where(
        UserQuestionState.user_id == actor)), "review_event": session.scalar(select(func.count()).select_from(
        ReviewEvent).where(ReviewEvent.user_id == actor))}
    actor_counts["match_zero"] = actor_counts["user_question_state"] == actor_counts["review_event"] == 0
    revision = session.get(UserRevision, actor)
    actor_counts["state_revision"] = revision.state_revision if revision else None
    actor_counts["idempotency_receipts"] = session.scalar(select(func.count()).select_from(IdempotencyReceipt).where(
        IdempotencyReceipt.actor_id == actor))
    return {"snapshot": snapshot, "documents": documents, "interviews": interviews,
            "questions": questions, "canonicals": canonicals, "followups": followups}, actor_counts


def audit(args):
    expected, reference = load_reference(args.dataset)
    seal = read_json(args.runtime_seal)
    actor = seal.get("actor")
    require(isinstance(actor, str) and actor.startswith("eval-"), "EVALUATION_ACTOR_REQUIRED")
    require(seal.get("as_of") == SNAPSHOT["as_of"], "RUNTIME_AS_OF_MISMATCH")
    report = {"schema": "user_query_facts_audit_v1", "created_at": datetime.now(timezone.utc).isoformat(),
              "script_sha256": digest(Path(__file__).read_bytes()), "reference": reference,
              "runtime_seal_sha256": digest(args.runtime_seal.read_bytes()), "actor": actor,
              "mode": "OFFLINE_REFERENCE_COMPARISON" if args.offline_facts else "LIVE_AFTER_RUN_READ_ONLY",
              "frozen_sources": verify_sources(expected["documents"], args.dataset, exported=True),
              "database_writes_by_auditor": 0, "external_calls": 0}
    if args.before_snapshot:
        before_path = args.before_snapshot / "facts.json"
        report["before_snapshot"] = compare_facts(expected, read_json(before_path))
        report["before_snapshot"]["facts_file_sha256"] = digest(before_path.read_bytes())
        report["before_snapshot"]["sources"] = verify_sources(expected["documents"], args.before_snapshot, exported=True)
    if args.before_core_check:
        before = read_json(args.before_core_check)
        require(all(before.get(name, {}).get("count") == count and
                    before[name].get("current_count") == count and before[name].get("match") is True and
                    before[name].get("archive_sha256") == before[name].get("current_sha256")
                    for name, count in COUNTS.items()), "BEFORE_CORE_CHECK_INVALID")
        report["before_core_check"] = {"file_sha256": digest(args.before_core_check.read_bytes()),
            "counts_verified": COUNTS, "legacy_hashes_preserved": {name: before[name].get("archive_sha256") for name in COUNTS},
            "note": "Legacy digest encoding is unspecified; full-row comparisons here use sorted IDs and eval.common.digest."}
    if args.offline_facts:
        actual = read_json(args.offline_facts)
        report["actual_facts_file_sha256"] = digest(args.offline_facts.read_bytes())
        report["actual_sources"] = verify_sources(actual["documents"], args.offline_facts.parent, exported=True)
        report["actor_write_check"] = {"status": "NOT_EXECUTED_OFFLINE"}
    else:
        from interview_intelligence.config import load_settings
        settings = load_settings()
        check_actor(actor, seal, settings)
        engine = create_engine(settings.database_url, poolclass=NullPool, hide_parameters=True)
        try:
            require(engine.dialect.name == "postgresql", "LIVE_AUDIT_REQUIRES_POSTGRESQL")
            with readonly_session(engine) as session:
                actual, report["actor_write_check"] = collect_live(session, settings, actor=actor, as_of=seal["as_of"])
                report["actual_sources"] = verify_sources(actual["documents"], settings.snapshot_root, exported=False)
            report["transaction"] = {"isolation": "REPEATABLE READ", "read_only_verified": True, "end": "ROLLBACK"}
        finally:
            engine.dispose()
    report["facts"] = compare_facts(expected, actual)
    matches = [report["facts"]["match"], report["actual_sources"]["match"], report["frozen_sources"]["match"]]
    if "before_snapshot" in report:
        matches.extend([report["before_snapshot"]["match"], report["before_snapshot"]["sources"]["match"]])
    if not args.offline_facts:
        matches.append(report["actor_write_check"]["match_zero"])
    report["passed"] = all(matches)
    report["status"] = ("OFFLINE_REFERENCE_MATCH" if args.offline_facts else "AFTER_RUN_PASSED") if report["passed"] else "MISMATCH"
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--runtime-seal", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--before-snapshot", type=Path)
    parser.add_argument("--before-core-check", type=Path)
    parser.add_argument("--offline-facts", type=Path, help="Compare an exported facts.json; does not query any database.")
    args = parser.parse_args()
    # Refuse to replace historical audit evidence before opening a connection.
    require(not args.output.exists(), "OUTPUT_ALREADY_EXISTS")
    try:
        report = audit(args)
    except Exception as exc:
        # Driver, config and OS exception strings can contain URLs/secrets or paths.
        report = {"schema": "user_query_facts_audit_v1", "status": "ERROR", "passed": False,
                  "error_code": str(exc) if isinstance(exc, AuditError) else "AUDIT_FAILED_" + type(exc).__name__,
                  "database_writes_by_auditor": 0, "external_calls": 0}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"status": report["status"], "passed": report["passed"],
                      "error_code": report.get("error_code"), "output": str(args.output)}, ensure_ascii=False))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
