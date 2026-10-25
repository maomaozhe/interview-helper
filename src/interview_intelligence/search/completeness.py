"""Keep candidate verification separate from whole-corpus coverage."""
from __future__ import annotations


def search_completeness(meta: dict, returned_count: int) -> dict:
    """Describe the executed scope without claiming semantic recall or Gold quality."""
    workset = meta.get("candidate_workset_progress")
    if isinstance(workset, dict) and workset.get("version") == "candidate_workset_progress_v1":
        return {"scope": "engineering_candidate_workset", "corpus_complete": False,
            "candidate_universe_complete": True,
            "verification_complete": workset.get("processing_complete") is True and workset.get("pending") == 0,
            "candidate_count": workset.get("candidate_total"), "processed_count": workset.get("processed"),
            "unprocessed_count": workset.get("unprocessed"), "confirmed_count": workset.get("confirmed"),
            "pending_count": workset.get("pending"), "excluded_count": workset.get("excluded"),
            "returned_count": returned_count, "business_count_complete": workset.get("processing_complete") is True
                and workset.get("pending") == 0}
    empty_scope = meta.get("verification_basis") == "empty_sql_scope"
    partial = meta.get("candidate_verification_status") == "PARTIAL"
    audit = meta.get("rerank_audit") or {}
    candidate_count = meta.get("candidate_count")
    if candidate_count is None and isinstance(meta.get("candidate_ids"), list):
        candidate_count = len(meta["candidate_ids"])
    evidence_count = meta.get("invalid_evidence_count", audit.get("invalid_evidence_count", 0))
    invalid_count = meta.get("invalid_candidate_count", 0)
    schema_count = meta.get("invalid_schema_count", audit.get("invalid_schema_count"))
    # Older retrievers only reported schema failures through invalid_count.
    if schema_count is None:
        schema_count = max(0, invalid_count - evidence_count)
    return {
        "scope": "empty_sql_scope" if empty_scope else "retrieval_candidates",
        "corpus_complete": empty_scope,
        "verification_complete": meta.get("relevance_status") == "VERIFIED" and not partial,
        "candidate_count": candidate_count,
        "eligible_count": meta.get("eligible_count"),
        "returned_count": returned_count,
        "invalid_candidate_count": invalid_count,
        "invalid_evidence_count": evidence_count,
        "invalid_schema_count": schema_count,
        "page_cutoff_count": sum(row.get("decision") == "PAGE_CUTOFF"
                                  for row in audit.get("candidates", [])),
    }


def partial_verification_warning(meta: dict) -> str:
    workset = meta.get("candidate_workset_progress")
    if isinstance(workset, dict) and workset.get("version") == "candidate_workset_progress_v1":
        return (f"工程候选全集 {workset['candidate_total']} 题，已处理 {workset['processed']} 题；"
            f"{workset['pending']} 题待核验，{workset['unprocessed']} 题尚未处理。候选总数不代表业务题总数。")
    counts = meta.get("scope_candidate_counts") or {}
    pending = counts.get("pending") if isinstance(counts, dict) else None
    if type(pending) is int and pending > 0:
        return f"{pending} 个候选尚未完成范围核验，已保留为待核验。结果仅限本次检索候选。"
    completeness = search_completeness(meta, meta.get("returned_count", 0))
    evidence, schema = (completeness[key] for key in ("invalid_evidence_count", "invalid_schema_count"))
    reasons = []
    if evidence:
        reasons.append(f"{evidence} 个证据异常候选")
    if schema:
        reasons.append(f"{schema} 个结构异常候选")
    excluded = "、".join(reasons) or f"{completeness['invalid_candidate_count']} 个异常候选"
    return f"已排除 {excluded}，这些候选未能完成核验，结果可能不完整。"
