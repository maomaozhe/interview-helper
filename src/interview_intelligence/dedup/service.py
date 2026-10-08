"""Embedding candidates followed by a three-way semantic decision."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from interview_intelligence.domain.models import CanonicalQuestion, EmbeddingCache
from interview_intelligence.dedup.candidates import exact_rank, text_hash


class Encoder(Protocol):
    version: str

    def embed(self, text: str) -> list[float]: ...


class Judge(Protocol):
    version: str

    def judge(self, incoming: str, candidate: str): ...


@dataclass(frozen=True)
class DedupResolution:
    canonical: CanonicalQuestion
    decision: str
    candidate_ids: list[str]
    related_ids: list[str]
    confidence: float
    needs_review: bool = False
    evidence: dict | None = None


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        raise ValueError("embedding dimension mismatch")
    norm_a = math.sqrt(sum(value * value for value in a))
    norm_b = math.sqrt(sum(value * value for value in b))
    if not math.isfinite(norm_a * norm_b) or norm_a == 0 or norm_b == 0:
        raise ValueError("invalid embedding vector")
    return sum(x * y for x, y in zip(a, b)) / (norm_a * norm_b)


class DedupService:
    def __init__(self, *, encoder: Encoder, judge: Judge, candidate_limit: int = 10,
                 candidate_head=None, candidate_backend="auto", ann_min_size=10000):
        if not 5 <= candidate_limit <= 10:
            raise ValueError("candidate_limit must be 5–10")
        self.encoder = encoder
        self.judge = judge
        self.candidate_limit = candidate_limit
        self.candidate_head, self.candidate_backend, self.ann_min_size = candidate_head, candidate_backend, ann_min_size

    def candidates(self, session, question, existing, query_vector=None):
        import httpx
        query_vector = query_vector if query_vector is not None else self._embedding(session,question)
        selected = existing
        metadata = {"engine":"vectorized_exact","indexed_count":0,"delta_count":len(existing)}
        use_ann = self.candidate_backend=="hnsw" or (self.candidate_backend=="auto" and len(existing)>=self.ann_min_size)
        if self.candidate_head and use_ann:
            try:
                selected, metadata = self.candidate_head.propose(query_vector,existing,self.encoder.version,self.candidate_limit)
            except (httpx.HTTPError, KeyError, ValueError) as failure:
                metadata["fallback_reason"] = type(failure).__name__
        hashes = {text_hash(item.canonical_text) for item in selected}
        cached = {row.text_hash:row.vector for row in session.scalars(select(EmbeddingCache).where(
            EmbeddingCache.text_hash.in_(hashes),EmbeddingCache.embedding_version==self.encoder.version))} if hashes else {}
        vectors = [cached[text_hash(item.canonical_text)] if text_hash(item.canonical_text) in cached
                   else self._embedding(session,item.canonical_text) for item in selected]
        return exact_rank(query_vector,selected,vectors,self.candidate_limit), metadata

    def _embedding(self, session: Session, text: str) -> list[float]:
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        cached = session.scalar(select(EmbeddingCache).where(
            EmbeddingCache.text_hash == text_hash,
            EmbeddingCache.embedding_version == self.encoder.version,
        ))
        if cached:
            return cached.vector
        vector = self.encoder.embed(text)
        cosine(vector, vector)
        session.add(EmbeddingCache(
            text_hash=text_hash, embedding_version=self.encoder.version,
            dimension=len(vector), vector=vector,
        ))
        session.flush()
        return vector

    def resolve(self, session: Session, question: str, topic_id: str, question_type: str) -> DedupResolution:
        query_vector = self._embedding(session, question)
        existing = list(session.scalars(select(CanonicalQuestion).where(CanonicalQuestion.lifecycle == "ACTIVE")))
        ranked, candidate_search = self.candidates(session,question,existing,query_vector)
        same = []
        related = []
        decisions = []
        candidate_ids = [item.id for _, item in ranked]
        batch_method = getattr(self.judge, "judge_many", None)
        batch_results = None
        if ranked and callable(batch_method):
            batch_results = batch_method(question, [(item.id, item.canonical_text) for _, item in ranked])
            if not isinstance(batch_results, dict) or set(batch_results) != set(candidate_ids):
                raise ValueError("batch judge returned a different candidate set")
        for _score, candidate in ranked:
            result = (batch_results[candidate.id] if batch_results is not None
                      else self.judge.judge(question, candidate.canonical_text))
            label = result if isinstance(result, str) else result.decision
            if label not in {"SAME", "RELATED", "DIFFERENT"}:
                raise ValueError("judge returned an unknown semantic decision")
            decisions.append({"candidate_id": candidate.id, "decision": label,
                              "reason_code": getattr(result, "reason_code", None),
                              "confidence": getattr(result, "confidence", None),
                              "cosine": _score})
            if label == "SAME":
                same.append(candidate)
            elif label == "RELATED":
                related.append(candidate.id)
        if len(same) == 1:
            matching = next(item for item in decisions if item["candidate_id"] == same[0].id)
            return DedupResolution(same[0], "SAME", candidate_ids, related,
                                   matching["confidence"] or 1.0, evidence={"judgements": decisions,"candidate_search":candidate_search})
        canonical = CanonicalQuestion(
            canonical_text=question, primary_topic_id=topic_id,
            taxonomy_version="v1", question_type=question_type,
        )
        session.add(canonical)
        session.flush()
        return DedupResolution(canonical, "NEW", candidate_ids, related, 1.0,
                               len(same) > 1, evidence={"judgements": decisions,"candidate_search":candidate_search})
