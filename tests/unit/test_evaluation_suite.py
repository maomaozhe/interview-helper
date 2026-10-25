"""Offline contract tests for the entry point and interruption boundaries."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from eval.common import read_json, write_json
from eval.demo import prepare_demo
from eval.scoring import score_query
from eval.suite import compare_suites, run_suite


@pytest.fixture
def suite_file(tmp_path):
    root = tmp_path / "inputs"
    prepare_demo(root)
    suite = {"schema_version": "evaluation_suite_v1", "id": "offline-contract",
             "candidate": {"id": "synthetic", "components": {
                 section: {"kind": "synthetic", "version": "v1"} for section in ("routing", "sql")}},
             "modules": [{"id": section, "section": section, "dataset": "fixture", "split": "dev",
                          "review_policy": "human", "threshold_version": "quality_gate_v3",
                          "default_pipeline": "HYBRID", "predictions": f"fixture/{section}.predictions.jsonl"}
                         for section in ("routing", "sql")]}
    path = root / "suite.json"
    write_json(path, suite)
    return path


def test_real_cli_bootstraps_src_and_runs_six_modules_without_pythonpath(tmp_path):
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    output = tmp_path / "demo"
    result = subprocess.run([sys.executable, "-m", "eval", "demo", "--output", str(output)],
                            cwd=Path(__file__).resolve().parents[2], env=environment,
                            capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    summary = read_json(output / "run" / "report.json")
    assert len(summary["modules"]) == 6
    assert summary["execution_status"] == "COMPLETED"
    assert summary["evidence_status"] == "VERIFIED"
    assert summary["quality_status"] == "NOT_ELIGIBLE"
    assert summary["user_task_quality"] == "NOT_ESTABLISHED"
    assert (output / "run" / "release-bundle.json").is_file()


def test_resume_keeps_completed_reports_and_continues_after_interruption(suite_file, tmp_path, monkeypatch):
    import eval.run
    original = eval.run.run_section
    calls = []
    def interrupt(dataset, section, *args, **kwargs):
        calls.append(section)
        if section == "sql":
            raise KeyboardInterrupt()
        return original(dataset, section, *args, **kwargs)
    monkeypatch.setattr(eval.run, "run_section", interrupt)
    output = tmp_path / "run"
    with pytest.raises(KeyboardInterrupt):
        run_suite(suite_file, output)
    state = read_json(output / "checkpoint.json")
    first_report = state["modules"]["routing"]["report"]
    assert state["status"] == "INTERRUPTED"
    assert state["modules"]["routing"]["state"] == "COMPLETED"
    monkeypatch.setattr(eval.run, "run_section", original)
    result = run_suite(suite_file, output, resume=True)
    assert result["execution_status"] == "COMPLETED"
    assert read_json(output / "checkpoint.json")["modules"]["routing"]["report"] == first_report
    assert len(list((output / "modules" / "routing" / "reports").rglob("integrity.json"))) == 1
    # A completed resume verifies evidence without creating new runs.
    assert run_suite(suite_file, output, resume=True)["execution_status"] == "COMPLETED"


def test_changed_inputs_and_tampered_completed_reports_refuse_resume(suite_file, tmp_path):
    output = tmp_path / "run"
    run_suite(suite_file, output)
    original = suite_file.read_text(encoding="utf-8")
    suite = json.loads(original)
    suite["candidate"]["components"]["sql"]["version"] = "v2"
    write_json(suite_file, suite)
    with pytest.raises(ValueError, match="SUITE_INPUT_OR_VERSION_CHANGED"):
        run_suite(suite_file, output, resume=True)
    suite_file.write_text(original, encoding="utf-8")
    state = read_json(output / "checkpoint.json")
    report = output / state["modules"]["sql"]["report"]
    write_json(report / "metrics.json", {"task_success_rate": 0})
    with pytest.raises(ValueError, match="COMPLETED_REPORT_INVALID"):
        run_suite(suite_file, output, resume=True)


def test_compare_same_suite_and_output_collision(suite_file, tmp_path):
    left, right = tmp_path / "left", tmp_path / "right"
    run_suite(suite_file, left)
    run_suite(suite_file, right)
    compared = compare_suites(left, right)
    assert compared["compatible"]
    assert set(compared["modules"]) == {"routing", "sql"}
    with pytest.raises(FileExistsError):
        run_suite(suite_file, left)


def test_correct_tool_and_parameters_do_not_hide_wrong_user_result():
    gold = {"id": "query", "required_tools": ["list_questions"], "expected_plan": {"top_n": 2},
            "assertions": [{"id": "expected_questions", "path": "result.rows.*.canonical_question_id",
                            "op": "eq", "value": ["expected-a", "expected-b"]}]}
    observed = {"id": "query", "task_success": True, "tool_trace": [{"name": "list_questions"}],
                "result": {"plan": {"top_n": 2}, "rows": [{"canonical_question_id": "wrong-a"},
                                                           {"canonical_question_id": "wrong-b"}]}}
    metrics, rows = score_query([gold], [observed])
    assert metrics["tool_selection_accuracy"] == metrics["argument_accuracy"] == 1
    assert metrics["task_success_rate"] == 0
    assert rows[0]["status"] == "FAIL"
    assert any(check["id"] == "turn0.expected_questions" and check["status"] == "FAIL" for check in rows[0]["checks"])


def test_collected_extraction_waits_for_alignment_without_recollecting(tmp_path, monkeypatch):
    import eval.collect
    import eval.suite
    root = tmp_path / "inputs"
    fixture = prepare_demo(root)
    # The collector is replaced at its public boundary; scoring and recovery use
    # the real implementation. Collector persistence has separate crash tests.
    suite = {"schema_version": "evaluation_suite_v1", "id": "alignment-review",
             "candidate": {"id": "fixture", "components": {"extraction": {"version": "v1"}}},
             "modules": [{"id": "extraction", "section": "extraction", "dataset": "fixture", "split": "dev",
                          "review_policy": "human", "threshold_version": "quality_gate_v3", "default_pipeline": "HYBRID",
                          "collect": {"adapter": "published", "options": {"snapshot_export": "export"}},
                          "alignments": "approved.jsonl"}]}
    export = root / "export"
    export.mkdir()
    write_json(export / "manifest.json", {})
    write_json(export / "facts.json", {})
    suite_path = root / "suite.json"
    write_json(suite_path, suite)
    monkeypatch.setattr(eval.suite, "preflight", lambda *args, **kwargs: {"status": "PASS", "checks": []})
    collected = []
    def collect(argv):
        output = Path(argv[argv.index("--output") + 1])
        if "--resume" in argv:
            assert output.exists()
            return
        collected.append(output)
        output.write_bytes((fixture / "extraction.predictions.jsonl").read_bytes())
        write_json(output.with_suffix(".checkpoint.json"), {"stub": True})
    monkeypatch.setattr(eval.collect, "main", collect)
    output = tmp_path / "run"
    assert run_suite(suite_path, output)["execution_status"] == "NEEDS_INPUT"
    assert (output / "modules/extraction/alignments.draft.jsonl").exists()
    (root / "approved.jsonl").write_bytes((fixture / "alignments.jsonl").read_bytes())
    assert run_suite(suite_path, output, resume=True)["execution_status"] == "COMPLETED"
    assert len(collected) == 1
    (root / "approved.jsonl").write_text("[]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="APPROVED_ALIGNMENT_CHANGED"):
        run_suite(suite_path, output, resume=True)


def test_resume_rejects_report_escape_without_linking_external_report(suite_file, tmp_path):
    output = tmp_path / "run"
    run_suite(suite_file, output)
    state = read_json(output / "checkpoint.json")
    state["modules"]["sql"]["report"] = "../external-report"
    write_json(output / "checkpoint.json", state)
    with pytest.raises(ValueError, match="SUITE_REPORT_PATH_ESCAPE"):
        run_suite(suite_file, output, resume=True)
    summary = read_json(output / "report.json")
    assert next(m for m in summary["modules"] if m["id"] == "sql")["report"] is None
    assert summary["quality_status"] == "NOT_EVALUATED"


def test_resume_rejects_valid_report_bound_to_other_predictions(suite_file, tmp_path):
    from eval.run import run_section
    output = tmp_path / "run"
    run_suite(suite_file, output)
    state = read_json(output / "checkpoint.json")
    original = output / state["modules"]["sql"]["report"]
    config = read_json(original / "config.json")
    predictions = tmp_path / "other.jsonl"
    predictions.write_text('{"id":"sql","model_calls":[],"result":{"rows":[]}}\n', encoding="utf-8")
    substituted = run_section(suite_file.parent / "fixture", "sql", predictions, output / "substitute",
                              split="dev", threshold_version="quality_gate_v3", candidate=config["candidate"],
                              suite=config["suite"], protocol=config["protocol"])
    state["modules"]["sql"]["report"] = substituted.relative_to(output).as_posix()
    write_json(output / "checkpoint.json", state)
    with pytest.raises(ValueError, match="SUITE_REPORT_INPUT_MISMATCH"):
        run_suite(suite_file, output, resume=True)
