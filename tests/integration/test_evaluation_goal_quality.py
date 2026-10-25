"""Collect real host receipts offline, then seal multi-turn answer assessments."""
from copy import deepcopy
from datetime import date
from pathlib import Path
import shutil

import httpx
import pytest
from fastapi.testclient import TestClient

from eval.budget import EvaluationBudget
from eval.collect import HttpQueryCollector
from eval.common import digest, read_json, write_json, write_jsonl
from eval.compare import compare
from eval.preflight import load_suite, preflight
from eval.release import verify_report
from eval.run import run_section, score_inputs
from eval.serve import evaluation_app
from eval.suite import run_suite
from interview_intelligence.agent.query_contract import QuerySpec
from interview_intelligence.api import create_app
from interview_intelligence.config import Settings
from test_analytics import seed_corpus


ANSWER = "当前字节 Redis 范围共有2道题。这是该筛选范围的完整统计，不是全库所有题。"
RUBRIC = {"schema": "query_answer_v1", "required_basis": "CORPUS", "criteria": [
    {"id": "goal", "description": "直接回答当前字节 Redis 筛选范围的去重题数量为2。"},
    {"id": "scope", "description": "说明该计数只覆盖指定筛选，不冒充全库数量。"}]}


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    database, _, _ = seed_corpus()

    class Planner:
        def plan(self, run, context):
            goal = {"objective": run.request.message, "response_mode": "SYNTHESIS"}
            filters = {"company": "字节", "topic_l1": "Redis"}
            if run.request.message == "列出字节 Redis 题":
                return QuerySpec(action="LIST", page_size=1, filters=filters,
                                 user_goal={**goal, "response_mode": "DIRECT_RESULT"})
            if not context["completed_tools"]:
                return QuerySpec(action="COUNT", filters=filters, user_goal=goal)
            return QuerySpec(action="ANSWER", filters=filters, user_goal=goal, answer_basis="CORPUS",
                             evidence_tool_ordinals=[1], answer_text=ANSWER)

    monkeypatch.setattr("interview_intelligence.api.create_app",
                        lambda db, settings: create_app(db, settings, query_planner=Planner()))
    settings = Settings(local_user_id="eval-answer-quality", model_api_key=None, elasticsearch_url=None,
                        query_prompt_version="query_agent_v16", query_router_enabled=True,
                        query_max_model_calls=4, query_max_total_model_calls=6,
                        query_max_tokens=96000, query_deadline_seconds=180,
                        snapshot_root=tmp_path / "snapshots")
    app = evaluation_app(database, settings, as_of=date(2026, 10, 25))
    case = {"id": "conversation", "group_id": "synthetic-conversation", "split": "dev", "human_verified": False,
        "turns": [
            {"message": "列出字节 Redis 题", "expected_plan": {"action": "LIST", "filters.company": "字节"},
             "required_tools": ["list_questions"], "assertions": [
                 {"id": "first_page", "path": "result.rows", "op": "length", "value": 1}]},
            {"message": "核对数量并解释范围", "expected_plan": {"action": "ANSWER"},
             "required_tools": ["get_question_count", "answer_question"], "answer_rubric": RUBRIC,
             "assertions": [{"id": "terminal", "path": "run_status", "op": "eq", "value": "SUCCEEDED"}]}]}
    with TestClient(app) as client:
        snapshot = client.get("/api/evaluation/context").json()["snapshot"]

        def handle(request):
            response = client.request(request.method, str(request.url).replace("http://fixture", ""),
                                      content=request.content, headers=dict(request.headers))
            body = response.json()
            if request.url.path == "/api/evaluation/context":
                # No provider is called. Simulate the Linux lock flag only for this in-process transport.
                body["model_gate_shared"] = True
            return httpx.Response(response.status_code, json=body)

        with httpx.Client(base_url="http://fixture", transport=httpx.MockTransport(handle)) as http:
            budget = EvaluationBudget(12, 192000)
            row = {"id": case["id"], **HttpQueryCollector(http, {"snapshot": snapshot}, budget)(case, [])}
    assert not row["failed"] and budget.used_calls == budget.used_tokens == 0
    dataset = tmp_path / "gold"
    dataset.mkdir()
    manifest = {"version": "synthetic-goal-quality", "kind": "fixture", "status": "draft",
                "snapshot": snapshot, "routing": [case]}
    write_json(dataset / "manifest.json", manifest)
    predictions = tmp_path / "predictions.jsonl"
    write_jsonl(predictions, [row])
    # Match the normalized manifest used by the scorer, including empty sections.
    from eval.validate_gold import load_dataset
    manifest = load_dataset(dataset)
    review = {"schema": "query_answer_reviews_v1", "dataset_sha256": digest(manifest),
        "predictions_sha256": digest(predictions.read_bytes()), "reviews": [{
            "case_id": case["id"], "turn_index": 1, "answer_sha256": digest(ANSWER.encode()),
            "rubric_sha256": digest(RUBRIC), "reviewer_kind": "agent", "reviewer": "synthetic-independent-reviewer",
            "reviewed_at": "2026-10-25T00:00:00+08:00", "independent": True, "assessments": {
                "goal": {"passed": True, "evidence_quote": "共有2道题", "reason": "Matches the fixed synthetic fixture."},
                "scope": {"passed": True, "evidence_quote": "不是全库所有题", "reason": "Keeps the frozen scope."}}}]}
    reviews = tmp_path / "answer-reviews.json"
    write_json(reviews, review)
    return dataset, predictions, reviews, row


def test_actual_collector_retains_final_text_and_current_persistent_receipts(inputs):
    dataset, predictions, reviews, row = inputs
    actual = row["turns"][1]
    assert actual["result"]["answer"] == ANSWER
    assert [i["ordinal"] for i in actual["tool_invocations"]] == [1, 2]
    assert all(i["run_id"] == actual["run_id"] for i in actual["tool_invocations"])
    missing = score_inputs(dataset, "routing", predictions, split="dev")
    assert missing["scored"][0]["status"] == "UNKNOWN"
    assert missing["metrics"]["answer_quality"]["engineering_pass_rate"] == 1
    result = score_inputs(dataset, "routing", predictions, split="dev", answer_reviews=reviews)
    assert result["scored"][0]["status"] == "PASS"
    assert result["metrics"]["answer_quality"]["semantic_pass_rate"] == 1
    assert result["gate"]["status"] == "NOT_ELIGIBLE"


def test_second_turn_and_independent_rejection_cannot_be_hidden_by_first_turn(inputs):
    dataset, predictions, reviews, row = inputs
    review = read_json(reviews)
    review["reviews"][0]["assessments"]["scope"].update(passed=False, reason="Synthetic rejection of scope wording.")
    write_json(reviews, review)
    assert score_inputs(dataset, "routing", predictions, split="dev", answer_reviews=reviews)["scored"][0]["status"] == "FAIL"
    changed = deepcopy(row)
    changed["turns"][1]["result"]["meta"]["completion"]["status"] = "NEEDS_RESPONSE"
    write_jsonl(predictions, [changed])
    assert score_inputs(dataset, "routing", predictions, split="dev")["scored"][0]["status"] == "FAIL"


@pytest.mark.parametrize("mutation", ["dataset", "predictions", "duplicate", "turn", "rubric", "reviewer"])
def test_stale_or_ambiguous_review_is_rejected_before_scoring(inputs, mutation):
    dataset, predictions, reviews, _ = inputs
    review = read_json(reviews)
    if mutation in {"dataset", "predictions"}:
        review[mutation + "_sha256"] = "0" * 64
    elif mutation == "duplicate":
        review["reviews"].append(deepcopy(review["reviews"][0]))
    elif mutation == "turn":
        review["reviews"][0]["turn_index"] = True
    elif mutation == "rubric":
        review["reviews"][0]["rubric_sha256"] = "0" * 64
    else:
        review["reviews"][0]["independent"] = False
    write_json(reviews, review)
    with pytest.raises(ValueError, match="ANSWER_REVIEW"):
        score_inputs(dataset, "routing", predictions, split="dev", answer_reviews=reviews)


def test_report_seals_review_and_recomputes_without_original_inputs(inputs, tmp_path):
    dataset, predictions, reviews, _ = inputs
    report = run_section(dataset, "routing", predictions, tmp_path / "reports", split="dev", answer_reviews=reviews)
    assert read_json(report / "manifest.json")["answer_reviews_sha256"] == digest(reviews.read_bytes())
    assert dataset.resolve().is_relative_to(tmp_path.resolve())
    shutil.rmtree(dataset)
    predictions.unlink()
    reviews.unlink()
    assert verify_report(report)["strict_verified"]
    review = read_json(report / "answer_reviews.json")
    review["reviews"][0]["assessments"]["scope"]["passed"] = False
    write_json(report / "answer_reviews.json", review)
    assert verify_report(report)["status"] == "INVALID"
    # Rewriting the report's outer hashes cannot manufacture an assessment bound to another answer.
    review["reviews"][0]["answer_sha256"] = "0" * 64
    write_json(report / "answer_reviews.json", review)
    metadata = read_json(report / "manifest.json")
    metadata["answer_reviews_sha256"] = digest((report / "answer_reviews.json").read_bytes())
    write_json(report / "manifest.json", metadata)
    seal = read_json(report / "integrity.json")
    for filename in ("manifest.json", "answer_reviews.json"):
        seal["artifacts"][filename] = digest((report / filename).read_bytes())
    write_json(report / "integrity.json", seal)
    result = verify_report(report)
    assert result["status"] == "INVALID" and "ANSWER_REVIEW_TEXT_CHANGED" in result["errors"]


def test_suite_preflight_run_and_resume_bind_external_review(inputs, tmp_path):
    dataset, predictions, reviews, _ = inputs
    suite = {"schema_version": "evaluation_suite_v1", "id": "multiturn-answer",
        "candidate": {"id": "synthetic", "components": {"routing": {"version": "synthetic"}}},
        "modules": [{"id": "dialogue", "section": "routing", "dataset": str(dataset), "split": "dev",
                     "review_policy": "human", "threshold_version": "quality_gate_v3", "default_pipeline": "BM25",
                     "predictions": str(predictions), "answer_reviews": str(reviews)}]}
    path = tmp_path / "suite.json"
    write_json(path, suite)
    assert preflight(load_suite(path))["status"] == "PASS"
    output = tmp_path / "suite-run"
    assert run_suite(path, output)["execution_status"] == "COMPLETED"
    assert run_suite(path, output, resume=True)["execution_status"] == "COMPLETED"
    review = read_json(reviews)
    review["reviews"][0]["reviewer"] = "changed-reviewer"
    write_json(reviews, review)
    with pytest.raises(ValueError, match="SUITE_INPUT"):
        run_suite(path, output, resume=True)


def test_adding_an_independent_review_is_new_measurement_not_model_improvement(inputs, tmp_path):
    dataset, predictions, reviews, _ = inputs
    before = run_section(dataset, "routing", predictions, tmp_path / "reports", split="dev")
    after = run_section(dataset, "routing", predictions, tmp_path / "reports", split="dev", answer_reviews=reviews)
    result = compare(before, after)
    assert not result["quality_comparable"]
    assert "answer_review_coverage" in result["protocol_changes"]
    change = result["metric_changes"]["answer_quality.semantic_pass_rate"]
    assert "delta" not in change and change["numeric_difference"] == 1
    another = run_section(dataset, "routing", predictions, tmp_path / "reports", split="dev", answer_reviews=reviews)
    assert compare(after, another)["quality_comparable"]
