"""Export current published facts in one read transaction; never mutate corpus."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from sqlalchemy import select

from eval.common import digest, provenance, write_json
from eval.validate_gold import require


def today():
    return datetime.now(timezone(timedelta(hours=8))).date().isoformat()


def current_snapshot(database, settings, *, as_of=None):
    from interview_intelligence.domain.models import CorpusState
    with database.session() as session:
        state = session.get(CorpusState, 1)
        require(state is not None, "corpus state missing")
        return {"corpus_revision": state.current_revision, "indexed_revision": state.indexed_revision,
                "task_annotation_revision": state.task_annotation_revision, "as_of": as_of or today(),
                "task_annotation_policy": settings.task_annotation_policy}


def assert_snapshot(expected, actual):
    require(all(actual.get(k) == value for k, value in expected.items()), "SNAPSHOT_CHANGED")


def export_snapshot(database, settings, output: Path, *, as_of=None):
    from interview_intelligence.domain.models import (CanonicalQuestion, CorpusState, DocumentBuild, Interview,
        OccurrenceTaskAnnotation, QuestionRelation, SourceDocument, SourceRevision)
    from interview_intelligence.repository.corpus import active_occurrences
    from interview_intelligence.taxonomy import load_taxonomy
    output.mkdir(parents=True, exist_ok=False)
    def record(entity):
        return {column.name: value.isoformat() if hasattr(value, "isoformat") else value
                for column in entity.__table__.columns for value in [getattr(entity, column.name)]}
    taxonomy = load_taxonomy()
    with database.session() as session:
        if session.bind.dialect.name == "postgresql":
            session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        state = session.get(CorpusState, 1)
        require(state is not None, "corpus state missing")
        snapshot = {"corpus_revision": state.current_revision, "indexed_revision": state.indexed_revision,
                    "task_annotation_revision": state.task_annotation_revision, "as_of": as_of or today(),
                    "task_annotation_policy": settings.task_annotation_policy}
        documents = []
        builds = list(session.execute(select(SourceDocument, DocumentBuild, SourceRevision)
            .join(DocumentBuild, SourceDocument.active_build_id == DocumentBuild.id)
            .join(SourceRevision, DocumentBuild.source_revision_id == SourceRevision.id)))
        for document, build, revision in builds:
            raw = settings.snapshot_root / (revision.raw_file_hash + ".md")
            require(raw.is_file() and digest(raw.read_bytes()) == revision.raw_file_hash, "source snapshot missing/corrupt")
            target = output / "sources" / raw.name
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(raw.read_bytes())
            documents.append({"id": document.id, "path": document.original_relative_path,
                              "source_hash": revision.raw_file_hash, "revision_id": revision.id,
                              "build_id": build.id, "decision": build.decision, "document_kind": build.document_kind,
                              "source_path": "sources/" + raw.name, "processing_fingerprint": build.processing_fingerprint})
        interviews = [record(i) for i in session.scalars(select(Interview).where(Interview.build_id.in_([b.id for _, b, _ in builds])))]
        questions = []
        for question in active_occurrences(session):
            row = record(question)
            row["topic_l1"], row["topic_l2"] = taxonomy.labels(question.topic_id)
            annotation = session.get(OccurrenceTaskAnnotation, question.id)
            row["task_annotation"] = record(annotation) if annotation else None
            questions.append(row)
        ids = {q["canonical_question_id"] for q in questions}
        occurrence_ids = {q["id"] for q in questions}
        followups = [{"source_id": f.source_occurrence_id, "target_id": f.target_occurrence_id,
                      "evidence_spans": f.evidence_spans}
                    for f in session.scalars(select(QuestionRelation).where(QuestionRelation.relation_type == "OBSERVED_FOLLOWUP"))
                    if f.source_occurrence_id in occurrence_ids and f.target_occurrence_id in occurrence_ids]
        canonicals = [record(c) for c in session.scalars(select(CanonicalQuestion).where(CanonicalQuestion.id.in_(ids)))]
    physical = []
    if settings.elasticsearch_url:
        with httpx.Client(base_url=settings.elasticsearch_url, timeout=10, trust_env=False) as client:
            response = client.get("/_alias/interview_questions")
            response.raise_for_status()
            physical = sorted(response.json())
    assert_snapshot(snapshot, current_snapshot(database, settings, as_of=snapshot["as_of"]))
    facts = {"snapshot": snapshot, "documents": documents, "interviews": interviews,
             "questions": questions, "canonicals": canonicals, "followups": followups, "physical_indices": physical}
    write_json(output / "facts.json", facts)
    write_json(output / "manifest.json", {"kind": "published_corpus_snapshot", "snapshot": snapshot,
        "facts_sha256": digest(facts), "physical_indices": physical,
        "counts": {"sources": len(documents), "interviews": len(interviews), "questions": len(questions), "canonicals": len(canonicals)},
        "provenance": provenance()})
    return facts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--as-of")
    args = parser.parse_args()
    from interview_intelligence.config import load_settings
    from interview_intelligence.domain.models import create_database
    settings = load_settings()
    facts = export_snapshot(create_database(settings.database_url, create_tables=False), settings, args.output, as_of=args.as_of)
    print({"output": str(args.output), "snapshot": facts["snapshot"], "questions": len(facts["questions"])})


if __name__ == "__main__":
    main()
