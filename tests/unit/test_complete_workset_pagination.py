from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from interview_intelligence.agent import complete_workset as pages
from interview_intelligence.agent.query_contract import QuerySpec
from interview_intelligence.agent.query_scope import engineering_all_source_messages
from interview_intelligence.relevance_scope import compile_relevance_scope


def setup(count=63):
    scope = compile_relevance_scope(["所有工程系统设计题 具体业务系统设计"], candidate_mode=True)
    run = SimpleNamespace(id="run", conversation_id="conversation", revisions=(7, 3, 0), state={"relevance_scope": scope})
    plan = QuerySpec(action="SEARCH", search_query="具体业务系统设计", page_size=5)
    manifest = {"candidate_ids": [f"q-{index:03}" for index in range(count)], "manifest_hash": "m"}
    pages.create(run, manifest, plan, {}, signing_key="secret", user_id="actor")
    return run


def process(run, offset, status="CONFIRMED"):
    identities = pages.batch_ids(run.state[pages.STATE_KEY], offset)
    pages.apply_batch(run, identities, {"data": [{"canonical_question_id": identity} for identity in identities]
        if status != "EXCLUDED" else [], "outcomes": [{"canonical_question_id": identity, "status": status}
            for identity in identities], "meta": {}}, signing_key="secret", user_id="actor")


def test_complete_ids_over_fifty_are_stable_and_cached_pages_need_no_batch():
    run = setup()
    seen, batches = [], []
    offset = 0
    while True:
        ids = pages.batch_ids(run.state[pages.STATE_KEY], offset)
        if ids:
            batches.append(len(ids))
            process(run, offset)
        result = pages.page(run, offset, signing_key="secret", user_id="actor")
        seen.extend(row["canonical_question_id"] for row in result["data"])
        if not result["meta"]["pagination"]["next_cursor"]:
            break
        offset = pages.offset(run, signing_key="secret", user_id="actor")
    assert batches == [20, 20, 20, 3]
    assert seen == [f"q-{index:03}" for index in range(63)]
    assert result["meta"]["candidate_workset_progress"]["unprocessed"] == 0
    assert "candidate_pool_limit" not in result["meta"]


def test_negative_batch_keeps_a_continuation_and_pending_is_never_semantically_complete():
    run = setup(23)
    process(run, 0, "EXCLUDED")
    result = pages.page(run, 0, signing_key="secret", user_id="actor")
    assert result["data"] == [] and result["meta"]["pagination"]["next_cursor"]
    assert result["meta"]["candidate_workset_progress"]["next_requires_processing"] is True
    offset = pages.offset(run, signing_key="secret", user_id="actor")
    process(run, offset, "PENDING")
    result = pages.page(run, offset, signing_key="secret", user_id="actor")
    assert len(result["data"]) == 3
    assert result["meta"]["candidate_workset_progress"]["processing_complete"] is True
    assert result["meta"]["result_completeness"]["business_membership_complete"] is False


@pytest.mark.parametrize("mutation", ["rows", "frontier", "owner", "conversation", "page_size", "filters", "revisions"])
def test_signed_workset_binds_every_security_and_processing_dimension(mutation):
    run = setup()
    process(run, 0)
    pages.page(run, 0, signing_key="secret", user_id="actor")
    if mutation == "rows": run.state[pages.STATE_KEY]["rows"].pop()
    elif mutation == "frontier": run.state[pages.STATE_KEY]["frontier"] += 1
    elif mutation == "conversation": run.conversation_id = "different"
    elif mutation == "page_size": run.state[pages.STATE_KEY]["page_size"] = 7
    elif mutation == "filters": run.state[pages.STATE_KEY]["filters"]["company"] = "other"
    elif mutation == "revisions": run.revisions = (8, 3, 0)
    with pytest.raises(ValueError):
        pages.offset(run, signing_key="secret", user_id="different" if mutation == "owner" else "actor")


def test_missing_outcome_cannot_silently_advance_or_turn_into_negative():
    run = setup()
    before = deepcopy(run.state)
    with pytest.raises(ValueError, match="WORKSET_BATCH_INVALID"):
        pages.apply_batch(run, run.state[pages.STATE_KEY]["manifest"]["candidate_ids"][:20],
            {"outcomes": [], "data": []}, signing_key="secret", user_id="actor")
    assert run.state == before


def partial_result(identities):
    outcomes = [{"canonical_question_id": identity,
        "status": ("CONFIRMED", "PENDING", "EXCLUDED")[int(identity.removeprefix("q-")) % 3]}
        for identity in identities]
    return {"processed_candidate_ids": list(identities), "outcomes": outcomes,
        "data": [{"canonical_question_id": item["canonical_question_id"]} for item in outcomes
            if item["status"] != "EXCLUDED"], "meta": {}}


def test_partial_prefix_survives_serialized_refresh_and_pages_without_losing_the_tail():
    run = setup()
    run.state["complete_workset_mode"] = "ALL"
    original_scope = deepcopy(run.state["relevance_scope"])
    original_ids = list(run.state[pages.STATE_KEY]["manifest"]["candidate_ids"])
    seen, processed_ids = [], []
    page_offset, count = 0, 0
    while True:
        snapshot = pages.validate(run, signing_key="secret", user_id="actor")
        offered = pages.batch_ids(snapshot, page_offset)
        if offered:
            processed = offered[:(7, 1, 13)[count % 3]]
            pages.start_batch(run, offered, page_offset, signing_key="secret", user_id="actor")
            pages.apply_batch(run, offered, partial_result(processed), page_offset=page_offset,
                signing_key="secret", user_id="actor")
            count += 1
            processed_ids.extend(processed)
            receipt = snapshot["batch_receipts"][-1]
            assert receipt["candidate_ids"] == processed and receipt["offered_candidate_ids"] == offered
            assert snapshot["frontier"] == len(processed_ids)
            assert snapshot["last_batch_count"] == len(processed)
            assert all(snapshot["statuses"][identity] == "UNPROCESSED"
                for identity in original_ids[snapshot["frontier"]:])
        result = pages.page(run, page_offset, signing_key="secret", user_id="actor")
        seen.extend(row["canonical_question_id"] for row in result["data"])
        # A fresh host reads only the signed JSON state. Cached visible pages
        # must not consume the next prefix or clear the accepted ALL choice.
        run = SimpleNamespace(id=run.id, conversation_id=run.conversation_id,
            revisions=run.revisions, state=json.loads(json.dumps(run.state)))
        restored = pages.validate(run, signing_key="secret", user_id="actor")
        assert run.state["complete_workset_mode"] == "ALL" and run.state["relevance_scope"] == original_scope
        assert restored["manifest"]["candidate_ids"] == original_ids
        if not result["meta"]["pagination"]["next_cursor"]:
            break
        page_offset = pages.offset(run, signing_key="secret", user_id="actor")
        if page_offset < len(restored["rows"]):
            assert pages.batch_ids(restored, page_offset) == []
    assert processed_ids == original_ids
    assert seen == [identity for identity in original_ids if int(identity.removeprefix("q-")) % 3 != 2]
    progress = result["meta"]["candidate_workset_progress"]
    assert (progress["processed"], progress["unprocessed"], progress["pending"], progress["excluded"]) == (63, 0, 21, 21)
    assert result["meta"]["result_completeness"]["business_membership_complete"] is False


@pytest.mark.parametrize("fault", ["empty", "none", "tuple", "string", "non_string", "gap", "reorder",
    "duplicate", "extra", "missing_outcome", "tail_outcome", "tail_row", "malformed_status", "missing_field"])
def test_invalid_partial_prefix_cannot_advance_or_clear_the_durable_attempt(fault):
    run = setup()
    offered = pages.batch_ids(run.state[pages.STATE_KEY], 0)
    pages.start_batch(run, offered, 0, signing_key="secret", user_id="actor")
    before = deepcopy(run.state)
    result = partial_result(offered[:3])
    if fault == "empty": result["processed_candidate_ids"] = []
    elif fault == "none": result["processed_candidate_ids"] = None
    elif fault == "tuple": result["processed_candidate_ids"] = tuple(offered[:3])
    elif fault == "string": result["processed_candidate_ids"] = offered[0]
    elif fault == "non_string": result["processed_candidate_ids"] = [True]
    elif fault == "gap": result["processed_candidate_ids"] = offered[1:4]
    elif fault == "reorder": result["processed_candidate_ids"] = list(reversed(offered[:3]))
    elif fault == "duplicate": result["processed_candidate_ids"] = [offered[0], offered[0]]
    elif fault == "extra": result["processed_candidate_ids"] = offered + ["unoffered"]
    elif fault == "missing_outcome": result["outcomes"].pop()
    elif fault == "tail_outcome": result["outcomes"].append({"canonical_question_id": offered[3], "status": "EXCLUDED"})
    elif fault == "tail_row": result["data"].append({"canonical_question_id": offered[3]})
    elif fault == "malformed_status": result["outcomes"][0]["status"] = []
    elif fault == "missing_field": result.pop("processed_candidate_ids")
    with pytest.raises(ValueError, match="WORKSET_BATCH_INVALID"):
        pages.apply_batch(run, offered, result, signing_key="secret", user_id="actor")
    assert run.state == before
    with pytest.raises(ValueError, match="WORKSET_BATCH_OUTCOME_UNAVAILABLE"):
        pages.start_batch(run, offered, 0, signing_key="secret", user_id="actor")


def test_partial_result_must_match_the_entire_offered_attempt_and_page_offset():
    run = setup()
    offered = pages.batch_ids(run.state[pages.STATE_KEY], 0)
    pages.start_batch(run, offered, 0, signing_key="secret", user_id="actor")
    before = deepcopy(run.state)
    for identities, page_offset in ((offered[:3], 0), (offered, 1)):
        with pytest.raises(ValueError, match="WORKSET_BATCH_INVALID"):
            pages.apply_batch(run, identities, partial_result(offered[:3]), page_offset=page_offset,
                signing_key="secret", user_id="actor")
        assert run.state == before


@pytest.mark.parametrize("message,allowed", [
    ("列出所有工程系统设计题", True), ("所有工程系统设计题 具体业务系统设计", True),
    ("查看完整系统设计题", True), ("所有具体业务系统设计题", False),
    ("所有后端系统设计题", False), ("系统设计题", False),
    ("不要所有工程系统设计题", False), ("不是所有工程设计题，只找具体业务", False),
])
def test_engineering_superset_requires_positive_original_all_authorization(message, allowed):
    assert bool(engineering_all_source_messages([message])) is allowed
