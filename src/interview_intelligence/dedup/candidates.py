"""ANN over a pinned ES head plus an exact, transaction-visible delta.

An old index never hides canonicals created earlier in the current ingestion
transaction. Changed text/model identities also move to the exact delta. Cache
entries only name immutable index documents, not uncommitted database objects.
"""
from __future__ import annotations

import hashlib

import numpy as np

from interview_intelligence.search.elasticsearch import ElasticsearchRetriever


def text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def exact_rank(query, items, vectors, limit):
    if not items: return []
    matrix = np.asarray(vectors, dtype=np.float64)
    vector = np.asarray(query, dtype=np.float64)
    if matrix.ndim != 2 or matrix.shape[1] != len(vector) or not np.isfinite(matrix).all() or not np.isfinite(vector).all():
        raise ValueError("INVALID_CANDIDATE_VECTOR")
    norms = np.linalg.norm(matrix, axis=1)
    query_norm = np.linalg.norm(vector)
    if query_norm == 0 or np.any(norms == 0): raise ValueError("INVALID_CANDIDATE_VECTOR")
    scores = (matrix @ vector) / (norms * query_norm)
    order = np.lexsort((np.asarray([item.id for item in items]), -scores))[:limit]
    return [(float(scores[index]), items[index]) for index in order]


class ElasticsearchCandidateHead:
    version = "dedup_hnsw_head_exact_delta_v1"

    def __init__(self, url, *, client=None, alias="interview_questions", oversample=5, num_candidates=500):
        self.es = ElasticsearchRetriever(url, client=client, alias=alias)
        self.oversample, self.num_candidates = oversample, num_candidates
        self.physical = None
        self.identities = {}

    def refresh(self):
        names = sorted(self.es._request("get", f"/_alias/{self.es.alias}"))
        if len(names) != 1: raise ValueError("ANN_INDEX_ALIAS_AMBIGUOUS")
        if names[0] == self.physical: return
        physical = names[0]
        pit = self.es._request("post", f"/{physical}/_pit", params={"keep_alive":"1m"})["id"]
        identities, after = {}, None
        try:
            while True:
                body = {"pit":{"id":pit,"keep_alive":"1m"},"size":1000,"sort":["_shard_doc"],
                        "_source":["canonical_text","embedding_version"],"query":{"match_all":{}}}
                if after is not None: body["search_after"] = after
                hits = self.es._request("post","/_search",json=body,
                    params={"allow_partial_search_results":"false"})["hits"]["hits"]
                for hit in hits:
                    source = hit["_source"]
                    identities[hit["_id"]] = (text_hash(source["canonical_text"]),source["embedding_version"])
                if len(hits)<1000: break
                after = hits[-1]["sort"]
        finally:
            self.es._request("delete","/_pit",json={"id":pit})
        self.physical, self.identities = physical, identities

    def propose(self, query, existing, embedding_version, limit):
        self.refresh()
        head, delta = [], []
        for item in existing:
            (head if self.identities.get(item.id)==(text_hash(item.canonical_text),embedding_version) else delta).append(item)
        if not head:
            return delta, {"engine":"exact_delta","indexed_count":0,"delta_count":len(delta),"index":self.physical}
        count = min(len(head),limit*self.oversample)
        body = {"size":count,"_source":False,"knn":{"field":"embedding","query_vector":query,
                "k":count,"num_candidates":max(count,self.num_candidates),"filter":{"terms":{"_id":[item.id for item in head]}}}}
        hits = self.es._request("post",f"/{self.physical}/_search",json=body,
                               params={"allow_partial_search_results":"false"})["hits"]["hits"]
        by_id = {item.id:item for item in head}
        ids = [hit["_id"] for hit in hits]
        if len(ids)!=len(set(ids)) or any(key not in by_id for key in ids): raise ValueError("ANN_CANDIDATE_SCOPE_INVALID")
        return [by_id[key] for key in ids]+delta, {"engine":"hnsw_head_exact_delta","indexed_count":len(head),
            "delta_count":len(delta),"index":self.physical,"oversample":self.oversample,"num_candidates":self.num_candidates,
            "version":self.version}
