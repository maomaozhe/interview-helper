"""Independent response review cannot be replaced by successful tool receipts."""
from copy import deepcopy

import pytest

from eval.answer_quality import score_answer, validate_answer_rubric
from eval.common import digest
from eval.validate_gold import GoldValidationError


@pytest.fixture
def rubric():
    return {"schema": "query_answer_v1", "required_basis": "CORPUS", "criteria": [
        {"id": "goal", "description": "Directly answer the frozen user's question using the evidence."},
        {"id": "coverage", "description": "Distinguish retrieved candidates from complete corpus coverage."}]}


@pytest.fixture
def actual():
    completion = {"version": "query_goal_completion_v1", "status": "COMPLETE", "tool_action": "ANSWER",
                  "terminal": True, "answer_completed": True, "tool_ordinal": 4}
    goal = {"run_id": "current-run", "response_mode": "SYNTHESIS"}
    receipt = {"name": "get_question_count", "ordinal": 2, "status": "COMPLETED", "result": {
        "intent": "COUNT", "facts": {"data": [], "meta": {"request_id": "current-request",
            "user_goal": deepcopy(goal), "counts": {"canonical_questions": 4},
            "completion": {"tool_ordinal": 2, "tool_action": "COUNT", "status": "NEEDS_RESPONSE"}}}}}
    value = {"failed": False, "run_status": "SUCCEEDED", "tool_invocations": [receipt], "result": {
        "rows": [], "answer": "There are four candidates; complete corpus coverage is unknown.",
        "plan": {"action": "ANSWER", "answer_text": "There are four candidates; complete corpus coverage is unknown.",
                 "answer_basis": "CORPUS", "question_ids": [], "evidence_tool_ordinals": [2]},
        "meta": {"request_id": "current-request", "user_goal": goal, "completion": completion,
                 "answer_basis": "CORPUS", "evidence_question_ids": [], "evidence_tool_ordinals": [2],
                 "answer_evidence": [{"ordinal": 2, "intent": "COUNT", "meta": {"counts": {"canonical_questions": 4}}}]}}}
    value["tool_invocations"].append(final_invocation(value))
    return value


def review_for(rubric, actual):
    answer = actual["result"]["plan"]["answer_text"]
    return {"answer_sha256": digest(answer.encode("utf-8")), "rubric_sha256": digest(rubric),
        "reviewer_kind": "agent", "reviewer": "independent-reviewer", "reviewed_at": "2026-10-25T02:00:00+08:00",
        "independent": True, "assessments": {
            "goal": {"passed": True, "evidence_quote": "four candidates", "reason": "Matches the independently frozen count."},
            "coverage": {"passed": True, "evidence_quote": "complete corpus coverage is unknown",
                         "reason": "Does not claim exhaustive retrieval."}}}


def results(rubric, actual, *, review=None):
    checks, diagnostics = score_answer({"answer_rubric": rubric}, actual, review=review)
    return {check["id"]: check["status"] for check in checks}, diagnostics


def test_legacy_turns_have_no_new_requirements():
    assert score_answer({"message": "List questions"}, {"failed": False}) == ([], {})


def test_host_complete_without_independent_review_remains_unknown(rubric, actual):
    checks, diagnostics = results(rubric, actual)
    assert checks["answer.host_completion"] == "PASS"
    assert checks["answer.current_evidence"] == "PASS"
    assert checks["answer.semantic.goal"] == checks["answer.semantic.coverage"] == "UNKNOWN"
    assert diagnostics["engineering_status"] == "PASS"
    assert diagnostics["status"] == "UNKNOWN"


def test_bound_review_and_real_ordinal_gap_can_pass(rubric, actual):
    checks, diagnostics = results(rubric, actual, review=review_for(rubric, actual))
    assert set(checks.values()) == {"PASS"}
    assert diagnostics["status"] == diagnostics["semantic_status"] == "PASS"


def test_old_receipt_can_supply_ordinal_from_completion_not_array_index(rubric, actual):
    actual["tool_invocations"][0].pop("ordinal")
    assert results(rubric, actual)[0]["answer.current_evidence"] == "PASS"
    actual["result"]["plan"]["evidence_tool_ordinals"] = [1]
    actual["result"]["meta"]["evidence_tool_ordinals"] = [1]
    actual["result"]["meta"].pop("answer_evidence")
    assert results(rubric, actual)[0]["answer.current_evidence"] == "FAIL"


@pytest.mark.parametrize("mutation", [
    "empty_text", "list_instead_of_answer", "failed", "running", "nonterminal", "no_answer_completed",
    "wrong_basis", "display_mismatch", "meta_display_mismatch", "host_basis_mismatch"])
def test_engineering_failure_cannot_be_hidden_by_semantic_pass(rubric, actual, mutation):
    plan, meta = actual["result"]["plan"], actual["result"]["meta"]
    if mutation == "empty_text":
        plan["answer_text"] = " "
        actual["result"]["answer"] = " "
    elif mutation == "list_instead_of_answer":
        plan["action"] = "LIST"
    elif mutation == "failed":
        actual["failed"] = True
    elif mutation == "running":
        actual["run_status"] = "RUNNING"
    elif mutation == "nonterminal":
        meta["completion"]["terminal"] = False
    elif mutation == "no_answer_completed":
        meta["completion"]["answer_completed"] = False
    elif mutation == "wrong_basis":
        plan["answer_basis"] = "MIXED"
    elif mutation == "display_mismatch":
        actual["result"]["answer"] = "Different displayed response."
    elif mutation == "meta_display_mismatch":
        meta["answer_text"] = "Different displayed response."
    else:
        meta["answer_basis"] = "GENERAL_KNOWLEDGE"
    review = review_for(rubric, actual)
    if mutation == "empty_text":
        for assessment in review["assessments"].values():
            assessment.update(passed=False, evidence_quote="")
    assert results(rubric, actual, review=review)[1]["status"] == "FAIL"


@pytest.mark.parametrize("missing", ["completion", "run_status", "tool_invocations"])
def test_missing_runtime_evidence_cannot_be_invented_from_review(rubric, actual, missing):
    if missing == "completion":
        actual["result"]["meta"].pop(missing)
    else:
        actual.pop(missing)
    checks, diagnostics = results(rubric, actual, review=review_for(rubric, actual))
    assert diagnostics["status"] == "UNKNOWN"
    assert diagnostics["semantic_status"] == "PASS"
    assert "UNKNOWN" in checks.values()


@pytest.mark.parametrize("mutation", ["failed_read", "future_read", "other_request", "other_run", "answer_receipt",
    "mismatched_ordinal", "duplicate_ordinal", "projected_ordinal", "projected_action", "missing_reference",
    "invalid_reference", "duplicate_reference", "bool_reference"])
def test_invalid_or_historical_receipts_do_not_prove_answer_evidence(rubric, actual, mutation):
    receipt = actual["tool_invocations"][0]
    plan, meta = actual["result"]["plan"], actual["result"]["meta"]
    if mutation == "failed_read":
        receipt["status"] = "FAILED"
    elif mutation == "future_read":
        meta["completion"]["tool_ordinal"] = 2
    elif mutation == "other_request":
        receipt["result"]["facts"]["meta"]["request_id"] = "old-request"
    elif mutation == "other_run":
        receipt["result"]["facts"]["meta"]["user_goal"]["run_id"] = "old-run"
    elif mutation == "answer_receipt":
        receipt["result"]["intent"] = "ANSWER"
    elif mutation == "mismatched_ordinal":
        receipt["ordinal"] = 3
    elif mutation == "duplicate_ordinal":
        actual["tool_invocations"].append(deepcopy(receipt))
    elif mutation == "projected_ordinal":
        meta["answer_evidence"][0]["ordinal"] = 1
    elif mutation == "projected_action":
        meta["answer_evidence"][0]["intent"] = "SEARCH"
    elif mutation == "missing_reference":
        plan["evidence_tool_ordinals"] = []
    elif mutation == "invalid_reference":
        plan["evidence_tool_ordinals"] = "2"
    elif mutation == "duplicate_reference":
        plan["evidence_tool_ordinals"] = [2, 2]
    else:
        plan["evidence_tool_ordinals"] = [True]
    assert results(rubric, actual)[0]["answer.current_evidence"] == "FAIL"


def test_missing_receipt_ordinal_stays_unknown(rubric, actual):
    actual["tool_invocations"][0].pop("ordinal")
    actual["tool_invocations"][0]["result"]["facts"]["meta"].pop("completion")
    assert results(rubric, actual)[0]["answer.current_evidence"] == "UNKNOWN"


def test_question_id_without_fresh_read_needs_independent_detail_proof(rubric, actual):
    plan, meta = actual["result"]["plan"], actual["result"]["meta"]
    plan.update(question_ids=["q1"], evidence_tool_ordinals=[])
    meta.update(evidence_question_ids=["q1"], evidence_tool_ordinals=[])
    meta.pop("answer_evidence")
    actual["result"]["rows"] = [{"canonical_question_id": "q1"}]
    actual["tool_invocations"][-1] = final_invocation(actual)
    assert results(rubric, actual)[0]["answer.current_evidence"] == "UNKNOWN"
    actual["tool_invocations"][0]["result"]["facts"]["data"] = [{"canonical_question_id": "q1"}]
    assert results(rubric, actual)[0]["answer.current_evidence"] == "PASS"


def final_invocation(actual):
    result = actual["result"]
    return {"name": "answer_question", "ordinal": result["meta"]["completion"]["tool_ordinal"],
        "status": "COMPLETED", "run_id": "current-run", "result": {"intent": "ANSWER",
        "answer": result["plan"]["answer_text"], "planning": {"spec": deepcopy(result["plan"])},
        "facts": {"data": deepcopy(result["rows"]), "meta": deepcopy(result["meta"])}}}


def test_legal_followup_can_use_persisted_answer_details_without_false_failure(rubric, actual):
    plan, meta = actual["result"]["plan"], actual["result"]["meta"]
    plan.update(question_ids=["q1"], evidence_tool_ordinals=[])
    meta.update(evidence_question_ids=["q1"], evidence_tool_ordinals=[])
    meta.pop("answer_evidence")
    actual["result"]["rows"] = [{"canonical_question_id": "q1", "occurrence_count": 1,
        "sources": [{"occurrence_id": "occ1", "revision_id": "rev1", "raw_file_hash": "0" * 64,
                     "start_char": 0, "end_char": 8, "quote": "Question"}]}]
    actual["tool_invocations"] = [final_invocation(actual)]
    checks, diagnostics = results(rubric, actual, review=review_for(rubric, actual))
    assert checks["answer.persisted_final"] == "PASS"
    assert checks["answer.current_evidence"] == "PASS"
    assert diagnostics["status"] == "PASS"  # Engineering shape does not certify source bytes or semantics.


@pytest.mark.parametrize("mutation", ["text", "basis", "references", "action", "status", "run"])
def test_persisted_final_answer_must_match_actual_response(rubric, actual, mutation):
    saved = final_invocation(actual)
    actual["tool_invocations"][-1] = saved
    if mutation == "text":
        saved["result"]["planning"]["spec"]["answer_text"] = "Other response"
    elif mutation == "basis":
        saved["result"]["planning"]["spec"]["answer_basis"] = "GENERAL_KNOWLEDGE"
    elif mutation == "references":
        saved["result"]["planning"]["spec"]["evidence_tool_ordinals"] = [1]
    elif mutation == "action":
        saved["result"]["intent"] = "LIST"
    elif mutation == "status":
        saved["status"] = "FAILED"
    else:
        saved["run_id"] = "old-run"
    assert results(rubric, actual)[0]["answer.persisted_final"] == "FAIL"


@pytest.mark.parametrize("key,value,expected", [
    ("tool_action", "COUNT", "FAIL"), ("version", "old-completion", "FAIL"),
    ("tool_ordinal", True, "FAIL"), ("tool_ordinal", 0, "FAIL"),
    ("version", None, "UNKNOWN"), ("tool_action", None, "UNKNOWN")])
def test_completion_cannot_be_copied_from_another_tool(rubric, actual, key, value, expected):
    if value is None:
        actual["result"]["meta"]["completion"].pop(key)
    else:
        actual["result"]["meta"]["completion"][key] = value
    assert results(rubric, actual)[0]["answer.host_completion"] == expected


def test_completed_search_with_missing_coverage_is_not_complete_evidence(rubric, actual):
    read = actual["tool_invocations"][0]
    read["result"]["intent"] = "SEARCH"
    read["result"]["facts"]["meta"]["completion"]["tool_action"] = "SEARCH"
    actual["result"]["meta"]["answer_evidence"][0]["intent"] = "SEARCH"
    assert results(rubric, actual)[0]["answer.current_evidence"] == "UNKNOWN"
    read["result"]["facts"]["meta"].update(relevance_status="VERIFIED", result_completeness={
        "scope": "retrieval_candidates", "corpus_complete": False, "verification_complete": True, "returned_count": 0})
    assert results(rubric, actual)[0]["answer.current_evidence"] == "PASS"


def test_current_workset_progress_can_support_a_coverage_answer(rubric, actual):
    read = actual["tool_invocations"][0]
    read["result"]["intent"] = "NEXT"
    meta = read["result"]["facts"]["meta"]
    meta["completion"]["tool_action"] = "NEXT"
    meta.update(relevance_status="VERIFIED", result_completeness={"scope": "complete_business_workset",
        "complete": False, "candidate_universe_complete": True, "business_membership_complete": False},
        candidate_workset_progress={"version": "candidate_workset_progress_v1", "candidate_total": 30,
            "processed": 10, "unprocessed": 20, "confirmed": 5, "pending": 2, "excluded": 3,
            "visible": 7, "batch_size": 10, "last_batch_count": 10, "processing_complete": False,
            "next_requires_processing": True})
    actual["result"]["meta"]["answer_evidence"][0]["intent"] = "NEXT"
    assert results(rubric, actual)[0]["answer.current_evidence"] == "PASS"
    meta["candidate_workset_progress"]["unprocessed"] = 19
    assert results(rubric, actual)[0]["answer.current_evidence"] == "UNKNOWN"


def test_general_answer_without_persisted_final_stays_unknown(rubric, actual):
    rubric["required_basis"] = "GENERAL_KNOWLEDGE"
    actual["result"]["plan"].update(answer_basis="GENERAL_KNOWLEDGE", evidence_tool_ordinals=[])
    actual["result"]["meta"].update(answer_basis="GENERAL_KNOWLEDGE", evidence_tool_ordinals=[])
    actual["result"]["meta"].pop("answer_evidence")
    actual["tool_invocations"] = []
    checks, diagnostic = results(rubric, actual, review=review_for(rubric, actual))
    assert checks["answer.persisted_final"] == "UNKNOWN"
    assert diagnostic["status"] == "UNKNOWN"


@pytest.mark.parametrize("mutation,expected", [("no_sources", "UNKNOWN"), ("bad_hash", "FAIL"),
    ("bad_span", "FAIL"), ("zero_occurrences", "FAIL"), ("source_issue", "UNKNOWN"),
    ("altered_display", "FAIL")])
def test_followup_requires_consistent_persisted_details_and_usable_citation_shape(rubric, actual, mutation, expected):
    plan, meta = actual["result"]["plan"], actual["result"]["meta"]
    plan.update(question_ids=["q1"], evidence_tool_ordinals=[])
    meta.update(evidence_question_ids=["q1"], evidence_tool_ordinals=[])
    meta.pop("answer_evidence")
    row = {"canonical_question_id": "q1", "occurrence_count": 1, "sources": [{
        "occurrence_id": "occ1", "revision_id": "rev1", "raw_file_hash": "0" * 64,
        "start_line": 1, "end_line": 1, "quote": "Question"}]}
    if mutation == "no_sources":
        row["sources"] = []
    elif mutation == "bad_hash":
        row["sources"][0]["raw_file_hash"] = "invented"
    elif mutation == "bad_span":
        row["sources"][0]["end_line"] = 0
    elif mutation == "zero_occurrences":
        row["occurrence_count"] = 0
    elif mutation == "source_issue":
        row["source_evidence_verified"] = False
    actual["result"]["rows"] = [row]
    actual["tool_invocations"] = [final_invocation(actual)]
    if mutation == "altered_display":
        row["sources"][0]["quote"] = "Altered displayed citation"
    assert results(rubric, actual)[0]["answer.current_evidence"] == expected


def test_explicit_top_level_run_identity_rejects_stale_read(rubric, actual):
    actual["result"]["meta"]["run_id"] = "new-run"
    actual["tool_invocations"][0]["run_id"] = "old-run"
    assert results(rubric, actual)[0]["answer.current_evidence"] == "FAIL"


def test_general_knowledge_answer_needs_no_corpus_receipts_but_still_needs_review(rubric, actual):
    rubric["required_basis"] = "GENERAL_KNOWLEDGE"
    actual["result"]["plan"].update(answer_basis="GENERAL_KNOWLEDGE", evidence_tool_ordinals=[])
    actual["result"]["meta"].update(answer_basis="GENERAL_KNOWLEDGE", evidence_tool_ordinals=[])
    actual["result"]["meta"].pop("answer_evidence")
    actual["tool_invocations"] = [final_invocation(actual)]
    assert results(rubric, actual)[0]["answer.current_evidence"] == "PASS"
    assert results(rubric, actual)[1]["status"] == "UNKNOWN"
    assert results(rubric, actual, review=review_for(rubric, actual))[1]["status"] == "PASS"


@pytest.mark.parametrize("verdict", [False, None])
def test_negative_or_unknown_review_allows_empty_quote_but_does_not_pass(rubric, actual, verdict):
    review = review_for(rubric, actual)
    review["assessments"]["goal"].update(passed=verdict, evidence_quote="", reason="The answer omits the requested explanation.")
    checks, diagnostics = results(rubric, actual, review=review)
    assert checks["answer.semantic.goal"] == ("FAIL" if verdict is False else "UNKNOWN")
    assert diagnostics["status"] == ("FAIL" if verdict is False else "UNKNOWN")


def test_missing_criterion_review_is_unknown(rubric, actual):
    review = review_for(rubric, actual)
    review["assessments"].pop("coverage")
    checks, diagnostics = results(rubric, actual, review=review)
    assert checks["answer.semantic.coverage"] == "UNKNOWN"
    assert diagnostics["criterion_counts"] == {"PASS": 1, "UNKNOWN": 1}


@pytest.mark.parametrize("mutation,error", [
    ("answer_hash", "TEXT_CHANGED"), ("rubric_hash", "RUBRIC_CHANGED"),
    ("independent", "INDEPENDENT_REQUIRED"), ("reviewer", "PROVENANCE_REQUIRED"),
    ("reviewer_kind", "PROVENANCE_REQUIRED"), ("timestamp", "TIMESTAMP_INVALID"),
    ("timezone", "TIME"), ("unknown_criterion", "CRITERION_UNKNOWN"),
    ("numeric_verdict", "VERDICT_INVALID"), ("quote", "QUOTE_NOT_IN_TEXT"),
    ("empty_positive_quote", "POSITIVE_QUOTE_REQUIRED"), ("reason", "EVIDENCE_REQUIRED"),
    ("extra_assessment_field", "ASSESSMENT_INVALID")])
def test_unbound_or_malformed_review_is_rejected(rubric, actual, mutation, error):
    review = review_for(rubric, actual)
    if mutation == "answer_hash":
        review["answer_sha256"] = "0" * 64
    elif mutation == "rubric_hash":
        review["rubric_sha256"] = "0" * 64
    elif mutation == "independent":
        review["independent"] = 1
    elif mutation == "reviewer":
        review["reviewer"] = " "
    elif mutation == "reviewer_kind":
        review["reviewer_kind"] = "self"
    elif mutation == "timestamp":
        review["reviewed_at"] = "not-a-date"
    elif mutation == "timezone":
        review["reviewed_at"] = "2026-10-25T00:00:00"
    elif mutation == "unknown_criterion":
        review["assessments"]["unfrozen-goal"] = deepcopy(review["assessments"]["goal"])
    elif mutation == "numeric_verdict":
        review["assessments"]["goal"]["passed"] = 1
    elif mutation == "quote":
        review["assessments"]["goal"]["evidence_quote"] = "text that does not appear"
    elif mutation == "empty_positive_quote":
        review["assessments"]["goal"]["evidence_quote"] = ""
    elif mutation == "reason":
        review["assessments"]["goal"]["reason"] = ""
    else:
        review["assessments"]["goal"]["confidence"] = 1
    with pytest.raises(GoldValidationError, match=error):
        results(rubric, actual, review=review)


@pytest.mark.parametrize("mutation", ["schema", "basis", "empty_criteria", "duplicate_id", "description", "extra", "assertion"])
def test_invalid_gold_rubric_is_rejected(rubric, mutation):
    if mutation == "schema":
        rubric["schema"] = "future"
    elif mutation == "basis":
        rubric["required_basis"] = {"bad": "CORPUS"}
    elif mutation == "empty_criteria":
        rubric["criteria"] = []
    elif mutation == "duplicate_id":
        rubric["criteria"].append(deepcopy(rubric["criteria"][0]))
    elif mutation == "description":
        rubric["criteria"][0]["description"] = " "
    elif mutation == "extra":
        rubric["minimum_score"] = 0
    else:
        rubric["assertions"] = [{"id": "bad", "path": "result.answer", "op": "model_says_so"}]
    with pytest.raises(GoldValidationError):
        validate_answer_rubric(rubric)


def test_optional_deterministic_assertions_check_actual_turn(rubric, actual):
    rubric["assertions"] = [{"id": "coverage_statement", "path": "result.answer", "op": "contains", "value": "unknown"}]
    assert results(rubric, actual)[0]["answer.assertion.coverage_statement"] == "PASS"
    rubric["assertions"][0]["value"] = "the entire corpus has exactly four questions"
    assert results(rubric, actual)[0]["answer.assertion.coverage_statement"] == "FAIL"


def test_scoring_does_not_mutate_gold_observation_or_review(rubric, actual):
    review = review_for(rubric, actual)
    before = deepcopy((rubric, actual, review))
    results(rubric, actual, review=review)
    assert (rubric, actual, review) == before


@pytest.mark.parametrize("explicit,stored", [(True, 1), (1.0, 1), (1, True), (1, 1.0), (1, 2)])
def test_final_explicit_and_persisted_ordinals_are_exact_consistent_integers(rubric, actual, explicit, stored):
    rubric["required_basis"] = "GENERAL_KNOWLEDGE"
    actual["result"]["plan"].update(answer_basis="GENERAL_KNOWLEDGE", evidence_tool_ordinals=[])
    meta = actual["result"]["meta"]
    meta.update(answer_basis="GENERAL_KNOWLEDGE", evidence_tool_ordinals=[])
    meta.pop("answer_evidence")
    meta["completion"]["tool_ordinal"] = 1
    saved = final_invocation(actual)
    saved["ordinal"] = explicit
    saved["result"]["facts"]["meta"]["completion"]["tool_ordinal"] = stored
    actual["tool_invocations"] = [saved]
    checks, diagnostic = results(rubric, actual, review=review_for(rubric, actual))
    assert checks["answer.persisted_final"] == "FAIL"
    assert diagnostic["status"] == "FAIL"


@pytest.mark.parametrize("explicit,stored", [(True, 2), (2.0, 2), (2, True), (2, 2.0), (2, 3)])
def test_read_explicit_and_persisted_ordinals_cannot_use_bool_float_or_conflict(rubric, actual, explicit, stored):
    read = actual["tool_invocations"][0]
    read["ordinal"] = explicit
    read["result"]["facts"]["meta"]["completion"]["tool_ordinal"] = stored
    assert results(rubric, actual)[0]["answer.current_evidence"] == "FAIL"


@pytest.mark.parametrize("missing", ["all", "empty", "version", "tool_action", "terminal", "tool_ordinal"])
def test_incomplete_saved_completion_is_unknown_even_with_valid_response_completion(rubric, actual, missing):
    saved_meta = actual["tool_invocations"][-1]["result"]["facts"]["meta"]
    if missing == "all":
        saved_meta.pop("completion")
    elif missing == "empty":
        saved_meta["completion"] = {}
    else:
        saved_meta["completion"].pop(missing)
    checks, diagnostic = results(rubric, actual, review=review_for(rubric, actual))
    assert checks["answer.host_completion"] == "PASS"
    assert checks["answer.persisted_final"] == "UNKNOWN"
    assert diagnostic["status"] == "UNKNOWN"


@pytest.mark.parametrize("invalid", [True, 1, 1.0, "", " ", [], {}])
@pytest.mark.parametrize("key", ["run_id", "request_id"])
def test_matching_but_malformed_request_or_run_id_is_not_authority(rubric, actual, invalid, key):
    current_meta = actual["result"]["meta"]
    current_meta[key] = deepcopy(invalid)
    if key == "run_id":
        current_meta["user_goal"][key] = deepcopy(invalid)
    for invocation in actual["tool_invocations"]:
        invocation[key] = deepcopy(invalid)
        saved_meta = invocation["result"]["facts"]["meta"]
        saved_meta[key] = deepcopy(invalid)
        if key == "run_id":
            saved_meta["user_goal"][key] = deepcopy(invalid)
    checks, diagnostic = results(rubric, actual, review=review_for(rubric, actual))
    assert checks["answer.persisted_final"] == checks["answer.current_evidence"] == "FAIL"
    assert diagnostic["status"] == "FAIL"
