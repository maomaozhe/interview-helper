"""Durable paging of one verified, bounded semantic candidate result set."""
from copy import deepcopy
import hashlib
import json

from interview_intelligence.analytics.scope import sign_scope, verify_scope
from interview_intelligence.relevance_scope import validate_relevance_scope

VERSION = "semantic_candidate_pages_v1"
KIND = "semantic_candidates"


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":")).encode()).hexdigest()


def is_candidate_listing(state):
    return (state.get("list_request") or {}).get("kind") == KIND


def _pending(row):
    return (row.get("relevance_scope_status") or (row.get("retrieval") or {}).get(
        "relevance_scope_status")) == "PENDING"


def store_candidates(run, result, plan, *, signing_key, user_id):
    """Store all <=50 judged candidates before cutting the displayed page."""
    scope = validate_relevance_scope(run.state["relevance_scope"])
    rows = deepcopy(result["data"])
    ids = [row["canonical_question_id"] for row in rows]
    if len(rows) > 50 or len(ids) != len(set(ids)):
        raise ValueError("SEARCH_CANDIDATE_SNAPSHOT_INVALID")
    meta = deepcopy({key: value for key, value in result["meta"].items()
        if key not in {"rerank_audit", "retrieval_rank_audit", "catalog_selection_audit", "harness", "timings"}})
    counts = {"visible": len(rows), "pending": sum(_pending(row) for row in rows)}
    counts["confirmed"] = counts["visible"] - counts["pending"]
    snapshot = {"version": VERSION, "kind": KIND, "rows": rows, "meta": meta,
        "source_run_id": run.id, "scope_hash": scope["scope_hash"],
        "revisions": list(run.revisions), "filters": plan.filters.model_dump(mode="json"),
        "page_size": plan.page_size, "root_plan": plan.model_dump(mode="json"),
        "counts": counts}
    snapshot["result_hash"] = _digest({key: value for key, value in snapshot.items()
        if key not in {"meta"}})
    run.state["semantic_candidate_pages"] = snapshot
    return candidate_page(run, offset=0, signing_key=signing_key, user_id=user_id,
        initial_meta=result["meta"])


def candidate_page(run, *, offset=None, signing_key, user_id, initial_meta=None):
    snapshot = run.state.get("semantic_candidate_pages") or {}
    scope = validate_relevance_scope(run.state.get("relevance_scope"))
    if snapshot.get("version") != VERSION or snapshot.get("scope_hash") != scope["scope_hash"]:
        raise ValueError("SEARCH_CANDIDATE_SCOPE_CHANGED")
    if snapshot.get("revisions") != list(run.revisions):
        raise ValueError("SNAPSHOT_CHANGED")
    if snapshot.get("result_hash") != _digest({key: value for key, value in snapshot.items()
            if key not in {"meta", "result_hash"}}):
        raise ValueError("SEARCH_CANDIDATE_SNAPSHOT_INVALID")
    bound = {"kind": KIND, "owner": user_id, "conversation": run.conversation_id,
        "source_run_id": snapshot["source_run_id"], "scope_hash": scope["scope_hash"],
        "result_hash": snapshot["result_hash"], "revisions": list(run.revisions),
        "filters_hash": _digest(snapshot["filters"]), "page_size": snapshot["page_size"]}
    if offset is None:
        listing = run.state.get("list_request") or {}
        cursor = listing.get("cursor")
        if not cursor:
            offset = len(snapshot["rows"])
        else:
            payload = verify_scope(cursor, key=signing_key, corpus_revision=run.revisions[0])
            if any(payload.get(key) != value for key, value in bound.items()):
                raise ValueError("INVALID_SCOPE")
            offset = payload.get("offset")
    if type(offset) is not int or not 0 <= offset <= len(snapshot["rows"]):
        raise ValueError("INVALID_SCOPE")
    rows = deepcopy(snapshot["rows"][offset:offset + snapshot["page_size"]])
    next_offset = offset + len(rows)
    cursor = sign_scope({**bound, "offset": next_offset}, key=signing_key,
        corpus_revision=run.revisions[0]) if next_offset < len(snapshot["rows"]) else None
    listing = {"kind": KIND, "cursor": cursor, "page_size": snapshot["page_size"],
        "scope_hash": scope["scope_hash"], "filters": snapshot["filters"]}
    run.state["list_request"] = listing
    meta = deepcopy(initial_meta if initial_meta is not None else snapshot["meta"])
    meta.update(route="SEARCH", relevance_scope=scope,
        relevance_scope_hash=scope["scope_hash"], scope_candidate_counts=snapshot["counts"],
        candidate_pool_limit=50, source_query_run_id=snapshot["source_run_id"],
        returned_count=len(rows), list_request=listing,
        pagination={"kind": KIND, "offset": offset, "result_total": len(snapshot["rows"]),
            "page_size": snapshot["page_size"], "next_cursor": cursor,
            "candidate_pool_limit": 50, "complete": False})
    return {"data": rows, "meta": meta}
