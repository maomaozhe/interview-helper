"""Validated extraction artifacts survive a rolled-back document publication."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Callable, Literal

from sqlalchemy import select

from interview_intelligence.contracts import ExtractionResult, StrictModel
from interview_intelligence.domain.models import Database, PipelineTask, StageArtifact


def _hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def extraction_config_hash(extractor, taxonomy) -> str:
    """Explicit non-secret inputs only; judgement configuration is independent."""
    return _hash({
        "format": "extraction-stage-v1",
        "extractor": f"{type(extractor).__module__}.{type(extractor).__qualname__}",
        "version": extractor.version,
        "model": getattr(extractor, "model", None),
        "prompt_hash": getattr(extractor, "prompt_hash", None),
        "configuration": getattr(extractor, "cache_configuration", None),
        "schema": ExtractionResult.model_json_schema(),
        "taxonomy": {"version": taxonomy.version, "topics": taxonomy.topics},
    })


class CachedExtraction(StrictModel):
    format: Literal["extraction-stage-v1"] = "extraction-stage-v1"
    result: ExtractionResult
    resolved_model: str | None = None


class ExtractionStageCache:
    def __init__(self, database: Database, snapshot_root: Path, *, revision_id: str,
                 source_hash: str, config_hash: str):
        self.database = database
        self.input_hash = _hash({"revision_id": revision_id, "raw_file_hash": source_hash})
        self.config_hash = config_hash
        self.key = _hash({"stage": "EXTRACT", "input": self.input_hash, "config": config_hash})
        self.root = snapshot_root.parent / "processed" / "extract"
        self.path = self.root / f"{self.key}.json"

    def load(self, task_id: str, validate: Callable[[ExtractionResult], None]) -> CachedExtraction | None:
        with self.database.session() as session, session.begin():
            artifact = session.scalar(select(StageArtifact).where(StageArtifact.cache_key == self.key))
            if (artifact is None or artifact.stage != "EXTRACT"
                    or artifact.input_hash != self.input_hash or artifact.config_hash != self.config_hash):
                return None
            try:
                # Derive the path from the current mount, never a historical DB path.
                raw = self.path.read_bytes()
                if hashlib.sha256(raw).hexdigest() != artifact.output_hash:
                    return None
                cached = CachedExtraction.model_validate_json(raw)
                validate(cached.result)
            except (OSError, ValueError):
                return None
            session.get(PipelineTask, task_id).artifact_id = artifact.id
            return cached

    def save(self, task_id: str, result: ExtractionResult, resolved_model: str | None) -> CachedExtraction:
        cached = CachedExtraction(result=result, resolved_model=resolved_model)
        raw = cached.model_dump_json().encode("utf-8")
        output_hash = hashlib.sha256(raw).hexdigest()
        self.root.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_path = tempfile.mkstemp(prefix=".extract-", dir=self.root)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_path, self.path)
        finally:
            if os.path.exists(temporary_path):
                os.unlink(temporary_path)
        if hashlib.sha256(self.path.read_bytes()).hexdigest() != output_hash:
            raise ValueError("extraction artifact write verification failed")
        # Commit before entering the document transaction: later failures must not
        # discard successful extraction work or its task association.
        with self.database.session() as session, session.begin():
            artifact = session.scalar(select(StageArtifact).where(StageArtifact.cache_key == self.key))
            if artifact is None:
                artifact = StageArtifact(cache_key=self.key)
                session.add(artifact)
            artifact.stage = "EXTRACT"
            artifact.input_hash = self.input_hash
            artifact.config_hash = self.config_hash
            artifact.output_hash = output_hash
            artifact.artifact_path = f"processed/extract/{self.key}.json"
            session.flush()
            session.get(PipelineTask, task_id).artifact_id = artifact.id
        return cached
