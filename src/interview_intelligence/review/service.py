"""Transactional, idempotent review recording."""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import select

from interview_intelligence.contracts import ReviewItem, ReviewRequest, ReviewStatus
from interview_intelligence.domain.models import (
    CanonicalQuestion, Database, IdempotencyReceipt, ReviewEvent,
    UserQuestionState, UserRevision, now_utc,
)


class ReviewService:
    def __init__(self, database: Database):
        self.database = database

    def get_states(self, user_id: str, canonical_ids: list[str]) -> dict:
        with self.database.session() as session:
            stored = {
                item.canonical_question_id: item
                for item in session.scalars(select(UserQuestionState).where(
                    UserQuestionState.user_id == user_id,
                    UserQuestionState.canonical_question_id.in_(canonical_ids),
                ))
            }
            revision = session.get(UserRevision, user_id)
            return {
                "states": {canonical_id: {
                    "status": stored[canonical_id].status if canonical_id in stored else "UNSEEN",
                    "review_count": stored[canonical_id].review_count if canonical_id in stored else 0,
                    "binding_status": stored[canonical_id].binding_status if canonical_id in stored else "RESOLVED",
                } for canonical_id in canonical_ids},
                "user_state_revision": revision.state_revision if revision else 0,
            }

    def record(self, user_id: str, request: ReviewRequest) -> dict:
        payload = request.model_dump(mode="json")
        payload_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        namespace = "review_" + request.operation
        with self.database.session() as session:
            with session.begin():
                receipt = session.scalar(select(IdempotencyReceipt).where(
                    IdempotencyReceipt.namespace == namespace,
                    IdempotencyReceipt.actor_id == user_id,
                    IdempotencyReceipt.idempotency_key == request.idempotency_key,
                ))
                if receipt is not None:
                    if receipt.payload_hash != payload_hash:
                        raise ValueError("IDEMPOTENCY_CONFLICT")
                    return dict(receipt.response_json)

                revision = session.get(UserRevision, user_id)
                if revision is None:
                    revision = UserRevision(user_id=user_id, state_revision=0)
                    session.add(revision)
                    session.flush()

                if request.operation == "resolve":
                    items, changed = self._resolve(session, user_id, request)
                else:
                    items, changed = self._create(session, user_id, request)
                if changed:
                    revision.state_revision += 1
                response = {"items": items, "user_state_revision": revision.state_revision}
                session.add(IdempotencyReceipt(
                    namespace=namespace, actor_id=user_id,
                    idempotency_key=request.idempotency_key,
                    payload_hash=payload_hash, response_json=response,
                ))
                return response

    def _create(self, session, user_id: str, request: ReviewRequest) -> tuple[list[dict], bool]:
        results = []
        seen = set()
        for index, item in enumerate(request.items):
            canonical_id = item.canonical_question_id
            if canonical_id:
                canonical = session.get(CanonicalQuestion, canonical_id)
                if canonical is None:
                    raise KeyError(f"unknown canonical question {canonical_id}")
                if canonical.lifecycle == "REDIRECT":
                    canonical_id = canonical.redirect_to_id
            else:
                matches = list(session.scalars(select(CanonicalQuestion).where(
                    CanonicalQuestion.canonical_text == item.raw_question,
                    CanonicalQuestion.lifecycle == "ACTIVE",
                )))
                if len(matches) > 1:
                    raise ValueError("NEEDS_CLARIFICATION: multiple matching canonical questions")
                canonical_id = matches[0].id if matches else None
            if canonical_id and canonical_id in seen:
                raise ValueError("duplicate canonical question in review batch")
            if canonical_id:
                seen.add(canonical_id)
            event = ReviewEvent(
                user_id=user_id, canonical_question_id=canonical_id,
                raw_question=item.raw_question,
                requested_status=item.status.value, score=item.score, note=item.note,
                occurred_at=item.occurred_at or now_utc(),
                idempotency_key=request.idempotency_key,
                item_index=index,
                resolution_status="RESOLVED" if canonical_id else "UNRESOLVED",
            )
            session.add(event)
            session.flush()
            if canonical_id:
                self._apply_state(session, user_id, canonical_id, item)
            results.append({
                "event_id": event.id, "canonical_question_id": canonical_id,
                "resolution_status": event.resolution_status,
                "status": item.status.value,
            })
        return results, bool(results)

    def _resolve(self, session, user_id: str, request: ReviewRequest) -> tuple[list[dict], bool]:
        event = session.scalar(select(ReviewEvent).where(
            ReviewEvent.id == request.resolve_event_id,
            ReviewEvent.user_id == user_id,
        ).with_for_update())
        if event is None:
            raise KeyError("unknown review event")
        canonical = session.get(CanonicalQuestion, request.canonical_question_id)
        if canonical is None:
            raise KeyError("unknown canonical question")
        target_id = canonical.redirect_to_id if canonical.lifecycle == "REDIRECT" else canonical.id
        if event.resolution_status == "RESOLVED":
            if event.canonical_question_id != target_id:
                raise ValueError("REVIEW_EVENT_ALREADY_RESOLVED_TO_OTHER_QUESTION")
            return [{"event_id": event.id, "canonical_question_id": target_id,
                     "resolution_status": "RESOLVED", "status": event.requested_status}], False
        if event.resolution_status != "UNRESOLVED":
            raise ValueError("review event cannot be resolved")
        item = ReviewItem(
            canonical_question_id=target_id,
            status=ReviewStatus(event.requested_status),
            score=event.score, note=event.note,
            occurred_at=event.occurred_at,
            expected_version=request.expected_version,
        )
        self._apply_state(session, user_id, target_id, item)
        event.canonical_question_id = target_id
        event.resolution_status = "RESOLVED"
        return [{"event_id": event.id, "canonical_question_id": target_id,
                 "resolution_status": "RESOLVED", "status": event.requested_status}], True

    def _apply_state(self, session, user_id: str, canonical_id: str, item: ReviewItem) -> None:
        state = session.scalar(select(UserQuestionState).where(
            UserQuestionState.user_id == user_id,
            UserQuestionState.canonical_question_id == canonical_id,
        ).with_for_update())
        current_version = state.version if state else 0
        if item.expected_version is not None and item.expected_version != current_version:
            raise ValueError("USER_STATE_VERSION_CONFLICT")
        if state is None:
            state = UserQuestionState(
                user_id=user_id, canonical_question_id=canonical_id,
                review_count=0, version=0,
            )
            session.add(state)
        state.status = item.status.value
        state.binding_status = "RESOLVED"
        state.version += 1
        if item.status != ReviewStatus.UNSEEN:
            state.review_count += 1
            occurred_at = item.occurred_at or now_utc()
            if state.last_reviewed_at is None or occurred_at > state.last_reviewed_at.replace(tzinfo=occurred_at.tzinfo):
                state.last_reviewed_at = occurred_at
        if item.score is not None:
            state.last_score = item.score
        if "note" in item.model_fields_set:
            state.note = item.note
