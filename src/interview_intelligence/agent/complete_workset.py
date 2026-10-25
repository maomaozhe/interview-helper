"""Signed, durable progress over an explicitly authorized complete candidate set.

The candidate universe is not a business-question count.  Only processed
CONFIRMED/PENDING rows become visible, and their order never changes as later
batches append.  No provider or database work happens in this module.
"""
from copy import deepcopy

from interview_intelligence.agent.search_pagination import KIND, _digest
from interview_intelligence.analytics.scope import sign_scope, verify_scope
from interview_intelligence.relevance_scope import validate_relevance_scope

VERSION = "complete_candidate_workset_v1"
STATE_KEY = "candidate_workset_snapshot"
BATCH_SIZE = 20
STATUSES = {"UNPROCESSED", "CONFIRMED", "PENDING", "EXCLUDED"}
PRIVATE_META = {"rerank_audit", "retrieval_rank_audit", "catalog_selection_audit",
    "workset_preparation_audit", "workset_batch_audit", "harness", "timings"}


def is_workset(state):
    return (state.get(STATE_KEY) or {}).get("version") == VERSION


def _body(snapshot):
    return {key: value for key, value in snapshot.items() if key not in {"state_hash", "signature"}}


def _bound(run, snapshot, user_id):
    return {"kind": KIND, "workset": VERSION, "owner": user_id,
        "conversation": run.conversation_id, "source_run_id": snapshot["source_run_id"],
        "scope_hash": snapshot["scope_hash"], "filters_hash": _digest(snapshot["filters"]),
        "revisions": list(run.revisions), "page_size": snapshot["page_size"],
        "manifest_hash": snapshot["manifest"]["manifest_hash"], "state_hash": snapshot["state_hash"]}


def seal(run, *, signing_key, user_id):
    snapshot = run.state[STATE_KEY]
    snapshot["state_hash"] = _digest(_body(snapshot))
    snapshot["signature"] = sign_scope(_bound(run, snapshot, user_id), key=signing_key,
        corpus_revision=run.revisions[0])


def validate(run, *, signing_key, user_id):
    snapshot = run.state.get(STATE_KEY) or {}
    scope = validate_relevance_scope(run.state.get("relevance_scope"))
    if snapshot.get("version") != VERSION or snapshot.get("scope_hash") != scope["scope_hash"]:
        raise ValueError("SEARCH_CANDIDATE_SCOPE_CHANGED")
    if snapshot.get("revisions") != list(run.revisions):
        raise ValueError("SNAPSHOT_CHANGED")
    if snapshot.get("state_hash") != _digest(_body(snapshot)):
        raise ValueError("SEARCH_CANDIDATE_SNAPSHOT_INVALID")
    payload = verify_scope(snapshot.get("signature", ""), key=signing_key, corpus_revision=run.revisions[0])
    if any(payload.get(key) != value for key, value in _bound(run, snapshot, user_id).items()):
        raise ValueError("INVALID_SCOPE")
    ids, frontier, statuses = snapshot["manifest"]["candidate_ids"], snapshot["frontier"], snapshot["statuses"]
    if (len(ids) != len(set(ids)) or set(statuses) != set(ids) or type(frontier) is not int
            or not 0 <= frontier <= len(ids) or any(value not in STATUSES for value in statuses.values())
            or any(statuses[identity] == "UNPROCESSED" for identity in ids[:frontier])
            or any(statuses[identity] != "UNPROCESSED" for identity in ids[frontier:])):
        raise ValueError("SEARCH_CANDIDATE_SNAPSHOT_INVALID")
    visible = [identity for identity in ids[:frontier] if statuses[identity] in {"CONFIRMED", "PENDING"}]
    if [row["canonical_question_id"] for row in snapshot["rows"]] != visible:
        raise ValueError("SEARCH_CANDIDATE_SNAPSHOT_INVALID")
    return snapshot


def create(run, manifest, plan, meta, *, signing_key, user_id):
    scope = validate_relevance_scope(run.state["relevance_scope"])
    ids = manifest["candidate_ids"]
    if not isinstance(ids, list) or any(not isinstance(identity, str) for identity in ids) or len(ids) != len(set(ids)):
        raise ValueError("SEARCH_CANDIDATE_SNAPSHOT_INVALID")
    run.state[STATE_KEY] = {"version": VERSION, "manifest": deepcopy(manifest), "kind": KIND,
        "scope_hash": scope["scope_hash"], "source_run_id": run.id, "revisions": list(run.revisions),
        "filters": plan.filters.model_dump(mode="json"), "page_size": plan.page_size,
        "root_plan": plan.model_dump(mode="json"), "frontier": 0, "rows": [],
        "statuses": dict.fromkeys(ids, "UNPROCESSED"), "last_batch_count": 0,
        "last_batch_run_id": None, "last_batch_page_offset": None, "batch_attempt": None, "batch_receipts": [],
        "meta": deepcopy({key: value for key, value in meta.items() if key not in PRIVATE_META})}
    run.state.pop("semantic_candidate_pages", None)
    seal(run, signing_key=signing_key, user_id=user_id)


def offset(run, *, initial=False, signing_key, user_id):
    snapshot = validate(run, signing_key=signing_key, user_id=user_id)
    if initial:
        return 0
    cursor = (run.state.get("list_request") or {}).get("cursor")
    if not cursor:
        if snapshot["frontier"] < len(snapshot["manifest"]["candidate_ids"]):
            raise ValueError("INVALID_SCOPE")
        return len(snapshot["rows"])
    payload = verify_scope(cursor, key=signing_key, corpus_revision=run.revisions[0])
    if any(payload.get(key) != value for key, value in _bound(run, snapshot, user_id).items()):
        raise ValueError("INVALID_SCOPE")
    result = payload.get("offset")
    if type(result) is not int or not 0 <= result <= len(snapshot["rows"]):
        raise ValueError("INVALID_SCOPE")
    return result


def batch_ids(snapshot, page_offset):
    if page_offset < len(snapshot["rows"]):
        return []
    return snapshot["manifest"]["candidate_ids"][snapshot["frontier"]:snapshot["frontier"] + BATCH_SIZE]


def start_batch(run, identities, page_offset, *, signing_key, user_id):
    snapshot = validate(run, signing_key=signing_key, user_id=user_id)
    if snapshot.get("batch_attempt"):
        raise ValueError("WORKSET_BATCH_OUTCOME_UNAVAILABLE")
    if identities != batch_ids(snapshot, page_offset) or not identities:
        raise ValueError("WORKSET_BATCH_INVALID")
    snapshot["batch_attempt"] = {"run_id": run.id, "manifest_hash": snapshot["manifest"]["manifest_hash"],
        "candidate_ids": list(identities), "page_offset": page_offset}
    seal(run, signing_key=signing_key, user_id=user_id)


def apply_batch(run, identities, result, *, signing_key, user_id, page_offset=0):
    snapshot = validate(run, signing_key=signing_key, user_id=user_id)
    if (type(identities) is not list or not identities or len(identities) > BATCH_SIZE
            or identities != snapshot["manifest"]["candidate_ids"][snapshot["frontier"]:snapshot["frontier"] + BATCH_SIZE]
            or type(result) is not dict):
        raise ValueError("WORKSET_BATCH_INVALID")
    attempt = snapshot.get("batch_attempt")
    if attempt and attempt != {"run_id": run.id, "manifest_hash": snapshot["manifest"]["manifest_hash"],
            "candidate_ids": identities, "page_offset": page_offset}:
        raise ValueError("WORKSET_BATCH_INVALID")
    # The offered window was durably reserved before any provider work. Only
    # the returned, ordered prefix was actually processed; its tail keeps the
    # original identities and UNPROCESSED state for a later request.
    processed = result.get("processed_candidate_ids", identities)
    if (type(processed) is not list or not processed
            or any(type(identity) is not str for identity in processed)
            or processed != identities[:len(processed)]):
        raise ValueError("WORKSET_BATCH_INVALID")
    outcomes, rows = result.get("outcomes", []), result.get("data", [])
    if (type(outcomes) is not list or type(rows) is not list or type(result.get("meta", {})) is not dict
            or any(type(item) is not dict or type(item.get("canonical_question_id")) is not str
                for item in [*outcomes, *rows])):
        raise ValueError("WORKSET_BATCH_INVALID")
    by_outcome = {item["canonical_question_id"]: item for item in outcomes}
    by_row = {row["canonical_question_id"]: row for row in rows}
    if (len(by_outcome) != len(outcomes)
            or set(by_outcome) != set(processed) or len(by_row) != len(rows)
            or any(type(item.get("status")) is not str or item["status"] not in STATUSES - {"UNPROCESSED"}
                for item in outcomes)
            or set(by_row) != {identity for identity, item in by_outcome.items() if item["status"] != "EXCLUDED"}):
        raise ValueError("WORKSET_BATCH_INVALID")
    for identity in processed:
        status = by_outcome[identity]["status"]
        snapshot["statuses"][identity] = status
        if status != "EXCLUDED":
            row = deepcopy(by_row[identity])
            row["relevance_scope_status"] = status
            row["rank"] = len(snapshot["rows"]) + 1
            snapshot["rows"].append(row)
    snapshot["frontier"] += len(processed)
    snapshot["last_batch_count"] = len(processed)
    snapshot["last_batch_run_id"] = run.id
    snapshot["last_batch_page_offset"] = page_offset
    snapshot["batch_attempt"] = None
    snapshot["batch_receipts"].append({"source_run_id": run.id, "candidate_ids": list(processed),
        "offered_candidate_ids": list(identities),
        "outcomes": deepcopy(outcomes), "meta": deepcopy(result.get("meta", {}))})
    snapshot["meta"].update(deepcopy({key: value for key, value in result.get("meta", {}).items()
        if key not in PRIVATE_META}))
    seal(run, signing_key=signing_key, user_id=user_id)


def page(run, page_offset, *, signing_key, user_id, initial_meta=None):
    snapshot = validate(run, signing_key=signing_key, user_id=user_id)
    rows = deepcopy(snapshot["rows"][page_offset:page_offset + snapshot["page_size"]])
    end = page_offset + len(rows)
    counts = {status: sum(value == status for value in snapshot["statuses"].values()) for status in STATUSES}
    unprocessed = counts["UNPROCESSED"]
    requires_processing = end >= len(snapshot["rows"]) and bool(unprocessed)
    cursor = sign_scope({**_bound(run, snapshot, user_id), "offset": end}, key=signing_key,
        corpus_revision=run.revisions[0]) if end < len(snapshot["rows"]) or unprocessed else None
    listing = {"kind": KIND, "cursor": cursor, "page_size": snapshot["page_size"],
        "scope_hash": snapshot["scope_hash"], "filters": snapshot["filters"], "workset": True}
    run.state["list_request"] = listing
    progress = {"version": "candidate_workset_progress_v1", "candidate_total": len(snapshot["statuses"]),
        "processed": snapshot["frontier"], "unprocessed": unprocessed, "confirmed": counts["CONFIRMED"],
        "pending": counts["PENDING"], "excluded": counts["EXCLUDED"], "visible": len(snapshot["rows"]),
        "processing_complete": not unprocessed, "batch_size": BATCH_SIZE,
        "last_batch_count": snapshot["last_batch_count"], "next_requires_processing": requires_processing}
    scope = validate_relevance_scope(run.state["relevance_scope"])
    meta = deepcopy(initial_meta if initial_meta is not None else snapshot["meta"])
    meta.update(route="SEARCH", relevance_status="VERIFIED", relevance_scope=scope,
        ranking_scope="complete_candidate_workset",
        relevance_scope_hash=scope["scope_hash"], candidate_workset_progress=progress,
        scope_candidate_counts={"visible": progress["visible"], "confirmed": progress["confirmed"], "pending": progress["pending"]},
        source_query_run_id=snapshot["source_run_id"], returned_count=len(rows), list_request=listing,
        result_completeness={"complete": not unprocessed and not counts["PENDING"],
            "reason": "candidate_workset_unprocessed" if unprocessed else "candidate_workset_pending" if counts["PENDING"] else "complete_authorized_candidate_workset",
            "candidate_universe_complete": True, "business_membership_complete": not unprocessed and not counts["PENDING"]},
        pagination={"kind": KIND, "workset": True, "offset": page_offset, "result_total": len(snapshot["rows"]),
            "page_size": snapshot["page_size"], "next_cursor": cursor, "has_unprocessed": bool(unprocessed),
            "next_requires_processing": requires_processing, "processing_complete": not unprocessed, "complete": not unprocessed})
    meta.pop("candidate_pool_limit", None)
    return {"data": rows, "meta": meta}
