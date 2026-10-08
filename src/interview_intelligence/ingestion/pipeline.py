"""Staged extraction and atomic publication for a Markdown source."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

from sqlalchemy import select

from interview_intelligence.config import resolve_corpus_document
from interview_intelligence.contracts import ExtractionResult
from interview_intelligence.domain.models import (
    AlgorithmMatch, CanonicalAssignment, CanonicalQuestion, CorpusState, DocumentBuild, Interview,
    PipelineRun, PipelineTask, QuestionOccurrence, QuestionRelation,
    SourceDocument, SourceRevision, Database,
    now_utc, OccurrenceTaskAnnotation,
)
from interview_intelligence.ingestion.identity import source_identity
from interview_intelligence.ingestion.algorithm import parse_algorithm_match
from interview_intelligence.ingestion.snapshot import decode_source, save_snapshot
from interview_intelligence.ingestion.extraction_cache import ExtractionStageCache, extraction_config_hash
from interview_intelligence.repository.corpus import publish_build
from interview_intelligence.taxonomy import load_taxonomy


class Extractor(Protocol):
    version: str

    def extract(self, *, text: str, revision_id: str) -> ExtractionResult: ...


@dataclass(frozen=True)
class IngestOutcome:
    status: str
    run_id: str
    source_document_id: str
    corpus_revision: int
    question_count: int = 0
    excluded: bool = False


def _fingerprint(extractor_version: str) -> str:
    config = {"extractor": extractor_version, "taxonomy": "v1", "normalizer": "metadata_v2", "schema": "v1"}
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def _normalize_company(raw: str | None) -> str | None:
    if not raw:
        return None
    aliases = {"字节跳动": "字节", "抖音": "字节", "腾讯音乐": "腾讯音乐", "阿里云": "阿里云",
               "pdd": "拼多多", "boss": "BOSS直聘", "boss直聘": "BOSS直聘"}
    return aliases.get(raw.casefold(), raw)


def _normalize_position(raw: str | None) -> tuple[str | None, str | None, list[str]]:
    if not raw:
        return None, None, []
    languages = ["JAVA"] if re.search(r"java", raw, re.I) else []
    if any(term in raw for term in ("后端", "后台", "服务端")):
        return raw, "BACKEND", languages
    if any(term in raw for term in ("AI应用", "Agent开发", "大模型应用")):
        return raw, "AI_APPLICATION", languages
    if "算法" in raw:
        return raw, "ALGORITHM", languages
    return raw, "OTHER", languages


def _normalize_round(raw: str | None) -> str | None:
    if not raw:
        return None
    matches = {value for pattern, value in (
        (r"[一1]面", "FIRST"), (r"[二2]面", "SECOND"), (r"[三3]面", "THIRD"),
        (r"[四4五5]面", "FOURTH_PLUS"), (r"(?i)HR", "HR")) if re.search(pattern, raw)}
    # A multi-round title does not identify which round belongs to this session.
    return next(iter(matches)) if len(matches) == 1 else None


def _parse_full_date(raw: str | None) -> date | None:
    if not raw:
        return None
    match = re.search(r"(?<!\d)(20\d{2})[-./年](\d{1,2})[-./月](\d{1,2})(?:日)?(?!\d)", raw)
    if not match:
        match = re.search(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)", raw)
    if not match:
        return None
    try:
        return date(*map(int, match.groups()))
    except ValueError:
        return None


def _classify(question: str) -> tuple[str, str]:
    """Conservative test fallback; production model classifiers override this."""
    if "Redis" in question or "redis" in question:
        return "redis.performance", "PRINCIPLE"
    if "Spring" in question and "事务" in question:
        return "spring.transaction", "PRINCIPLE"
    return "other.unknown", "OTHER"


class IngestService:
    def __init__(self, database: Database, corpus_root: Path, snapshot_root: Path, extractor: Extractor, deduper=None):
        self.database = database
        self.corpus_root = corpus_root.resolve()
        self.snapshot_root = snapshot_root
        self.extractor = extractor
        self.deduper = deduper
        self.taxonomy = load_taxonomy()

    def ingest_file(self, relative_path: str) -> IngestOutcome:
        path = resolve_corpus_document(self.corpus_root, relative_path)
        raw_bytes = path.read_bytes()
        text = decode_source(raw_bytes)
        source_hash, snapshot = save_snapshot(raw_bytes, self.snapshot_root)
        identity, source_url, source_type = source_identity(text, relative_path)
        dedup_version = (f"{self.deduper.encoder.version}:{self.deduper.judge.version}"
                         f":{getattr(self.deduper.judge, 'model', '')}"
                         f":{getattr(self.deduper.judge, 'prompt_hash', '')}"
                         if self.deduper else "unconfigured")
        extract_config = extraction_config_hash(self.extractor, self.taxonomy)
        fingerprint = _fingerprint(f"{extract_config}:{dedup_version}")

        with self.database.session() as session:
            with session.begin():
                source = session.scalar(select(SourceDocument).where(SourceDocument.source_identity == identity))
                if source is None:
                    source = SourceDocument(source_identity=identity, source_type=source_type, source_url=source_url, original_relative_path=relative_path)
                    session.add(source)
                    session.flush()
                elif relative_path not in {source.original_relative_path, *source.aliases}:
                    source.aliases = [*source.aliases, relative_path]

                revision = session.scalar(select(SourceRevision).where(
                    SourceRevision.source_document_id == source.id,
                    SourceRevision.raw_file_hash == source_hash,
                ))
                if revision is None:
                    revision = SourceRevision(
                        source_document_id=source.id, raw_file_hash=source_hash,
                        snapshot_path=str(snapshot), raw_file_path=relative_path,
                        decoded_text_hash=hashlib.sha256(text.encode()).hexdigest(),
                    )
                    session.add(revision)
                    session.flush()

                existing = session.scalar(select(DocumentBuild).where(
                    DocumentBuild.source_revision_id == revision.id,
                    DocumentBuild.processing_fingerprint == fingerprint,
                ))
                run = PipelineRun(
                    pipeline_version="v1", extractor_version=self.extractor.version,
                    taxonomy_version="v1", embedding_version=hashlib.sha256(dedup_version.encode()).hexdigest(),
                    config_snapshot={"processing_fingerprint": fingerprint,
                                     "dedup_version": dedup_version,
                                     "extractor_prompt_hash": getattr(self.extractor, "prompt_hash", None)},
                    status="RUNNING",
                )
                session.add(run)
                session.flush()
                task = PipelineTask(run_id=run.id, source_document_id=source.id, revision_id=revision.id,
                                    stage="EXTRACT", state="RUNNING", task_key=hashlib.sha256(f"{identity}:{source_hash}:{fingerprint}".encode()).hexdigest())
                session.add(task)
                session.flush()
                source_id, revision_id, run_id, task_id = source.id, revision.id, run.id, task.id
                if source.active_build_id and existing and source.active_build_id == existing.id:
                    task.state = "SKIPPED"
                    run.status = "SUCCEEDED"
                    run.end_time = now_utc()
                    run.skipped_documents = 1
                    corpus_revision = session.get(CorpusState, 1).current_revision
                    return IngestOutcome("SKIPPED", run_id, source_id, corpus_revision)

        try:
            cache = ExtractionStageCache(self.database, self.snapshot_root, revision_id=revision_id,
                                         source_hash=source_hash, config_hash=extract_config)
            cached = cache.load(task_id, lambda result: self._validate_extraction(result, text, revision_id))
            if cached is None:
                result = self.extractor.extract(text=text, revision_id=revision_id)
                # Revalidate model instances too: callers can mutate Pydantic values.
                result = ExtractionResult.model_validate(
                    result.model_dump(mode="json") if isinstance(result, ExtractionResult) else result)
                self._validate_extraction(result, text, revision_id)
                cached = cache.save(task_id, result, getattr(self.extractor, "resolved_model", None))
            result = cached.result
            with self.database.session() as session, session.begin():
                session.get(PipelineTask, task_id).stage = (
                    "DEDUP" if self.deduper and result.document_kind in {"INTERVIEW_REPORT", "MIXED"}
                    else "PUBLISH")
            with self.database.session() as session:
                with session.begin():
                    build = session.scalar(select(DocumentBuild).where(
                        DocumentBuild.source_revision_id == revision_id,
                        DocumentBuild.processing_fingerprint == fingerprint,
                    ))
                    if build is None:
                        decision = ("EXCLUDED" if result.document_kind in {"COMPILATION", "TUTORIAL", "OTHER"}
                                    else "NEEDS_REVIEW" if result.document_kind == "UNKNOWN"
                                    else "INCLUDED")
                        build = DocumentBuild(
                            source_revision_id=revision_id, processing_fingerprint=fingerprint,
                            document_kind=result.document_kind, decision=decision,
                            processing_state="READY", exclusion_reason=result.exclusion_reason,
                            config_snapshot={"extractor_prompt_hash": getattr(self.extractor, "prompt_hash", None),
                                             "extraction_model": getattr(self.extractor, "model", None),
                                             "resolved_extraction_model": cached.resolved_model,
                                             "dedup_version": dedup_version},
                        )
                        session.add(build)
                        session.flush()
                        question_count = self._materialize(session, build, result, text, revision_id)
                        if result.document_kind == "INTERVIEW_REPORT" and question_count == 0:
                            raise ValueError("interview report has no eligible interview questions")
                        if result.document_kind == "MIXED" and question_count == 0:
                            build.decision = "NEEDS_REVIEW"
                    else:
                        question_count = 0
                    new_revision = (session.get(CorpusState, 1).current_revision
                                    if build.decision == "NEEDS_REVIEW"
                                    else publish_build(session, build.id))
                    task = session.get(PipelineTask, task_id)
                    task.build_id = build.id
                    task.state = "SUCCEEDED"
                    run = session.get(PipelineRun, run_id)
                    run.status = "NEEDS_REVIEW" if build.decision == "NEEDS_REVIEW" else "SUCCEEDED"
                    run.end_time = now_utc()
                    run.processed_documents = 0 if build.decision == "NEEDS_REVIEW" else 1
                    run.processed_questions = question_count
                    if build.decision == "EXCLUDED":
                        run.excluded_documents = 1
                    if build.decision == "NEEDS_REVIEW":
                        run.needs_review_documents = 1
                return IngestOutcome("NEEDS_REVIEW" if build.decision == "NEEDS_REVIEW" else "SUCCEEDED",
                                     run_id, source_id, new_revision, question_count,
                                     excluded=build.decision == "EXCLUDED")
        except Exception as error:
            with self.database.session() as session:
                with session.begin():
                    task = session.get(PipelineTask, task_id)
                    task.state = "FAILED"
                    task.attempt_count += 1
                    task.error_code = type(error).__name__
                    task.error_detail = str(error)[:2000]
                    run = session.get(PipelineRun, run_id)
                    run.status = "FAILED"
                    run.end_time = now_utc()
                    run.failed_documents = 1
            raise

    def _validate_extraction(self, result: ExtractionResult, text: str, revision_id: str) -> None:
        from interview_intelligence.extraction.validation import validate_extraction
        validate_extraction(result, text, revision_id, self.taxonomy)

    def _materialize(self, session, build: DocumentBuild, result: ExtractionResult, text: str, revision_id: str) -> int:
        if build.decision != "INCLUDED":
            return 0
        count = 0
        for order, extracted in enumerate(result.interviews, 1):
            metadata = extracted.metadata
            raw_position = metadata.get("position_raw")
            position, family, languages = _normalize_position(raw_position)
            interview = Interview(
                build_id=build.id, session_key=extracted.local_id,
                session_order=order, session_kind=extracted.session_kind,
                company_raw=metadata.get("company_raw"),
                company_normalized=_normalize_company(metadata.get("company_raw")),
                position_raw=raw_position, position_normalized=position,
                job_family=family, language_tags=languages,
                round_raw=metadata.get("round_raw"),
                round=_normalize_round(metadata.get("round_raw")),
                interview_date=_parse_full_date(metadata.get("interview_date_raw")),
                publish_date=_parse_full_date(metadata.get("publish_date_raw")),
                metadata_evidence={key: [span.model_dump(mode="json") for span in spans]
                                   for key, spans in extracted.metadata_evidence.items()},
                analytics_eligible=True,
                extractor_version=self.extractor.version,
            )
            session.add(interview)
            session.flush()
            local_questions = {}
            for question_order, question in enumerate(extracted.questions, 1):
                if question.evidence_kind != "INTERVIEW_QUESTION":
                    continue
                if bool(question.topic_l1) != bool(question.topic_l2):
                    raise ValueError("taxonomy classification must provide both levels")
                if question.topic_l1:
                    topic_id = self.taxonomy.resolve(question.topic_l1, question.topic_l2)
                    question_type = question.question_type.value if question.question_type else "OTHER"
                else:
                    topic_id, question_type = _classify(question.normalized_question)
                self.taxonomy.labels(topic_id)
                if self.deduper:
                    resolved = self.deduper.resolve(session, question.normalized_question, topic_id, question_type)
                    canonical = resolved.canonical
                    decision = resolved.decision
                    candidate_ids = resolved.candidate_ids
                    related_ids = resolved.related_ids
                    judge_version = self.deduper.judge.version
                    judge_evidence = resolved.evidence or {}
                    dedup_confidence = resolved.confidence
                else:
                    canonical = CanonicalQuestion(
                        canonical_text=question.normalized_question,
                        primary_topic_id=topic_id, taxonomy_version="v1",
                        question_type=question_type,
                    )
                    session.add(canonical)
                    session.flush()
                    decision, candidate_ids, related_ids, judge_version = "NEW", [], [], "unconfigured"
                    judge_evidence = {}
                    dedup_confidence = 1.0
                occurrence = QuestionOccurrence(
                    interview_id=interview.id, canonical_question_id=canonical.id,
                    raw_question=question.raw_question,
                    normalized_question=question.normalized_question,
                    question_order=question_order,
                    context_before=question.context_before,
                    context_after=question.context_after,
                    source_spans=[span.model_dump(mode="json") for span in question.source_spans],
                    topic_id=topic_id, taxonomy_version="v1",
                    question_type=question_type, confidence=question.confidence,
                    evidence_origin=question.source_spans[0].origin,
                )
                session.add(occurrence)
                session.flush()
                if question.response_form != "UNKNOWN" and question.coding_focus != "UNKNOWN":
                    session.add(OccurrenceTaskAnnotation(occurrence_id=occurrence.id,
                        classification_status="UNKNOWN" if "UNKNOWN" in {
                            question.response_form,question.coding_focus} else "NEEDS_REVIEW",
                        response_form=question.response_form, coding_focus=question.coding_focus,
                        producer_version="extraction_task_v1", evidence={"source_spans": occurrence.source_spans}))
                if question_type == "ALGORITHM" or any(word in question.raw_question for word in ("手撕", "lc", "力扣", "LeetCode")):
                    match = parse_algorithm_match(question.raw_question)
                    session.add(AlgorithmMatch(occurrence_id=occurrence.id, **match))
                session.add(CanonicalAssignment(
                    occurrence_id=occurrence.id, canonical_question_id=canonical.id,
                    decision=decision, candidate_ids=candidate_ids, judge_version=judge_version,
                    confidence=dedup_confidence, evidence=judge_evidence,
                    valid_from_revision=session.get(CorpusState, 1).current_revision + 1,
                ))
                for related_id in related_ids:
                    session.add(QuestionRelation(
                        relation_type="RELATED", source_canonical_id=canonical.id,
                        target_canonical_id=related_id, provenance="MODEL_INFERRED",
                        confidence=question.confidence,
                        producer_version=judge_version,
                    ))
                local_questions[question.local_id] = occurrence
                count += 1
            for followup in extracted.followups:
                source_question = local_questions.get(followup.source_local_id)
                target_question = local_questions.get(followup.target_local_id)
                if source_question is None or target_question is None or source_question.id == target_question.id:
                    continue
                if source_question.question_order >= target_question.question_order:
                    raise ValueError("followup must point to a later question")
                session.add(QuestionRelation(
                    relation_type="OBSERVED_FOLLOWUP",
                    source_occurrence_id=source_question.id,
                    target_occurrence_id=target_question.id,
                    source_interview_id=interview.id,
                    evidence_spans=[span.model_dump(mode="json") for span in followup.evidence_spans],
                    provenance="SOURCE", confidence=followup.confidence,
                ))
        return count
