"""Reporting clarification must preserve the frozen scoring decisions and inputs."""
from copy import deepcopy

import pytest

from eval.common import digest, read_json, read_jsonl, write_json, write_jsonl
from eval.validate_gold import GoldValidationError
from evals.project_quality import user_query_alignment as runner


@pytest.fixture
def reporting_fixture(tmp_path, monkeypatch):
    snapshot = {"corpus_revision": 293, "indexed_revision": 293,
                "task_annotation_revision": 30, "as_of": "2026-10-05"}
    def question(identity, canonical="shared"):
        return {"id": identity, "occurrence_id": "occ-" + identity, "canonical_question_id": canonical,
            "source_revision_id": "revision-" + identity, "source_hash": "hash-" + identity,
            "source_spans": [{"start_line": 3, "end_line": 3, "quote": "原文" + identity}]}
    p, b, n, u = question("p"), question("b"), question("n"), question("u", "unknown-scope")
    questions = [p, b, n, u]
    def item(q, bad=False):
        return {"canonical_question_id": q["canonical_question_id"], "sources": [{
            "occurrence_id": q["occurrence_id"], "revision_id": q["source_revision_id"],
            "raw_file_hash": q["source_hash"], **q["source_spans"][0],
            **({"quote": "伪造引用"} if bad else {})}]}
    cases, observations = [], []
    specs = [("page", item(p), "SEARCH"), ("boundary", item(b), "SEARCH"),
             ("unreviewed", item(u), "SEARCH"), ("negative-source", item(n), "SEARCH"),
             ("bad-quote", item(p, True), "SEARCH"), ("count", None, "COUNT")]
    for name, row, action in specs:
        turn = {"case_id": name, "turn_index": 0, "message": "合成测试/" + name, "expected": {
            "accepted_actions": [action], "scope_id": "design", "positive_gold_ids": ["p"],
            "requested_completeness": "all" if action == "COUNT" else "page",
            "expected_certification": "NOT_CERTIFIED"}}
        cases.append({"id": name, "turns": [turn]})
        observations.append({"id": name, "snapshot": snapshot, "turns": [{"failed": False,
            "result": {"plan": {"action": action}, "rows": [] if row is None else [row]}}]})
    bundle = {"manifest": {"snapshot": snapshot}, "questions": questions, "cases": cases,
              "scopes": {"design": {"positive_gold_ids": ["p"], "negative_gold_ids": ["n"],
                                    "boundary_gold_ids": ["b"], "reference_completeness": "corpus_occurrence_census_with_boundaries"}}}
    monkeypatch.setattr(runner, "load_gold", lambda _: bundle)
    # No HTTP call is part of a reporting audit, even when inputs are complete.
    def no_http(*args, **kwargs): raise AssertionError("reporting audit attempted HTTP")
    monkeypatch.setattr(runner.httpx, "Client", no_http)
    root = tmp_path / "sealed"
    scorer = root / "evaluator" / "scorer.py"
    scorer.parent.mkdir(parents=True)
    scorer.write_bytes(b"# original sealed scorer\n")
    files = {"scorer.py": digest(scorer.read_bytes())}
    write_json(root / "evaluator-seal.json", {"files": files, "sha256": digest(files)})
    source_run = tmp_path / "original-run"
    source_run.mkdir()
    protocol = {"gold_sha256": digest(bundle), "runtime_sha256": "sealed-api-runtime",
                "evaluator_sha256": digest(files), "runtime_root": str(root)}
    write_json(source_run / "protocol.json", protocol)
    write_jsonl(source_run / "predictions.jsonl", observations)
    receipt = {"gold_sha256": digest(bundle), "runtime_sha256": protocol["runtime_sha256"],
               "evaluator_sha256": protocol["evaluator_sha256"], "protocol_sha256": digest(protocol),
               "predictions_sha256": digest((source_run / "predictions.jsonl").read_bytes())}
    write_json(source_run / "collection.json", receipt)
    write_json(source_run / "after.json", {"snapshot": snapshot, "state_count": 0, "event_count": 0})
    assessed = [r for case, saved in zip(cases, observations) for r in runner.assess_case(case, saved, bundle)]
    # Old sealed output need not contain the newer diagnostic fields.
    original_turns = [{k: r[k] for k in ("case_id", "turn_index", "checks", "aligned")} for r in assessed]
    write_json(source_run / "alignment.json", {"turns": original_turns, "summary": {"turns": len(original_turns)}})
    def audit(): runner.audit_reporting(tmp_path / "gold", source_run, tmp_path / "audit")
    def sync_predictions(rows):
        write_jsonl(source_run / "predictions.jsonl", rows)
        r = read_json(source_run / "collection.json")
        r["predictions_sha256"] = digest((source_run / "predictions.jsonl").read_bytes())
        write_json(source_run / "collection.json", r)
    return {"run": source_run, "root": root, "bundle": bundle, "rows": observations,
            "output": tmp_path / "audit", "audit": audit, "sync_predictions": sync_predictions}


def test_complete_suite_adds_reporting_fields_without_changing_sealed_inputs(reporting_fixture):
    f = reporting_fixture
    original_hashes = {p.name: digest(p.read_bytes()) for p in f["run"].iterdir()}
    f["audit"]()
    report = read_json(f["output"] / "reporting-audit.json")
    assert {p.name: digest(p.read_bytes()) for p in f["run"].iterdir()} == original_hashes
    original = read_json(f["run"] / "alignment.json")["turns"]
    assert [(r["checks"], r["aligned"]) for r in report["turns"]] == [(r["checks"], r["aligned"]) for r in original]
    assert report["summary"]["cases"] == report["summary"]["turns"] == 6
    assert report["summary"]["human_verified"] is report["summary"]["release_eligible"] is False
    assert "zero new model calls" in report["note"] and "reporting clarification only" in report["note"]
    assert report["bindings"]["original_alignment_sha256"] == original_hashes["alignment.json"]


def test_literal_grounding_scope_acceptance_review_and_certification_stay_separate(reporting_fixture):
    f = reporting_fixture; f["audit"]()
    report = read_json(f["output"] / "reporting-audit.json")
    turns = {r["case_id"]: r for r in report["turns"]}
    page = turns["page"]
    assert page["aligned"] and page["literal_source_grounded"]
    assert not page["scope_certification_available"] and not page["scope_certification_required"]
    assert not page["certification_blocked"]
    boundary = turns["boundary"]
    assert boundary["literal_source_grounded"] and boundary["review_required"]
    assert not boundary["source_support_pass"] and not boundary["aligned"]
    assert boundary["canonical_known_positive_retrieved"] == 1
    assert boundary["source_supported_positive_retrieved"] == 0
    assert turns["unreviewed"]["literal_source_grounded"] and turns["unreviewed"]["review_required"]
    assert turns["negative-source"]["literal_source_grounded"]
    assert not turns["negative-source"]["items"][0]["source_scope_accepted"]
    assert not turns["bad-quote"]["literal_source_grounded"]
    assert turns["count"]["scope_certification_required"] and turns["count"]["certification_blocked"]
    summary = report["summary"]
    assert summary["literal_source_unverified_turns"] == summary["boundary_review_turns"] == 1
    assert summary["certification_required_turns"] == summary["certification_blocked_turns"] == 1
    assert summary["literal_source_grounded_items"] == 4 and summary["source_scope_accepted_items"] == 1


@pytest.mark.parametrize("change,error", [
    ("predictions", "QUERY_PREDICTIONS_CHANGED"), ("protocol", "QUERY_PROTOCOL_CHANGED"),
    ("evaluator", "SEALED_EVALUATOR_CHANGED"), ("gold", "QUERY_GOLD_CHANGED"),
    ("checks", "REPORTING_AUDIT_CHANGED_ALIGNMENT"), ("aligned", "REPORTING_AUDIT_CHANGED_ALIGNMENT"),
])
def test_tampered_inputs_or_original_decisions_are_refused(reporting_fixture, change, error):
    f = reporting_fixture
    if change == "predictions":
        with (f["run"] / "predictions.jsonl").open("a", encoding="utf-8") as output: output.write("\n")
    elif change == "protocol":
        protocol = read_json(f["run"] / "protocol.json"); protocol["added"] = "changed"
        write_json(f["run"] / "protocol.json", protocol)
    elif change == "evaluator": (f["root"] / "evaluator" / "scorer.py").write_bytes(b"modified")
    elif change == "gold": f["bundle"]["manifest"]["mutated"] = True
    else:
        original = read_json(f["run"] / "alignment.json")
        if change == "checks": original["turns"][0]["checks"]["action"] = False
        else: original["turns"][0]["aligned"] = False
        write_json(f["run"] / "alignment.json", original)
    with pytest.raises(GoldValidationError, match=error): f["audit"]()
    assert not f["output"].exists()


@pytest.mark.parametrize("change", ["partial", "order", "duplicate", "missing_turn"])
def test_even_rehashed_predictions_require_the_complete_ordered_suite(reporting_fixture, change):
    f = reporting_fixture; rows = deepcopy(f["rows"])
    if change == "partial": rows = rows[:-1]
    if change == "order": rows.reverse()
    if change == "duplicate": rows[1] = deepcopy(rows[0])
    if change == "missing_turn": rows[0]["turns"] = []
    f["sync_predictions"](rows)
    with pytest.raises(GoldValidationError, match="FULL_QUERY_SUITE_REQUIRED|QUERY_TURNS_MISSING"): f["audit"]()
    assert not f["output"].exists()


def test_reporting_audit_refuses_to_overwrite_an_existing_output(reporting_fixture):
    f = reporting_fixture; f["audit"]()
    original = (f["output"] / "reporting-audit.json").read_bytes()
    with pytest.raises(GoldValidationError, match="QUERY_REPORTING_AUDIT_ALREADY_EXISTS"): f["audit"]()
    assert (f["output"] / "reporting-audit.json").read_bytes() == original
