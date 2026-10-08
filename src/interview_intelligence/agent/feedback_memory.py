"""User-scoped, attributable query corrections; source feedback stays immutable."""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from uuid import UUID

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from interview_intelligence.contracts import StrictModel
from interview_intelligence.domain.models import AgentTurn, UserPreference


class MemoryUpdate(StrictModel):
    active: bool
    expected_version: int = Field(ge=0)


def query_key(text: str) -> str:
    return re.sub(r"[\s，,。.!！?？]+", "", unicodedata.normalize("NFKC", text).casefold())


class FeedbackMemory:
    def __init__(self, database, user_id: str, root: Path):
        self.database, self.user_id, self.root = database, user_id, root

    def feedback(self, feedback_id):
        try:
            name = str(UUID(feedback_id))
        except (ValueError, TypeError):
            raise KeyError("FEEDBACK_NOT_FOUND")
        path = self.root / f"{name}.json"
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise KeyError("FEEDBACK_NOT_FOUND")
        if record.get("user_id", "local") != self.user_id:
            raise KeyError("FEEDBACK_NOT_FOUND")
        return record

    @staticmethod
    def correction(record):
        return {"feedback_id": record["id"], "query": record.get("query", ""),
                "correction": record["note"], "negative_question_id":
                record.get("canonical_question_id") if record.get("category") == "IRRELEVANT" else None,
                "source_run_id": record.get("run_id"), "source": "explicit_user_feedback"}

    def selected(self, ids):
        records = [self.feedback(i) for i in ids]
        return [self.correction(r) for r in records]

    def matching(self, message):
        key = query_key(message)
        with self.database.session() as session:
            records = list(session.scalars(select(UserPreference).where(
                UserPreference.user_id == self.user_id,
                UserPreference.key.like("query_correction:%"), UserPreference.deleted.is_(False))
                .order_by(UserPreference.updated_at.desc(), UserPreference.key)))
            return [{**p.value, "memory_version": p.version} for p in records
                    if isinstance(p.value, dict) and p.value.get("trigger") == key][:3]

    def list(self):
        records = []
        if self.root.is_dir():
            for path in self.root.glob("*.json"):
                try:
                    r = self.feedback(path.stem)
                except KeyError:
                    continue
                if r.get("query") and r.get("category") == "IRRELEVANT":
                    records.append(r)
        records.sort(key=lambda r: r["created_at"], reverse=True)
        with self.database.session() as session:
            return [{**self.correction(r), "created_at": r["created_at"],
                     "active": bool(p and not p.deleted), "version": p.version if p else 0}
                    for r in records[:100]
                    for p in [session.get(UserPreference, (self.user_id, f"query_correction:{r['id']}"))]]

    def update(self, feedback_id, request: MemoryUpdate):
        record = self.feedback(feedback_id)
        if not record.get("query") or record.get("category") != "IRRELEVANT":
            raise ValueError("QUERY_CORRECTION_REQUIRES_RELEVANCE_FEEDBACK")
        for attempt in range(2):
            try:
                with self.database.session() as session, session.begin():
                    key = f"query_correction:{record['id']}"
                    p = session.get(UserPreference, (self.user_id, key), with_for_update=True)
                    if request.expected_version != (p.version if p else 0):
                        raise ValueError("PREFERENCE_VERSION_CONFLICT")
                    if not p:
                        p = UserPreference(user_id=self.user_id, key=key, version=0,
                                           source_message=record["note"])
                        session.add(p)
                    p.value = {**self.correction(record), "trigger": query_key(record["query"])}
                    p.deleted, p.version = not request.active, p.version + 1
                    session.flush()
                    return {"feedback_id": record["id"], "active": not p.deleted, "version": p.version}
            except IntegrityError:
                if attempt:
                    raise

    def export(self):
        samples = self.list()
        by_id = {m["feedback_id"]: {**m, "original_receipt":
            self.feedback(m["feedback_id"]).get("context", {}).get("receipt"), "replays": []}
            for m in samples}
        with self.database.session() as session:
            for turn in session.scalars(select(AgentTurn).where(AgentTurn.user_id == self.user_id)
                                       .order_by(AgentTurn.created_at.desc()).limit(1000)):
                for fid in (turn.request_payload or {}).get("feedback_ids", []):
                    if fid in by_id:
                        by_id[fid]["replays"].append({"run_id": turn.id, "request_id": turn.request_id,
                            "message": turn.message, "status": turn.status, "result": turn.response})
        return {"version": "feedback_regression_v1", "human_verified": False,
                "samples": list(by_id.values()), "scope": "user_query_corrections",
                "replay_scan_limit": 1000, "semantic_judgments": "user_corrections_not_independent_gold"}
