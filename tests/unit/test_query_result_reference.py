"""A correct tool or a related canonical cannot hide unsupported question results."""
from copy import deepcopy

import pytest

from eval.common import digest, read_json, write_json, write_jsonl
from eval.query_results import build_query_results, score_query_results, validate_query_results
from eval.run import run_section, score_inputs
from eval.validate_gold import validate_gold


@pytest.fixture
def inputs(tmp_path):
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    lines = [f"Synthetic question {i}." for i in range(1, 6)]
    text = "\n".join(lines) + "\n"
    source = snapshot / "source.md"
    source.write_text(text, encoding="utf-8")
    position, questions = 0, []
    for i, line in enumerate(lines, 1):
        questions.append({"id": f"q{i}", "interview_id": "i1", "raw_question": line,
            "canonical_question_id": "c1" if i == 2 else f"c{i}",
            "source_spans": [{"revision_id": "r1", "start_char": position, "end_char": position + len(line),
                              "start_line": i, "end_line": i, "quote": line}]})
        position += len(line) + 1
    facts = {"snapshot": {"corpus_revision": 1, "indexed_revision": 1, "task_annotation_revision": 1,
                          "task_annotation_policy": "KNOWN", "as_of": "2026-10-15"},
        "documents": [{"id": "d1", "build_id": "b1", "revision_id": "r1", "decision": "INCLUDED",
                       "source_path": "source.md", "source_hash": digest(source.read_bytes())}],
        "interviews": [{"id": "i1", "build_id": "b1", "analytics_eligible": True,
                        "company_normalized": "字节", "round": "FIRST"}], "questions": questions,
        "canonicals": [{"id": key} for key in ("c1", "c3", "c4", "c5")], "physical_indices": ["test-index"]}
    write_json(snapshot / "facts.json", facts)
    review = {"facts_sha256": digest(facts), "candidate_predictions_seen": False, "human_verified": False,
        "reviewer": "synthetic-source-reviewer", "reviewed_at": "2026-10-16T00:00:00Z", "queries": [{
            "query": "synthetic scenario", "criteria": "Fabricated test assignments, no model inference.",
            "coverage": {"scope": "judged_pool"}, "labels": [
                {"occurrence_id": key, "role": role, "reason": "Synthetic evidence assignment"}
                for key, role in (("q1", "POSITIVE"), ("q2", "NEGATIVE"), ("q3", "BOUNDARY"), ("q4", "NEGATIVE"))]}]}
    write_json(tmp_path / "review.json", review)
    recipe = {"id": "test-queries", "guide_version": "test-v1", "as_of": "2026-10-16", "cases": [
        {"id": "s1", "query": "synthetic scenario", "minimum_positive": 1, "expected_plan": {"action": "SEARCH"}}]}
    write_json(tmp_path / "recipe.json", recipe)
    return tmp_path, facts, review


def build(inputs):
    root, _, _ = inputs
    target = root / "gold"
    build_query_results(root / "snapshot", [root / "review.json"], root / "recipe.json", target,
                        authorization={"thread_id": "test", "user_instruction": "Synthetic test only"})
    return target


def observation(facts, *numbers):
    rows = []
    for number in numbers:
        question = facts["questions"][number - 1]
        rows.append({"canonical_question_id": question["canonical_question_id"], "sources": [{
            "occurrence_id": question["id"], "revision_id": "r1", "interview_id": "i1", "source_document_id": "d1",
            "raw_file_hash": facts["documents"][0]["source_hash"], **question["source_spans"][0]}]})
    return {"id": "s1", "snapshot": {**facts["snapshot"], "as_of": "2026-10-16"}, "failed": False,
        "turns": [{"failed": False, "run_status": "SUCCEEDED", "tool_trace": [], "result": {
            "plan": {"action": "SEARCH"}, "rows": rows, "write_event_delta": 0}}]}


def scored(inputs, *numbers):
    root, facts, _ = inputs
    dataset = build(inputs)
    write_jsonl(root / "predictions.jsonl", [observation(facts, *numbers)])
    return score_inputs(dataset, "routing", root / "predictions.jsonl", split="dev", review_policy="delegated_agent")


def test_normal_routing_score_checks_actual_source_results(inputs):
    result = scored(inputs, 1)
    assert result["scored"][0]["status"] == "PASS"
    assert result["metrics"]["source_results"]["returned_roles"] == {"POSITIVE": 1}
    assert result["gate"]["status"] == "NOT_ELIGIBLE"


@pytest.mark.parametrize("number,expected", [(2, "FAIL"), (3, "FAIL"), (4, "FAIL"), (5, "FAIL")])
def test_correct_plan_cannot_pass_without_a_positive_source(inputs, number, expected):
    result = scored(inputs, number)
    assert result["metrics"]["argument_accuracy"] == 1
    assert result["metrics"]["task_success_rate"] == 0
    assert result["scored"][0]["status"] == expected


@pytest.mark.parametrize("number", [3, 5])
def test_boundary_and_unreviewed_return_stay_unknown_not_negative(inputs, number):
    result = scored(inputs, 1, number)
    assert result["scored"][0]["status"] == "UNKNOWN"
    assert next(c for c in result["scored"][0]["checks"] if c["id"] == "source_results.no_negative")["status"] == "PASS"


def test_same_canonical_different_occurrence_does_not_borrow_positive_label(inputs):
    result = scored(inputs, 2)
    source = result["scored"][0]["scores"]["source_results"]
    assert source["covered_positive_canonicals"] == 0
    assert source["roles"] == {"NEGATIVE": 1}


@pytest.mark.parametrize("field,value", [("quote", "invented"), ("raw_file_hash", "0" * 64),
    ("revision_id", "wrong"), ("occurrence_id", "q5"), ("start_line", 5), ("interview_id", "wrong")])
def test_citations_require_matching_identity_hash_and_span(inputs, field, value):
    root, facts, _ = inputs
    dataset = build(inputs)
    actual = observation(facts, 1)
    actual["turns"][0]["result"]["rows"][0]["sources"][0][field] = value
    context = validate_query_results(dataset, read_json(dataset / "manifest.json"))
    checks, diagnostics = score_query_results(context["reference"]["queries"]["s1"], actual["turns"][0], context["rows"])
    assert diagnostics["roles"] == {"INVALID_SOURCE": 1}
    assert next(c for c in checks if c["id"] == "source_results.sources")["status"] == "FAIL"


def test_duplicate_return_does_not_inflate_minimum_or_coverage(inputs):
    result = scored(inputs, 1, 1)
    source = result["scored"][0]["scores"]["source_results"]
    assert source["covered_positive_canonicals"] == 1
    assert result["scored"][0]["status"] == "FAIL"


@pytest.mark.parametrize("corruption", ["facts", "source", "review_seen", "human", "duplicate", "insufficient"])
def test_stale_or_nonindependent_reference_fails_before_output(inputs, corruption):
    root, facts, review = inputs
    if corruption == "facts":
        facts["snapshot"]["corpus_revision"] = 2
        write_json(root / "snapshot/facts.json", facts)
    elif corruption == "source":
        (root / "snapshot/source.md").write_text("changed", encoding="utf-8")
    else:
        if corruption == "review_seen": review["candidate_predictions_seen"] = True
        if corruption == "human": review["human_verified"] = True
        if corruption == "duplicate": review["queries"][0]["labels"].append(deepcopy(review["queries"][0]["labels"][0]))
        if corruption == "insufficient": review["queries"][0]["labels"][0]["role"] = "BOUNDARY"
        write_json(root / "review.json", review)
    with pytest.raises(ValueError):
        build(inputs)
    assert not (root / "gold").exists()


def test_frozen_reference_cannot_be_relabeled_after_prediction(inputs):
    dataset = build(inputs)
    manifest = read_json(dataset / "manifest.json")
    manifest["query_result_reference"]["queries"]["s1"]["labels"][0]["role"] = "NEGATIVE"
    write_json(dataset / "manifest.json", manifest)
    with pytest.raises(ValueError, match="frozen dataset content changed"):
        validate_gold(dataset, "routing", split="dev", review_policy="delegated_agent")


def test_report_archives_source_reference_and_strictly_recomputes_it(inputs):
    from eval.release import verify_report
    root, facts, _ = inputs
    dataset = build(inputs)
    predictions = root / "predictions.jsonl"
    write_jsonl(predictions, [observation(facts, 1, 5)])
    report = run_section(dataset, "routing", predictions, root / "reports", split="dev", review_policy="delegated_agent")
    assert (report / "gold/snapshot/source.md").is_file()
    assert read_json(report / "metrics.json")["task_success_rate"] == 0
    assert (report / "gold/reviews/01-review.json").is_file()
    # Strict recomputation is distinct from the unbound candidate/deployment.
    result = verify_report(report, strict=True)
    assert result["strict_verified"] is True
    assert read_json(report / "metrics.json")["source_results"]["returned_roles"] == {"POSITIVE": 1, "UNREVIEWED": 1}


def test_wrong_company_uses_same_displayed_occurrence(inputs):
    dataset = build(inputs)
    context = validate_query_results(dataset, read_json(dataset / "manifest.json"))
    query = {**context["reference"]["queries"]["s1"], "required_company": "腾讯"}
    checks, _ = score_query_results(query, observation(inputs[1], 1)["turns"][0], context["rows"])
    assert next(c for c in checks if c["id"] == "source_results.same_occurrence_company")["status"] == "FAIL"


def test_one_displayed_occurrence_cannot_borrow_another_object_family(inputs):
    dataset = build(inputs)
    context = validate_query_results(dataset, read_json(dataset / "manifest.json"))
    query = deepcopy(context["reference"]["queries"]["s1"])
    query["minimum_families"] = 2
    query["labels"][0]["object_family"] = "Cache"
    query["labels"][2].update(role="POSITIVE", object_family="Scheduling")
    checks, diagnostics = score_query_results(query, observation(inputs[1], 1)["turns"][0], context["rows"])
    assert diagnostics["object_families"] == ["Cache"]
    assert next(c for c in checks if c["id"] == "source_results.object_families")["status"] == "FAIL"


@pytest.mark.parametrize("missing", ["occurrence_id", "raw_file_hash", "location"])
def test_weak_citation_cannot_borrow_a_positive_occurrence(inputs, missing):
    dataset = build(inputs)
    context = validate_query_results(dataset, read_json(dataset / "manifest.json"))
    actual = observation(inputs[1], 1)["turns"][0]
    source = actual["result"]["rows"][0]["sources"][0]
    if missing == "location":
        for key in ("start_char", "end_char", "start_line", "end_line"):
            source.pop(key)
    else:
        source.pop(missing)
    checks, diagnostics = score_query_results(context["reference"]["queries"]["s1"], actual, context["rows"])
    assert diagnostics["roles"] == {"INVALID_SOURCE": 1}
    assert next(c for c in checks if c["id"] == "source_results.sources")["status"] == "FAIL"


def test_unified_entry_freezes_without_a_provider_or_database(inputs, capsys):
    from eval.__main__ import main
    root, _, _ = inputs
    write_json(root / "authorization.json", {"thread_id": "test", "user_instruction": "Synthetic test only"})
    assert main(["freeze-queries", "--snapshot", str(root / "snapshot"), "--reviews", str(root / "review.json"),
                 "--recipe", str(root / "recipe.json"), "--authorization", str(root / "authorization.json"),
                 "--output", str(root / "gold")]) == 0
    assert '"model_calls": 0' in capsys.readouterr().out
    assert (root / "gold/EXPECTED-ANSWERS.md").is_file()


def test_sql_summary_resolves_only_a_unique_exact_frozen_occurrence(inputs):
    dataset = build(inputs)
    context = validate_query_results(dataset, read_json(dataset / "manifest.json"))
    actual = observation(inputs[1], 1)["turns"][0]
    actual["result"]["plan"]["action"] = "LIST"
    actual["result"]["meta"] = {"route": "SQL"}
    row = actual["result"]["rows"][0]
    source = row.pop("sources")[0]
    row["source_summary"] = {"revision_id": "r1", "spans": [inputs[1]["questions"][0]["source_spans"][0]]}
    checks, diagnostics = score_query_results(context["reference"]["queries"]["s1"], actual, context["rows"])
    assert all(c["status"] == "PASS" for c in checks)
    assert diagnostics["outcomes"][0]["identity_basis"] == "unique_exact_span_in_hash_verified_frozen_facts"
    duplicate = {**context["rows"][0], "id": "different-occurrence-at-same-span"}
    _, ambiguous = score_query_results(context["reference"]["queries"]["s1"], actual, context["rows"] + [duplicate])
    assert ambiguous["roles"] == {"INVALID_SOURCE": 1}
    row["source_summary"] = {"revision_id": "r1", "spans": [{"quote": source["quote"]}]}
    _, weak = score_query_results(context["reference"]["queries"]["s1"], actual, context["rows"])
    assert weak["roles"] == {"INVALID_SOURCE": 1}


@pytest.mark.parametrize("route", ["SEARCH", "SQL"])
def test_search_cannot_use_the_sql_summary_exception_even_with_a_forged_route(inputs, route):
    dataset = build(inputs)
    context = validate_query_results(dataset, read_json(dataset / "manifest.json"))
    actual = observation(inputs[1], 1)["turns"][0]
    row = actual["result"]["rows"][0]
    row.pop("sources")
    row["source_summary"] = {"revision_id": "r1", "spans": [inputs[1]["questions"][0]["source_spans"][0]]}
    actual["result"]["meta"] = {"route": route}
    _, diagnostics = score_query_results(context["reference"]["queries"]["s1"], actual, context["rows"])
    assert diagnostics["roles"] == {"INVALID_SOURCE": 1}


def test_claimed_complete_sql_cannot_pass_by_returning_only_the_minimum(inputs):
    dataset = build(inputs)
    context = validate_query_results(dataset, read_json(dataset / "manifest.json"))
    query = deepcopy(context["reference"]["queries"]["s1"])
    query["labels"][2]["role"] = "POSITIVE"
    actual = observation(inputs[1], 1)["turns"][0]
    actual["result"]["meta"] = {"route": "SQL", "semantic_scope": {"complete": True}, "pagination": {
        "offset": 0, "top_n": None, "next_cursor": None, "result_total": 1}}
    checks, _ = score_query_results(query, actual, context["rows"])
    assert next(c for c in checks if c["id"] == "source_results.minimum_positive")["status"] == "PASS"
    assert next(c for c in checks if c["id"] == "source_results.complete_sql_contains_known_positives")["status"] == "FAIL"
    actual["result"]["meta"]["pagination"]["next_cursor"] = "more-pages"
    checks, _ = score_query_results(query, actual, context["rows"])
    assert not any(c["id"] == "source_results.complete_sql_contains_known_positives" for c in checks)


def test_verified_partial_search_is_not_complete_success_even_when_every_return_is_correct(inputs):
    root, facts, _ = inputs
    dataset = build(inputs)
    actual = observation(facts, 1)
    actual["turns"][0]["result"]["meta"] = {"route": "SEARCH", "result_completeness": {
        "verification_complete": False, "invalid_candidate_count": 2, "corpus_complete": False}}
    write_jsonl(root / "predictions.jsonl", [actual])
    result = score_inputs(dataset, "routing", root / "predictions.jsonl", split="dev", review_policy="delegated_agent")
    assert result["scored"][0]["status"] == "UNKNOWN"
    assert result["metrics"]["task_success_rate"] == 0
