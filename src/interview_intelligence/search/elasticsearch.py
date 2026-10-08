"""Elasticsearch BM25/dense retrieval and revision-fenced full index rebuilds."""

from __future__ import annotations

import hashlib
import math
import re
from collections import defaultdict
from uuid import uuid4

import httpx
import jieba
from sqlalchemy import select

from interview_intelligence.analytics.stats import _active_from
from interview_intelligence.domain.models import (
    CanonicalQuestion, CorpusState, IndexSyncTask, Interview, QuestionOccurrence,
    SourceDocument, DocumentBuild, EmbeddingCache,
)
from interview_intelligence.search.service import rrf


ALIAS = "interview_questions"
SCHEMA_VERSION = "search_v1_jieba_whitespace"


def tokenize(text: str) -> str:
    protected = re.findall(r"[A-Za-z][A-Za-z0-9_+.#-]*", text)
    words = [word.casefold() for word in jieba.cut(text) if word.strip()]
    return " ".join(dict.fromkeys([*words, *(word.casefold() for word in protected)]))


def _valid_embedding(vector, dimension: int) -> bool:
    try:
        return (len(vector) == dimension
                and all(math.isfinite(value) for value in vector)
                and any(value != 0 for value in vector))
    except (TypeError, ValueError):
        return False


def _facet_candidate_pool(main: list[dict], facets: list[list[tuple[str, float]]]) -> list[dict]:
    """Reserve the main Top20, then cover lexical branches within the same 50."""
    rows = {item["canonical_question_id"]: {
        **item, "stage_scores": dict(item["stage_scores"]), "stage_ranks": {}} for item in main}
    for index, ranking in enumerate(facets, 1):
        stage = f"lexical_facet_{index}"
        for rank, (canonical_id, score) in enumerate(ranking, 1):
            item = rows.setdefault(canonical_id, {"canonical_question_id": canonical_id,
                                                  "stage_scores": {}, "stage_ranks": {}})
            item["stage_scores"][stage] = score
            item["stage_ranks"][stage] = rank
    selected, seen = [], set()

    def append(canonical_id):
        if len(selected) >= 50 or canonical_id in seen:
            return False
        seen.add(canonical_id)
        selected.append(rows[canonical_id])
        return True

    for item in main[:20]:
        append(item["canonical_question_id"])
    positions, additions = [0] * len(facets), [0] * len(facets)

    def add_from_branches(limit):
        while len(selected) < 50:
            added = False
            for index, ranking in enumerate(facets):
                while positions[index] < len(ranking) and ranking[positions[index]][0] in seen:
                    positions[index] += 1
                if additions[index] >= limit or positions[index] >= len(ranking):
                    continue
                canonical_id = ranking[positions[index]][0]
                positions[index] += 1
                if append(canonical_id):
                    additions[index] += 1
                    added = True
            if not added:
                break

    add_from_branches(10)
    for item in main[20:]:
        append(item["canonical_question_id"])
        if len(selected) >= 50:
            break
    add_from_branches(50)
    return selected


def _select_reranked(rows, facets, top_k):
    """Keep semantic leaders, cover missing facets once, then resume that order."""
    ranked = sorted(rows, key=lambda row: (-row.get("relevance_grade", 0), row.get("rerank_rank", 0)))
    if not facets:
        return ranked[:top_k]
    selected = ranked[:min(5, top_k)]
    seen = {row["canonical_question_id"] for row in selected}
    for index in range(1, len(facets) + 1):
        if len(selected) >= top_k:
            break
        stage = f"lexical_facet_{index}"
        if any(row.get("stage_ranks", {}).get(stage, 51) <= 10 for row in selected):
            continue
        row = min((row for row in ranked if row.get("stage_ranks", {}).get(stage, 51) <= 10
                   and row["canonical_question_id"] not in seen),
                  key=lambda row: row["stage_ranks"][stage], default=None)
        if row is not None:
            selected.append(row)
            seen.add(row["canonical_question_id"])
    for row in ranked:
        if len(selected) >= top_k:
            break
        if row["canonical_question_id"] not in seen:
            selected.append(row)
            seen.add(row["canonical_question_id"])
    return selected


class ElasticsearchRetriever:
    supports_relevance_query = True
    supports_lexical_facets = True
    supports_progress = True
    def __init__(self, url: str, encoder=None, *, reranker=None, client=None, alias: str = ALIAS):
        self.client = client or httpx.Client(base_url=url.rstrip("/"), timeout=60, trust_env=False)
        self.encoder = encoder
        self.reranker = reranker
        self.alias = alias

    def _request(self, method: str, path: str, **kwargs) -> dict:
        if method == "delete" and isinstance(self.client, httpx.Client):
            response = self.client.request("DELETE", path, **kwargs)
        else:
            response = getattr(self.client, method)(path, **kwargs)
        response.raise_for_status()
        return response.json()

    def retrieve(self, query: str, eligible_ids: list[str], pipeline: str, top_k: int,
                 *, relevance_query: str | None = None, lexical_facets: list[str] | None = None,
                 on_progress=None) -> dict:
        if pipeline not in {"BM25", "DENSE", "HYBRID", "HYBRID_RERANK"}:
            raise ValueError("unknown retrieval pipeline")
        if pipeline != "BM25" and self.encoder is None:
            raise ValueError("EMBEDDING_NOT_READY")
        if pipeline == "HYBRID_RERANK" and self.reranker is None:
            raise ValueError("RERANKER_NOT_READY")
        if lexical_facets is not None and (not isinstance(lexical_facets, list) or len(lexical_facets) > 3 or
                any(not isinstance(facet, str) or not facet.strip() or len(facet) > 150 for facet in lexical_facets)):
            raise ValueError("invalid lexical facets")
        facets = list(dict.fromkeys(facet.strip() for facet in lexical_facets or [])) if pipeline in {"HYBRID", "HYBRID_RERANK"} else []
        if not eligible_ids:
            return {"data": [], "meta": {"pipeline": pipeline}}
        # Waiting for the shared model gate can exceed a PIT's lifetime.
        # Keep the ES snapshot only across the main and optional lexical requests.
        vector = None
        if pipeline in {"DENSE", "HYBRID", "HYBRID_RERANK"}:
            if on_progress:
                on_progress({"stage":"embedding"})
            vector = (self.encoder.embed_query(query) if hasattr(self.encoder, "embed_query")
                      else self.encoder.embed(query))
        if on_progress:
            on_progress({"stage":"retrieving"})
        pit = self._request("post", f"/{self.alias}/_pit", params={"keep_alive": "1m"})["id"]
        try:
            filter_clause = {"terms": {"_id": eligible_ids}}
            rankings = []
            facet_rankings = []
            source_texts = {}
            if pipeline in {"BM25", "HYBRID", "HYBRID_RERANK"}:
                body = {
                    "pit": {"id": pit, "keep_alive": "1m"},
                    "size": 100,
                    "query": {"bool": {"filter": [filter_clause], "must": [{
                        "multi_match": {"query": tokenize(query),
                                        "fields": ["search_tokens^2", "variants_tokens"]}
                    }]}},
                }
                hits = self._request("post", "/_search", json=body,
                                     params={"allow_partial_search_results": "false"})["hits"]["hits"]
                source_texts.update({hit["_id"]: hit.get("_source", {}).get("canonical_text", "") for hit in hits})
                rankings.append([(hit["_id"], hit["_score"]) for hit in hits])
            for facet in facets:
                body = {
                    "pit": {"id": pit, "keep_alive": "1m"},
                    "size": 50,
                    "query": {"bool": {"filter": [filter_clause], "must": [{
                        "multi_match": {"query": tokenize(facet),
                                        "fields": ["search_tokens^2", "variants_tokens"]}
                    }]}},
                }
                hits = self._request("post", "/_search", json=body,
                                     params={"allow_partial_search_results": "false"})["hits"]["hits"]
                source_texts.update({hit["_id"]: hit.get("_source", {}).get("canonical_text", "") for hit in hits})
                facet_rankings.append([(hit["_id"], hit["_score"]) for hit in hits])
            if pipeline in {"DENSE", "HYBRID", "HYBRID_RERANK"}:
                body = {
                    "pit": {"id": pit, "keep_alive": "1m"},
                    "size": 100,
                    "knn": {"field": "embedding", "query_vector": vector,
                            "k": 100, "num_candidates": 500, "filter": filter_clause},
                }
                hits = self._request("post", "/_search", json=body,
                                     params={"allow_partial_search_results": "false"})["hits"]["hits"]
                source_texts.update({hit["_id"]: hit.get("_source", {}).get("canonical_text", "") for hit in hits})
                rankings.append([(hit["_id"], hit["_score"]) for hit in hits])
        except httpx.HTTPError as failure:
            if facets:
                raise ValueError("LEXICAL_FACET_RETRIEVAL_FAILED") from failure
            raise
        finally:
            try:
                self._request("delete", "/_pit", json={"id": pit})
            except httpx.HTTPError as failure:
                if facets:
                    raise ValueError("LEXICAL_FACET_RETRIEVAL_FAILED") from failure
                raise
        rerank_status="NOT_REQUESTED"
        rerank_audit = None
        executed=pipeline
        if pipeline in {"BM25", "DENSE"}:
            data = [{"canonical_question_id": canonical_id, "score": score,
                     "stage_scores": {pipeline.lower(): score}}
                    for canonical_id, score in rankings[0][:top_k]]
        else:
            main = rrf(*rankings)
            data = _facet_candidate_pool(main, facet_rankings) if facets else main[:50]
            candidate_count = len(data)
            if pipeline == "HYBRID_RERANK":
                if on_progress:
                    on_progress({"stage":"reranking", "count":candidate_count})
                data = [{**item, "canonical_text": source_texts.get(item["canonical_question_id"], "")}
                        for item in data]
                try:
                    data = self.reranker.rerank(relevance_query or query, data)
                    rerank_audit = getattr(data, "audit", None)
                    accepted_data = data
                    data = _select_reranked(accepted_data, facets, top_k)
                    if rerank_audit:
                        selected = {item["canonical_question_id"] for item in data[:top_k]}
                        rerank_audit = {**rerank_audit, "candidates": [
                            {**item, "decision": "RETURNED" if item["canonical_question_id"] in selected else
                                "PAGE_CUTOFF" if item["decision"] == "ACCEPTED" else item["decision"]}
                            for item in rerank_audit["candidates"]]}
                    rerank_status="COMPLETED"
                except Exception as e:
                    from openai import APIError
                    from pydantic import ValidationError
                    if isinstance(e,ValueError) and str(e) in {"QUERY_CANCELLED","QUERY_DEADLINE_EXCEEDED"}: raise
                    budget=isinstance(e,ValueError) and str(e) in {"QUERY_TOKEN_BUDGET_EXCEEDED","QUERY_MODEL_BUDGET_EXCEEDED"}
                    if not budget and not isinstance(e,(httpx.HTTPError,APIError,ValidationError)): raise
                    rerank_status="SKIPPED_BUDGET" if budget else "FAILED"
                    executed="HYBRID"
            data = data[:top_k]
        metadata = {"pipeline": executed,"rerank_status":rerank_status,
                "index_schema_version": SCHEMA_VERSION, "relevance_query": relevance_query or query,
                "reranker_version": self.reranker.version if pipeline == "HYBRID_RERANK" else None,
                "rerank_audit": rerank_audit,
                "candidate_count": candidate_count if pipeline in {"HYBRID", "HYBRID_RERANK"} else len(rankings[0]),
                "embedding_version": self.encoder.version if self.encoder else None}
        if lexical_facets:
            metadata.update(lexical_facets=facets, lexical_facets_ignored=not bool(facets),
                candidate_pool_policy="main20_facet10_round_robin_v1" if facets else "main_only",
                result_selection_policy="top5_missing_facet_once_v1" if facets and rerank_status == "COMPLETED" else "grade_then_model_rank_v1")
        return {"data": data, "meta": metadata}


def rebuild_index(database, retriever: ElasticsearchRetriever) -> int:
    """Build a complete new physical index and atomically switch its alias."""
    if retriever.encoder is None:
        raise ValueError("EMBEDDING_NOT_READY")
    with database.session() as session:
        state = session.get(CorpusState, 1)
        revision = state.current_revision
        rows = session.execute(
            select(QuestionOccurrence, CanonicalQuestion)
            .select_from(_active_from())
            .where(SourceDocument.active_build_id == DocumentBuild.id,
                   Interview.analytics_eligible.is_(True),
                   DocumentBuild.decision == "INCLUDED")
        )
        documents: dict[str, dict] = {}
        for occurrence, canonical in rows:
            entry = documents.setdefault(canonical.id, {
                "canonical_text": canonical.canonical_text,
                "text_hash": hashlib.sha256(canonical.canonical_text.encode("utf-8")).hexdigest(),
                "variants": set(), "topic_ids": set(), "question_types": set(),
            })
            entry["variants"].add(occurrence.raw_question)
            entry["topic_ids"].add(occurrence.topic_id)
            entry["question_types"].add(occurrence.question_type)
        cached_vectors = {
            cached.text_hash: cached.vector
            for cached in session.scalars(select(EmbeddingCache).where(
                EmbeddingCache.text_hash.in_({document["text_hash"] for document in documents.values()}),
                EmbeddingCache.embedding_version == retriever.encoder.version,
                EmbeddingCache.dimension == retriever.encoder.dimension,
            ))
            if _valid_embedding(cached.vector, retriever.encoder.dimension)
        }
    physical = f"{retriever.alias}_r{revision}_{SCHEMA_VERSION}_{uuid4().hex[:8]}"
    mapping = {"mappings": {"properties": {
        "canonical_text": {"type": "keyword"},
        "variants": {"type": "keyword"},
        "search_tokens": {"type": "text", "analyzer": "whitespace"},
        "variants_tokens": {"type": "text", "analyzer": "whitespace"},
        "topic_ids": {"type": "keyword"},
        "question_types": {"type": "keyword"},
        "embedding": {"type": "dense_vector", "dims": retriever.encoder.dimension,
                      "index": True, "similarity": "cosine"},
        "embedding_version": {"type": "keyword"},
        "schema_version": {"type": "keyword"},
        "corpus_revision": {"type": "integer"},
    }}}
    retriever._request("put", f"/{physical}", json=mapping)
    for canonical_id, document in sorted(documents.items()):
        variants = sorted(document["variants"])
        vector = cached_vectors.get(document["text_hash"])
        if vector is None:
            vector = retriever.encoder.embed(document["canonical_text"])
        if not _valid_embedding(vector, retriever.encoder.dimension):
            raise ValueError("INVALID_INDEX_EMBEDDING")
        payload = {
            "canonical_text": document["canonical_text"],
            "variants": variants,
            "search_tokens": tokenize(document["canonical_text"]),
            "variants_tokens": " ".join(tokenize(variant) for variant in variants),
            "topic_ids": sorted(document["topic_ids"]),
            "question_types": sorted(document["question_types"]),
            "embedding": vector,
            "embedding_version": retriever.encoder.version,
            "schema_version": SCHEMA_VERSION,
            "corpus_revision": revision,
        }
        retriever._request("put", f"/{physical}/_doc/{canonical_id}", json=payload)
    retriever._request("post", f"/{physical}/_refresh")
    count = retriever._request("get", f"/{physical}/_count")["count"]
    if count != len(documents):
        raise ValueError("INDEX_DOCUMENT_COUNT_MISMATCH")
    with database.session() as session:
        if session.get(CorpusState, 1).current_revision != revision:
            raise ValueError("SNAPSHOT_CHANGED")
    try:
        existing = retriever._request("get", f"/_alias/{retriever.alias}")
        old_names = list(existing)
    except httpx.HTTPStatusError as error:
        if error.response.status_code != 404:
            raise
        old_names = []
    actions = [{"remove": {"index": name, "alias": retriever.alias}} for name in old_names]
    actions.append({"add": {"index": physical, "alias": retriever.alias}})
    retriever._request("post", "/_aliases", json={"actions": actions})
    with database.session() as session:
        with session.begin():
            state = session.get(CorpusState, 1)
            if state.current_revision != revision:
                raise ValueError("SNAPSHOT_CHANGED")
            state.indexed_revision = revision
            for task in session.scalars(select(IndexSyncTask).where(IndexSyncTask.corpus_revision <= revision)):
                task.state = "SUCCEEDED"
    return revision
