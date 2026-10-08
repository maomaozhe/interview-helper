"""Read-only exact/ANN benchmark over cached vectors on a pinned real corpus."""
from __future__ import annotations

import argparse
import time

import numpy as np
from sqlalchemy import select, text

from eval.common import digest, provenance, write_json
from eval.snapshot import assert_snapshot, current_snapshot
from interview_intelligence.config import load_settings
from interview_intelligence.dedup.candidates import ElasticsearchCandidateHead, exact_rank, text_hash
from interview_intelligence.dedup.service import cosine
from interview_intelligence.domain.models import CanonicalQuestion, EmbeddingCache, create_database


def percentiles(values):
    return {"n":len(values),"p50_ms":float(np.percentile(values,50)),"p95_ms":float(np.percentile(values,95))}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--output",required=True)
    parser.add_argument("--samples",type=int,default=60); args = parser.parse_args()
    settings = load_settings(); db = create_database(settings.database_url,create_tables=False)
    before = current_snapshot(db,settings,as_of="2026-10-05")
    version = f"{settings.embedding_model}:{settings.embedding_dimension}:instructions_v2"
    with db.session() as session:
        session.connection(execution_options={"isolation_level":"REPEATABLE READ"})
        session.execute(text("SET TRANSACTION READ ONLY"))
        items = list(session.scalars(select(CanonicalQuestion).where(CanonicalQuestion.lifecycle=="ACTIVE").order_by(CanonicalQuestion.id)))
        hashes = {text_hash(item.canonical_text) for item in items}
        rows = list(session.scalars(select(EmbeddingCache).where(EmbeddingCache.embedding_version==version,EmbeddingCache.text_hash.in_(hashes))))
        vectors = {row.text_hash:row.vector for row in rows}
        if len(vectors)!=len(hashes): raise SystemExit("INCOMPLETE_CACHED_EMBEDDINGS")
        all_vectors = [vectors[text_hash(item.canonical_text)] for item in items]
        # Stable source-vector probes measure index recall. They are not a
        # semantic SAME/RELATED judge test or newly generated query embeddings.
        indices = np.random.default_rng(20261005).choice(len(items),min(args.samples,len(items)),replace=False).tolist()
        head = ElasticsearchCandidateHead(settings.elasticsearch_url)
        start=time.perf_counter(); head.refresh(); cold=(time.perf_counter()-start)*1000
        measurements=[]; exact_ms=[]; ann_ms=[]; python_ms=[]
        for number,index in enumerate(indices):
            query=all_vectors[index]
            start=time.perf_counter(); exact=exact_rank(query,items,all_vectors,10); exact_elapsed=(time.perf_counter()-start)*1000
            exact_ids=[item.id for _,item in exact]
            start=time.perf_counter(); proposed,meta=head.propose(query,items,version,10)
            candidate_vectors=[vectors[text_hash(item.canonical_text)] for item in proposed]
            ranked=exact_rank(query,proposed,candidate_vectors,10); ann_elapsed=(time.perf_counter()-start)*1000
            ann_ids=[item.id for _,item in ranked]
            if number<args.samples:
                start=time.perf_counter()
                baseline=sorted(((cosine(query,vector),item) for item,vector in zip(items,all_vectors)),key=lambda value:(-value[0],value[1].id))[:10]
                python_ms.append((time.perf_counter()-start)*1000)
                if [item.id for _,item in baseline]!=exact_ids: raise SystemExit("EXACT_IMPLEMENTATION_MISMATCH")
            measurements.append({"query_canonical_id":items[index].id,"exact_ids":exact_ids,"ann_ids":ann_ids,
                "recall_at_10":len(set(exact_ids)&set(ann_ids))/len(exact_ids),"exact_ms":exact_elapsed,"ann_ms":ann_elapsed,
                "proposed_count":len(proposed),"candidate_search":meta})
            exact_ms.append(exact_elapsed); ann_ms.append(ann_elapsed)
    after=current_snapshot(db,settings,as_of=before["as_of"]); assert_snapshot(before,after)
    result={"kind":"cached_vector_candidate_recall","snapshot":before,"embedding_version":version,"items":len(items),
        "vector_digest":digest({item.id:vectors[text_hash(item.canonical_text)] for item in items}),"ann_index":head.physical,
        "cold_head_refresh_ms":cold,"seed":20261005,"sample_count":len(measurements),
        "mean_candidate_recall_at_10":float(np.mean([row["recall_at_10"] for row in measurements])),
        "python_exact_in_memory":percentiles(python_ms),"vectorized_exact_in_memory":percentiles(exact_ms),
        "hnsw_plus_exact_delta_and_rerank":percentiles(ann_ms),"measurements":measurements,"provenance":provenance(),
        "limitations":["Cached canonical vectors; no incoming paraphrase embeddings or semantic judge measured",
            "Exact memory load and ANN HTTP time are included; bulk DB fetch is excluded from every measured loop",
            f"One real {len(items)}-item corpus, warm caches, one worker; not a large-corpus scale claim"]}
    write_json(__import__('pathlib').Path(args.output),result)
    print({key:value for key,value in result.items() if key not in {"measurements","provenance","limitations","vector_digest"}})
