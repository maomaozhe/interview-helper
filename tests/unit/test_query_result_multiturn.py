"""Each follow-up keeps its own independent source labels and frozen controls."""
from copy import deepcopy

import pytest

from eval.common import digest, read_json, write_json
from eval.query_results import (
    build_query_results,
    score_query_results,
    source_references_for_case,
    validate_query_results,
)


@pytest.fixture
def multi_inputs(tmp_path):
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    documents, interviews, questions = [], [], []
    for number, company in ((1, "字节"), (2, "腾讯")):
        lines = [f"Synthetic source question {2 * number - 1}.", f"Synthetic source question {2 * number}."]
        source = snapshot / f"source{number}.md"
        source.write_text("\n".join(lines) + "\n", encoding="utf-8")
        documents.append({"id": f"d{number}", "build_id": f"b{number}", "revision_id": f"r{number}",
                          "decision": "INCLUDED", "source_path": source.name,
                          "source_hash": digest(source.read_bytes())})
        interviews.append({"id": f"i{number}", "build_id": f"b{number}", "analytics_eligible": True,
                           "company_normalized": company, "round": "FIRST"})
        position = 0
        for line_number, line in enumerate(lines, 1):
            identity = 2 * number - 2 + line_number
            questions.append({"id": f"q{identity}", "canonical_question_id": f"c{identity}",
                "interview_id": f"i{number}", "raw_question": line,
                "source_spans": [{"revision_id": f"r{number}", "start_char": position,
                    "end_char": position + len(line), "start_line": line_number, "end_line": line_number,
                    "quote": line}]})
            position += len(line) + 1
    facts = {"snapshot": {"corpus_revision": 1, "indexed_revision": 1, "task_annotation_revision": 1,
                          "task_annotation_policy": "KNOWN", "as_of": "2026-10-15"},
             "documents": documents, "interviews": interviews, "questions": questions,
             "canonicals": [{"id": f"c{i}"} for i in range(1, 5)], "physical_indices": ["test-index"]}
    write_json(snapshot / "facts.json", facts)
    review = {"facts_sha256": digest(facts), "candidate_predictions_seen": False, "human_verified": False,
        "reviewer": "independent-synthetic-source-reviewer", "reviewed_at": "2026-10-25T00:00:00Z",
        "queries": [{"query": name, "criteria": "Synthetic independent scope assignments.",
                     "coverage": {"scope": "judged_pool"}, "labels": [
                         {"occurrence_id": positive, "role": "POSITIVE", "reason": "Synthetic positive.",
                          "object_family": family},
                         {"occurrence_id": negative, "role": "NEGATIVE", "reason": "Different requested scope."}]}
                    for name, positive, negative, family in (
                        ("byte-cache-review", "q1", "q3", "Cache"),
                        ("tencent-scheduling-review", "q3", "q1", "Scheduling"))]}
    write_json(tmp_path / "review.json", review)
    recipe = {"id": "synthetic-multiturn", "guide_version": "test-v2", "as_of": "2026-10-25", "cases": [
        {"id": "scope-switch", "turns": [
            {"message": "找字节缓存题", "query": "byte-cache-review", "minimum_positive": 1,
             "required_company": "字节", "expected_plan": {"action": "SEARCH", "filters.company": "字节"}},
            {"message": "改成腾讯调度题", "query": "tencent-scheduling-review", "minimum_positive": 1,
             "minimum_families": 1, "required_company": "腾讯", "tool_ordinal": 2,
             "expected_plan": {"action": "SEARCH", "filters.company": "腾讯"},
             "required_tools": ["search_questions"], "forbidden_tools": ["record_review", "list_questions"],
             "tool_order": ["search_questions"], "allowed_plans": [["search_questions"]],
             "assertions": [{"id": "second_scope", "path": "result.plan.filters.company", "op": "eq", "value": "腾讯"}]},
            {"message": "解释一下排队的概念", "source_results_required": False, "expected_plan": {"action": "ANSWER"},
             "answer_rubric": {"schema": "query_answer_v1",
                               "criteria": [{"id": "definition", "description": "Defines a queue."}]}}]}]}
    write_json(tmp_path / "recipe.json", recipe)
    return tmp_path, facts, review, recipe


def freeze(multi_inputs, name="gold"):
    root, _, _, _ = multi_inputs
    target = root / name
    result = build_query_results(root / "snapshot", [root / "review.json"], root / "recipe.json", target,
                                authorization={"thread_id": "synthetic", "user_instruction": "Synthetic test only"})
    return target, read_json(target / "manifest.json"), result


def returned_turn(facts, number):
    question = facts["questions"][number - 1]
    interview = next(i for i in facts["interviews"] if i["id"] == question["interview_id"])
    document = next(d for d in facts["documents"] if d["build_id"] == interview["build_id"])
    return {"failed": False, "result": {"rows": [{"canonical_question_id": question["canonical_question_id"],
        "sources": [{"occurrence_id": question["id"], "revision_id": document["revision_id"],
                     "raw_file_hash": document["source_hash"], **question["source_spans"][0]}]}]}}


def test_each_scope_uses_its_own_labels_not_the_first_turn(multi_inputs):
    dataset, manifest, _ = freeze(multi_inputs)
    context = validate_query_results(dataset, manifest)
    references = source_references_for_case(context["reference"], manifest["routing"][0])
    assert len(references) == 3 and references[2] is None
    first, _ = score_query_results(references[0], returned_turn(multi_inputs[1], 1), context["rows"])
    assert all(check["status"] == "PASS" for check in first)
    wrong, diagnostics = score_query_results(references[1], returned_turn(multi_inputs[1], 1), context["rows"])
    assert diagnostics["roles"] == {"NEGATIVE": 1}
    assert diagnostics["covered_positive_canonicals"] == 0
    assert next(check for check in wrong if check["id"] == "source_results.same_occurrence_company")["status"] == "FAIL"
    correct, _ = score_query_results(references[1], returned_turn(multi_inputs[1], 3), context["rows"])
    assert all(check["status"] == "PASS" for check in correct)


@pytest.mark.parametrize("second_receipt", ["correct", "first_scope", "missing"])
def test_v2_score_query_selects_current_turn_receipt_by_ordinal(multi_inputs, second_receipt):
    from eval.scoring import score_query

    dataset, manifest, _ = freeze(multi_inputs)
    context = validate_query_results(dataset, manifest)
    case = deepcopy(manifest["routing"][0])
    case["turns"] = case["turns"][:2]
    context["reference"]["queries"][case["id"]]["turns"] = context["reference"]["queries"][case["id"]]["turns"][:2]
    actual_turns = []
    for index, (company, question) in enumerate((("字节", 1), ("腾讯", 3))):
        actual = returned_turn(multi_inputs[1], question)
        actual.update(run_id=f"run-{index}", run_status="SUCCEEDED", tool_trace=[{"name": "search_questions"}])
        actual["result"].update(plan={"action": "SEARCH", "filters": {"company": company}}, write_event_delta=0,
                                meta={"conversation_id": "synthetic-conversation", "conversation_version": index + 1})
        actual_turns.append(actual)
    # The final displayed rows are correct in every variant. The selected tool
    # receipt is the independent source evidence under test, not its list index.
    receipt_rows = returned_turn(multi_inputs[1], 1 if second_receipt == "first_scope" else 3)["result"]["rows"]
    selected = {"ordinal": 2, "run_id": "run-1", "status": "COMPLETED", "result": {"intent": "SEARCH",
        "facts": {"data": receipt_rows, "meta": {"completion": {"tool_ordinal": 2}}},
        "planning": {"spec": actual_turns[1]["result"]["plan"]}}}
    distractor = deepcopy(selected)
    distractor["ordinal"] = distractor["result"]["facts"]["meta"]["completion"]["tool_ordinal"] = 1
    distractor["result"]["facts"]["data"] = returned_turn(multi_inputs[1], 1)["result"]["rows"]
    actual_turns[1]["tool_invocations"] = [selected, distractor] if second_receipt != "missing" else [distractor]
    metrics, scored = score_query([case], [{"id": case["id"], "turns": actual_turns}], result_reference=context)
    row = scored[0]
    if second_receipt == "correct":
        assert row["status"] == "PASS"
        assert metrics["source_results"]["evaluated_turns"] == 2
        assert metrics["source_results"]["returned_roles"] == {"POSITIVE": 2}
    elif second_receipt == "first_scope":
        assert row["status"] == "FAIL"
        assert next(check for check in row["checks"] if check["id"] == "turn1.source_results.no_negative")["status"] == "FAIL"
        assert row["scores"]["source_results"]["turns"][1]["roles"] == {"NEGATIVE": 1}
    else:
        assert row["status"] == "UNKNOWN"
        assert next(check for check in row["checks"] if check["id"] == "turn1.source_results.receipt")["status"] == "UNKNOWN"
        assert metrics["source_results"]["mean_judged_pool_positive_coverage"] is None


def test_freeze_retains_review_lookup_original_turns_controls_and_all_hashes(multi_inputs):
    dataset, manifest, result = freeze(multi_inputs)
    reference = manifest["query_result_reference"]
    assert reference["schema"] == "source_query_results_v2"
    refs = source_references_for_case(reference, manifest["routing"][0])
    assert refs[1]["query"] == "改成腾讯调度题"
    assert refs[1]["review_query"] == "tencent-scheduling-review"
    assert refs[1]["tool_ordinal"] == 2
    assert refs[1]["labels"][0]["review"]["reviewer"] == multi_inputs[2]["reviewer"]
    turns = manifest["routing"][0]["turns"]
    for key in ("expected_plan", "required_tools", "forbidden_tools", "tool_order", "allowed_plans"):
        assert turns[1][key] == multi_inputs[3]["cases"][0]["turns"][1][key]
    assert turns[1]["assertions"][-1] == multi_inputs[3]["cases"][0]["turns"][1]["assertions"][0]
    assert turns[2]["answer_rubric"] == multi_inputs[3]["cases"][0]["turns"][2]["answer_rubric"]
    assert turns[2]["source_results_required"] is False
    assert read_json(dataset / "recipe.json") == multi_inputs[3]
    assert read_json(dataset / "reviews/01-review.json") == multi_inputs[2]
    frozen = read_json(dataset / "freeze.json")
    assert frozen["dataset_sha256"] == digest(manifest) == result["dataset_sha256"]
    assert all(digest((dataset / path).read_bytes()) == value for path, value in frozen["file_hashes"].items())
    assert result["model_calls"] == 0 and result["release_eligible"] is False
    expected = (dataset / "EXPECTED-ANSWERS.md").read_text(encoding="utf-8")
    assert "第 2 轮：改成腾讯调度题" in expected and "本轮未要求来源题单" in expected


@pytest.mark.parametrize("corruption", ["missing", "extra", "message", "reuse_first"])
def test_reference_turn_lengths_and_original_messages_are_bound(multi_inputs, corruption):
    dataset, manifest, _ = freeze(multi_inputs)
    references = manifest["query_result_reference"]["queries"]["scope-switch"]["turns"]
    if corruption == "missing":
        references.pop()
    elif corruption == "extra":
        references.append(None)
    elif corruption == "message":
        manifest["routing"][0]["turns"][1]["message"] = "换一个范围"
    else:
        references[1] = deepcopy(references[0])
    with pytest.raises(ValueError, match="QUERY_REFERENCE_(TURN_COUNT|ORIGINAL_MESSAGE)_CHANGED"):
        validate_query_results(dataset, manifest)


@pytest.mark.parametrize("action", ["SEARCH", "LIST", "NEXT", "DETAILS"])
def test_question_list_actions_cannot_opt_out_via_null_reference(multi_inputs, action):
    root, _, _, recipe = multi_inputs
    recipe["cases"][0]["turns"][2]["expected_plan"]["action"] = action
    write_json(root / "recipe.json", recipe)
    with pytest.raises(ValueError, match="QUERY_REFERENCE_SOURCE_TURN_REQUIRED"):
        freeze(multi_inputs)
    assert not (root / "gold").exists()


def test_null_requires_an_explicit_source_free_turn(multi_inputs):
    root, _, _, recipe = multi_inputs
    recipe["cases"][0]["turns"][2].pop("source_results_required")
    write_json(root / "recipe.json", recipe)
    with pytest.raises(ValueError, match="QUERY_REFERENCE_SOURCE_TURN_REQUIRED"):
        freeze(multi_inputs)
    assert not (root / "gold").exists()


@pytest.mark.parametrize("ordinal", [0, -1, True, "2", 1.5, None])
def test_tool_ordinal_must_be_a_positive_integer(multi_inputs, ordinal):
    root, _, _, recipe = multi_inputs
    recipe["cases"][0]["turns"][1]["tool_ordinal"] = ordinal
    write_json(root / "recipe.json", recipe)
    with pytest.raises(ValueError, match="QUERY_REFERENCE_TOOL_ORDINAL_INVALID"):
        freeze(multi_inputs)
    assert not (root / "gold").exists()


@pytest.mark.parametrize("corruption", ["seen_predictions", "missing_second_review", "stale_facts"])
def test_multiturn_freeze_still_requires_independent_matching_reviews(multi_inputs, corruption):
    root, _, review, _ = multi_inputs
    if corruption == "seen_predictions":
        review["candidate_predictions_seen"] = True
    elif corruption == "missing_second_review":
        review["queries"].pop()
    else:
        review["facts_sha256"] = "0" * 64
    write_json(root / "review.json", review)
    with pytest.raises(ValueError):
        freeze(multi_inputs)
    assert not (root / "gold").exists()


@pytest.mark.parametrize("artifact,error", [("recipe.json", "QUERY_REFERENCE_RECIPE_CHANGED"),
                                          ("reviews/01-review.json", "QUERY_REFERENCE_REVIEW_CHANGED")])
def test_multiturn_recipe_and_review_hashes_are_verified(multi_inputs, artifact, error):
    dataset, manifest, _ = freeze(multi_inputs)
    with (dataset / artifact).open("a", encoding="utf-8") as stream:
        stream.write("\n")
    with pytest.raises(ValueError, match=error):
        validate_query_results(dataset, manifest)


def test_single_turn_recipe_and_v1_reference_remain_compatible(multi_inputs):
    root, _, review, recipe = multi_inputs
    review["queries"] = review["queries"][:1]
    original = recipe["cases"][0]["turns"][0]
    recipe["cases"] = [{"id": "single", "query": original["query"], "minimum_positive": 1,
                        "expected_plan": original["expected_plan"]}]
    write_json(root / "review.json", review)
    write_json(root / "recipe.json", recipe)
    dataset, manifest, _ = freeze(multi_inputs)
    context = validate_query_results(dataset, manifest)
    assert context["reference"]["schema"] == "source_query_results_v1"
    refs = source_references_for_case(context["reference"], manifest["routing"][0])
    assert refs == [context["reference"]["queries"]["single"]]
    manifest["routing"][0]["turns"].append(deepcopy(manifest["routing"][0]["turns"][0]))
    with pytest.raises(ValueError, match="QUERY_REFERENCE_ORIGINAL_MESSAGE_CHANGED"):
        source_references_for_case(context["reference"], manifest["routing"][0])


def test_unified_freeze_command_supports_multiturn_without_services(multi_inputs, capsys):
    from eval.__main__ import main

    root, _, _, _ = multi_inputs
    write_json(root / "authorization.json", {"thread_id": "synthetic", "user_instruction": "Synthetic test only"})
    assert main(["freeze-queries", "--snapshot", str(root / "snapshot"), "--reviews", str(root / "review.json"),
                 "--recipe", str(root / "recipe.json"), "--authorization", str(root / "authorization.json"),
                 "--output", str(root / "gold")]) == 0
    assert '"model_calls": 0' in capsys.readouterr().out
    assert read_json(root / "gold/manifest.json")["query_result_reference"]["schema"] == "source_query_results_v2"


def _v2_source_case_and_observation(multi_inputs):
    dataset, manifest, _ = freeze(multi_inputs)
    context = validate_query_results(dataset, manifest)
    case = deepcopy(manifest["routing"][0])
    turns = []
    for index, (company, question) in enumerate((("字节", 1), ("腾讯", 3))):
        actual = returned_turn(multi_inputs[1], question)
        actual.update(run_id=f"run-{index}", run_status="SUCCEEDED", tool_trace=[{"name": "search_questions"}])
        actual["result"].update(plan={"action": "SEARCH", "filters": {"company": company}}, write_event_delta=0,
                                meta={"conversation_id": "synthetic-conversation", "conversation_version": index + 1})
        turns.append(actual)
    receipt = {"ordinal": 2, "run_id": "run-1", "status": "COMPLETED", "result": {"intent": "SEARCH",
        "facts": {"data": deepcopy(turns[1]["result"]["rows"]),
                  "meta": {"completion": {"tool_ordinal": 2, "tool_action": "SEARCH"}}},
        "planning": {"spec": deepcopy(turns[1]["result"]["plan"])}}}
    turns[1]["tool_invocations"] = [receipt]
    return context, case, turns, receipt


@pytest.mark.parametrize("corruption", ["boolean_ordinal", "ordinal_conflict", "old_internal_run",
                                        "planning_action_conflict", "completion_action_conflict"])
def test_v2_source_selector_rejects_inconsistent_selected_receipts(multi_inputs, corruption):
    from eval.scoring import score_query

    context, case, turns, receipt = _v2_source_case_and_observation(multi_inputs)
    case["turns"] = case["turns"][:2]
    context["reference"]["queries"][case["id"]]["turns"] = context["reference"]["queries"][case["id"]]["turns"][:2]
    if corruption == "boolean_ordinal":
        receipt["ordinal"] = True
    elif corruption == "ordinal_conflict":
        receipt["ordinal"] = 1
    elif corruption == "old_internal_run":
        receipt["result"]["facts"]["meta"]["user_goal"] = {"run_id": "previous-turn-run"}
    elif corruption == "planning_action_conflict":
        receipt["result"]["planning"]["spec"]["action"] = "LIST"
    else:
        receipt["result"]["facts"]["meta"]["completion"]["tool_action"] = "ANSWER"
    metrics, scored = score_query([case], [{"id": case["id"], "turns": turns}], result_reference=context)
    row = scored[0]
    assert metrics["argument_accuracy"] == 1
    assert row["status"] == "FAIL"
    assert next(check for check in row["checks"] if check["id"] == "turn1.source_results.receipt")["status"] == "FAIL"
    assert row["scores"]["source_results"]["turns"][1]["receipt_status"] == "FAIL"


def test_null_source_turn_cannot_hide_search_receipt_behind_final_answer(multi_inputs):
    from eval.scoring import score_query

    context, case, turns, _ = _v2_source_case_and_observation(multi_inputs)
    # This test isolates the null-reference boundary from semantic answer review.
    case["turns"][2].pop("answer_rubric")
    final = {"failed": False, "run_id": "run-2", "run_status": "SUCCEEDED", "tool_trace": [],
             "result": {"plan": {"action": "ANSWER"}, "rows": [], "write_event_delta": 0},
             "tool_invocations": [
                 {"ordinal": 1, "run_id": "run-2", "status": "COMPLETED", "result": {"intent": "SEARCH",
                  "facts": {"data": returned_turn(multi_inputs[1], 3)["result"]["rows"], "meta": {}}}},
                 {"ordinal": 2, "run_id": "run-2", "status": "COMPLETED", "result": {"intent": "ANSWER"}}]}
    turns.append(final)
    metrics, scored = score_query([case], [{"id": case["id"], "turns": turns}], result_reference=context)
    assert metrics["argument_accuracy"] == 1
    assert scored[0]["status"] == "FAIL"
    assert next(check for check in scored[0]["checks"]
                if check["id"] == "turn2.source_results.not_requested")["status"] == "FAIL"


@pytest.mark.parametrize("field", ["receipt_facts", "receipt_meta", "actual_result", "actual_plan", "actual_meta"])
@pytest.mark.parametrize("value", [None, []])
def test_malformed_source_observations_do_not_raise_or_pass(multi_inputs, field, value):
    from eval.scoring import score_query

    context, case, turns, receipt = _v2_source_case_and_observation(multi_inputs)
    case["turns"] = case["turns"][:2]
    context["reference"]["queries"][case["id"]]["turns"] = context["reference"]["queries"][case["id"]]["turns"][:2]
    if field == "receipt_facts":
        receipt["result"]["facts"] = value
    elif field == "receipt_meta":
        receipt["result"]["facts"]["meta"] = value
    elif field == "actual_result":
        turns[1]["result"] = value
    else:
        turns[1]["result"]["plan" if field == "actual_plan" else "meta"] = value
    metrics, scored = score_query([case], [{"id": case["id"], "turns": turns}], result_reference=context)
    assert scored[0]["status"] != "PASS"
    assert metrics["task_success_rate"] == 0


@pytest.mark.parametrize("value", [None, []])
def test_optional_user_goal_shape_does_not_crash_when_run_ids_are_present(multi_inputs, value):
    from eval.scoring import score_query

    context, case, turns, receipt = _v2_source_case_and_observation(multi_inputs)
    case["turns"] = case["turns"][:2]
    context["reference"]["queries"][case["id"]]["turns"] = context["reference"]["queries"][case["id"]]["turns"][:2]
    turns[1]["result"]["meta"] = {"user_goal": value}
    receipt["result"]["facts"]["meta"]["user_goal"] = value
    _, scored = score_query([case], [{"id": case["id"], "turns": turns}], result_reference=context)
    # Absence of a goal is not itself a mismatch: both explicit run IDs still
    # identify the current execution. This regression only requires safe handling.
    assert len(scored) == 1
    assert scored[0]["status"] in {"PASS", "FAIL", "UNKNOWN"}


@pytest.mark.parametrize("invocations", [None,
    [{"status": "COMPLETED", "result": None}],
    [{"status": "COMPLETED", "result": []}],
    [{"status": "COMPLETED", "result": "invalid receipt"}]])
def test_null_source_turn_malformed_receipts_do_not_raise_or_pass(multi_inputs, invocations):
    from eval.scoring import score_query

    context, case, turns, _ = _v2_source_case_and_observation(multi_inputs)
    case["turns"][2].pop("answer_rubric")
    turns.append({"failed": False, "run_id": "run-2", "run_status": "SUCCEEDED", "tool_trace": [],
                  "result": {"plan": {"action": "ANSWER"}, "rows": [], "write_event_delta": 0},
                  "tool_invocations": invocations})
    _, scored = score_query([case], [{"id": case["id"], "turns": turns}], result_reference=context)
    assert scored[0]["status"] != "PASS"
    assert next(check for check in scored[0]["checks"]
                if check["id"] == "turn2.source_results.not_requested")["status"] == "FAIL"


@pytest.mark.parametrize("failed", [[], 0])
def test_turn_failed_flag_rejects_non_boolean_false_values(multi_inputs, failed):
    from eval.scoring import score_query

    context, case, turns, _ = _v2_source_case_and_observation(multi_inputs)
    case["turns"] = case["turns"][:2]
    context["reference"]["queries"][case["id"]]["turns"] = context["reference"]["queries"][case["id"]]["turns"][:2]
    turns[1]["failed"] = failed
    with pytest.raises(ValueError, match="QUERY_TURN_FAILED_FLAG_INVALID"):
        score_query([case], [{"id": case["id"], "turns": turns}], result_reference=context)


@pytest.mark.parametrize("corruption,expected", [("changed_conversation", "FAIL"),
                                                ("regressed_version", "FAIL"),
                                                ("missing_identity", "UNKNOWN")])
def test_v2_followup_requires_persisted_conversation_continuity(multi_inputs, corruption, expected):
    from eval.scoring import score_query

    context, case, turns, _ = _v2_source_case_and_observation(multi_inputs)
    case["turns"] = case["turns"][:2]
    context["reference"]["queries"][case["id"]]["turns"] = context["reference"]["queries"][case["id"]]["turns"][:2]
    metadata = turns[1]["result"]["meta"]
    if corruption == "changed_conversation":
        metadata["conversation_id"] = "another-conversation"
    elif corruption == "regressed_version":
        metadata["conversation_version"] = 0
    else:
        metadata.pop("conversation_id")
    _, scored = score_query([case], [{"id": case["id"], "turns": turns}], result_reference=context)
    assert scored[0]["status"] == expected
