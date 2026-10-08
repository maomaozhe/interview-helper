"""Protect quality conclusions against incomplete or misleading observations."""
import pytest

from eval.budget import EvaluationBudget
from eval.collect import collect_rows
from eval.common import MISSING, inside, lookup, read_json, write_json, write_jsonl
from eval.compare import compare
from eval.demo import demo
from eval.metrics import prf, retrieval_metrics, retrieval_sample
from eval.prepare import draft
from eval.run import run_section
from eval.scoring import assertion, score_dedup, score_extraction, score_labels, score_query, score_retrieval
from eval.snapshot import assert_snapshot
from eval.validate_gold import GoldValidationError, canonical_mapping, validate_gold


def test_wrong_classification_has_zero_f1_and_empty_sample_is_unscored():
    assert prf(0, 1, 1)["f1"] == 0
    assert prf(0, 0, 0)["f1"] is None


def test_duplicate_results_cannot_inflate_ndcg():
    result = retrieval_sample({"relevant": {"a": 2}, "ranked": ["a", "a"]})
    assert result["failed"] and result["invalid_ranking"]
    assert result["ndcg_at_10"] == 0


def test_failed_negative_is_not_a_correct_empty_result():
    result = retrieval_metrics([{"relevant": {}, "ranked": [], "failed": True}, {"relevant": {}, "ranked": []}])
    assert result["negative_correct_empty_rate"] == .5
    assert result["failed_queries"] == 1


def test_broad_query_keeps_all_gold_in_recall_denominator():
    result = retrieval_sample({"relevant": {str(i): 1 for i in range(25)}, "ranked": [str(i) for i in range(10)]})
    assert result["recall_at_10"] == result["recall_at_10_upper_bound"] == .4
    assert result["ndcg_at_10"] == 1


def test_wildcards_keep_missing_evidence_and_path_escape_is_rejected(tmp_path):
    assert lookup({"rows": [{"id": "a"}, {}]}, "rows.*.id") is MISSING
    with pytest.raises(ValueError):
        inside(tmp_path, "../outside.json")


def test_missing_rubric_cannot_pass_and_expression_is_not_executed():
    rule = {"id": "state", "op": "eq", "path": "result.state.status", "value": "MASTERED"}
    assert assertion(rule, {"task_success": True})["status"] == "UNKNOWN"
    assert assertion({"id": "u", "op": "unique", "path": "result"}, {"result": "abc"})["status"] == "UNKNOWN"


def test_wrong_arguments_fail_even_with_correct_tool_and_claimed_success():
    gold = {"id": "q", "required_tools": ["list_questions"], "expected_plan": {"top_n": 40},
            "assertions": [{"id": "rows", "path": "result.rows", "op": "exists"}]}
    pred = {"id": "q", "task_success": True, "tool_trace": [{"name": "list_questions"}], "result": {"plan": {"top_n": 20}, "rows": []}}
    metrics, rows = score_query([gold], [pred])
    assert metrics["tool_selection_accuracy"] == 1
    assert metrics["argument_accuracy"] == metrics["complete_plan_accuracy"] == metrics["task_success_rate"] == 0
    assert rows[0]["status"] == "FAIL"


def test_multi_turn_failure_missing_state_and_unauthorized_write_are_visible():
    turn = {"required_tools": ["list_questions"], "assertions": [{"id": "state", "path": "result.state", "op": "eq", "value": "done"}]}
    good = {"tool_trace": [{"name": "list_questions"}], "result": {"state": "done"}}
    bad = {"tool_trace": [{"name": "list_questions"}, {"name": "record_review"}], "result": {}}
    metrics, rows = score_query([{"id": "scene", "turns": [turn, turn]}], [{"id": "scene", "turns": [good, bad]}])
    assert metrics["task_success_rate"] == 0 and metrics["unknown_assertions"] == 1
    assert metrics["unauthorized_write_count"] == 1 and rows[0]["status"] == "FAIL"


def test_task_label_abstention_has_low_coverage_and_no_high_joint_accuracy():
    gold = {"id": "label", "response_form": "CODE", "coding_focus": "ENGINEERING"}
    metrics, _ = score_labels([gold], [{"id": "label", "result": {"response_form": "UNKNOWN", "coding_focus": "UNKNOWN"}}])
    assert metrics["joint_accuracy"] == metrics["predicted_coverage"] == 0
    assert metrics["covered_accuracy"] is None


def test_incomplete_candidate_observations_do_not_claim_end_to_end_recall():
    golds = [{"id": str(i), "label": "SAME", "right": {"id": "r"}} for i in range(2)]
    predictions = [{"result": {"label": "SAME", "candidate_ids": ["r"]}}, {"result": {"label": "SAME"}}]
    metrics, _ = score_dedup(golds, predictions)
    assert metrics["candidate_observation_coverage"] == .5
    assert metrics["end_to_end_same_recall"] is None


def test_rerank_fallback_and_different_candidates_receive_zero():
    gold = {"id": "q", "query": "q", "relevance": {"a": 2}}
    pred = {"id": "q", "pipelines": {"HYBRID": {"candidate_ids": ["a"], "ranked": ["a"]},
        "HYBRID_RERANK": {"candidate_ids": ["b"], "ranked": ["a"], "executed_pipeline": "HYBRID"}}}
    metrics, rows = score_retrieval([gold], [pred], {"id_namespace": "canonical"})
    assert metrics["pipelines"]["HYBRID_RERANK"]["recall_at_10"] == 0
    assert next(r for r in rows if r["pipeline"] == "HYBRID_RERANK")["status"] == "FAIL"


def test_ablation_aggregate_failure_does_not_erase_independent_success():
    gold = {"id":"q","query":"q","relevance":{"a":2}}
    pred = {"id":"q","failed":True,"pipelines":{"BM25":{"ranked":["a"]},"DENSE":{"failed":True,"ranked":[]}}}
    metrics, _ = score_retrieval([gold],[pred],{"id_namespace":"canonical"})
    assert metrics["pipelines"]["BM25"]["recall_at_10"] == 1
    assert metrics["pipelines"]["DENSE"]["recall_at_10"] == 0


def test_mapping_cannot_merge_distinct_gold_equivalence_groups():
    manifest = {"retrieval": [], "canonical_mapping": {"items": [
        {"gold_question_id": "a", "canonical_question_id": "same", "equivalence_group": "g1"},
        {"gold_question_id": "b", "canonical_question_id": "same", "equivalence_group": "g2"}]}}
    with pytest.raises(GoldValidationError, match="merged"):
        canonical_mapping(manifest)


def test_module_validation_does_not_require_unrelated_empty_gold(tmp_path):
    write_json(tmp_path / "manifest.json", {"version": "draft", "task_labels": [
        {**draft("one", "one", []), "response_form": "CODE", "coding_focus": "ENGINEERING"}]})
    assert validate_gold(tmp_path, "task_labels", split="dev")["version"] == "draft"
    with pytest.raises(GoldValidationError, match="200"):
        validate_gold(tmp_path, "task_labels", split="test")


def test_duplicate_ids_and_reverse_pairs_are_rejected(tmp_path):
    row = {**draft("same", "g", []), "label": "SAME", "left": {"id": "a", "text": "a"}, "right": {"id": "b", "text": "b"}}
    write_json(tmp_path / "manifest.json", {"version": "draft", "dedup": [row, row]})
    with pytest.raises(GoldValidationError, match="sample ID"):
        validate_gold(tmp_path, "dedup", split="dev")
    reverse = {**row, "id": "reverse", "left": row["right"], "right": row["left"]}
    write_json(tmp_path / "manifest.json", {"version": "draft", "dedup": [row, reverse]})
    with pytest.raises(GoldValidationError, match="reversed"):
        validate_gold(tmp_path, "dedup", split="dev")


def test_unknown_usage_retains_budget_and_failed_calls_reserve_again():
    budget = EvaluationBudget(2, 50000, per_call_tokens=1000)
    budget.before_call(estimated_input_tokens=0)
    budget.after_call(input_tokens=None, output_tokens=None)
    assert budget.used_tokens == 2024
    budget.before_call(estimated_input_tokens=0)
    budget.after_call(input_tokens=10, output_tokens=None)
    assert budget.used_calls == 2 and budget.used_tokens == 4048
    with pytest.raises(GoldValidationError, match="BUDGET"):
        budget.before_call()


def test_http_reservation_requires_full_upfront_budget():
    budget = EvaluationBudget(2, 100000)
    with pytest.raises(GoldValidationError, match="BUDGET"):
        budget.reserve(calls=3, tokens=65536)
    assert budget.used_calls == 0


def test_failed_collection_covers_every_id_and_snapshot_crossing_invalidates():
    def failure(gold, calls): raise TimeoutError("secret-provider-url")
    rows = collect_rows([{"id": "a"}, {"id": "b"}], failure, {"corpus_revision": 1}, lambda: {"corpus_revision": 1})
    assert [r["id"] for r in rows] == ["a", "b"]
    assert all(r["failed"] and r["error_code"] == "TimeoutError" for r in rows)
    rows = collect_rows([{"id": "a"}], failure, {"corpus_revision": 1}, lambda: {"corpus_revision": 2})
    assert rows[0]["snapshot"]["corpus_revision"] == 2
    with pytest.raises(GoldValidationError, match="SNAPSHOT"):
        assert_snapshot({"as_of": "2026-10-05"}, {"as_of": "2026-10-06"})


@pytest.fixture
def exercise(tmp_path):
    output = tmp_path / "demo"
    reports = demo(output)
    return output, reports


def test_demo_all_modules_have_reviewable_ineligible_reports(exercise):
    output, reports = exercise
    assert len(reports) == 6 and (output / "index.html").exists()
    for path in reports.values():
        assert read_json(path / "gates.json")["status"] == "NOT_ELIGIBLE"
        assert read_json(path / "manifest.json")["human_verified_count"] == 0
        assert all((path / name).exists() for name in ("per_sample.csv", "per_sample.jsonl", "config.json", "bad_cases.md", "summary.html"))
    assert read_json(reports["extraction"] / "metrics.json")["f1"] == pytest.approx(2/3)
    assert read_json(reports["routing"] / "metrics.json")["task_success_rate"] == 0


def test_extraction_rejects_self_reported_counts_and_stale_alignment(exercise):
    output, _ = exercise
    root = output / "fixture"
    from eval.common import read_jsonl
    gold = read_json(root / "manifest.json")["extraction"][0]
    pred = read_jsonl(root / "extraction.predictions.jsonl")[0]
    with pytest.raises(GoldValidationError, match="self-reported"):
        score_extraction([gold], [{"result": {"tp": 100, "fp": 0, "fn": 0}}], root, {})
    alignment = read_jsonl(root / "alignments.jsonl")[0]
    alignment["prediction_sha256"] = "stale"
    with pytest.raises(GoldValidationError, match="hash mismatch"):
        score_extraction([gold], [pred], root, {gold["id"]: alignment}, fixture=True)
    with pytest.raises(GoldValidationError, match="human review"):
        score_extraction([gold], [pred], root, {gold["id"]: alignment})


def test_report_compare_and_html_escape_do_not_hide_new_failures(exercise):
    output, reports = exercise
    before = reports["sql"]
    source = output / "fixture" / "sql.predictions.jsonl"
    write_jsonl(source, [{"id": "sql", "failed": True, "error_code": "<script>unsafe</script>", "result": {}, "model_calls": []}])
    after = run_section(output / "fixture", "sql", source, output / "reports", split="dev")
    changes = compare(before, after)
    assert changes["new_failures"] == [("sql", None)]
    assert "<script>unsafe</script>" not in (after / "summary.html").read_text(encoding="utf-8")
    manifest = read_json(after / "manifest.json")
    manifest["snapshot"] = {"corpus_revision": 99}
    write_json(after / "manifest.json", manifest)
    with pytest.raises(GoldValidationError, match="snapshot"):
        compare(before, after)


def test_prediction_coverage_rejects_duplicate_and_missing_samples(exercise):
    output, _ = exercise
    source = output / "fixture" / "task_labels.predictions.jsonl"
    write_jsonl(source, [{"id": "labels"}, {"id": "labels"}])
    with pytest.raises(GoldValidationError, match="exactly once"):
        run_section(output / "fixture", "task_labels", source, output / "reports", split="dev")
