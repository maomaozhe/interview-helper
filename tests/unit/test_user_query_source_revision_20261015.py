"""Narrow title-policy revision and source-only membership contract checks."""
from collections import Counter
from copy import deepcopy
from pathlib import Path
import shutil

import pytest

from eval.common import digest, read_json, read_jsonl, write_json, write_jsonl
from evals.agent_gold.user_query_source_20261015 import validate_dataset
from evals.agent_gold.user_query_source_revision_20261015 import (
    BASE, SOURCE_ONLY_IDS, revise, verify_narrow_revision)


@pytest.fixture(scope="module", autouse=True)
def require_private_base():
    missing = [name for name in ("manifest.json", "freeze.json") if not (BASE / name).is_file()]
    if missing:
        pytest.skip("private v1 Gold metadata unavailable: " + ", ".join(missing))
    freeze = read_json(BASE / "freeze.json")
    missing_files = sum(not (BASE / name).is_file() for name in freeze["files"])
    if missing_files:
        pytest.skip(f"private v1 Gold inputs unavailable: {missing_files} frozen files")


def reseal(path):
    freeze = read_json(path / "freeze.json")
    freeze["files"] = {p.relative_to(path).as_posix(): digest(p.read_bytes())
                       for p in sorted(path.rglob("*")) if p.is_file() and p.name != "freeze.json"}
    write_json(path / "freeze.json", freeze)


@pytest.fixture(scope="module")
def reference(tmp_path_factory):
    root = tmp_path_factory.mktemp("user-query-source-v2")
    output = root / "v2"
    receipt = revise(BASE, output, root / "review")
    return output, receipt


@pytest.fixture
def dataset(reference, tmp_path):
    target = tmp_path / "v2"
    shutil.copytree(reference[0], target)
    return target


def test_revision_preserves_v1_and_all_occurrence_records_and_raw_source_bytes(reference):
    output, receipt = reference
    before, after = validate_dataset(BASE), validate_dataset(output)
    assert verify_narrow_revision(before, after) == {"ACCEPT": 21, "REJECT": 2728, "BOUNDARY": 22}
    assert receipt["v1_unchanged"] and receipt["same_2771_occurrence_question_records"]
    assert set(receipt["changed_files"]) == {"questions.jsonl", "scopes.json", "manifest.json", "README.md", "freeze.json"}
    for name in ["facts.json", "cases.jsonl", "history.json", "census.json"]:
        assert (BASE / name).read_bytes() == (output / name).read_bytes()
    assert len(list((output / "sources").glob("*.md"))) == 184
    for path in (BASE / "sources").glob("*.md"):
        assert path.read_bytes() == (output / "sources" / path.name).read_bytes()
    rows = {q["id"]: q for q in after["questions"]}
    for gid in SOURCE_ONLY_IDS:
        assert rows[gid]["scope_judgments"]["engineering_system_design"]["grade"] == "BOUNDARY"
        assert rows[gid]["occurrence_id"] is rows[gid]["canonical_question_id"] is None
        assert "原题真实存在" in rows[gid]["scope_judgments"]["engineering_system_design"]["basis"]


def test_v2_exposure_is_explicitly_post_initial_v1_test(reference):
    manifest = validate_dataset(reference[0])["manifest"]
    exposure = manifest["exposure"]
    assert exposure["predictions_seen_before_freeze"] is True
    assert exposure["source_only_scope_reviewer_predictions_read"] is False
    assert exposure["revision_decided_from_predictions"] is False
    assert manifest["parent"]["dataset_id"] == BASE.name
    assert manifest["release_eligible"] is False


def test_positive_aliases_must_agree(dataset):
    scopes = read_json(dataset / "scopes.json")
    scopes["engineering_system_design"]["source-only_positive_gold_ids"] = [SOURCE_ONLY_IDS[0]]
    write_json(dataset / "scopes.json", scopes)
    reseal(dataset)
    with pytest.raises(ValueError, match="source-only positive aliases disagree"):
        validate_dataset(dataset)


def test_source_only_positive_and_boundary_must_not_overlap(dataset):
    scopes = read_json(dataset / "scopes.json")
    s = scopes["engineering_system_design"]
    s["source-only_positive_gold_ids"] = s["source_only_positive_gold_ids"] = [SOURCE_ONLY_IDS[0]]
    write_json(dataset / "scopes.json", scopes)
    reseal(dataset)
    with pytest.raises(ValueError, match="source-only grades overlap"):
        validate_dataset(dataset)


def test_source_only_boundary_coverage_is_complete(dataset):
    scopes = read_json(dataset / "scopes.json")
    scopes["engineering_system_design"]["source_only_boundary_gold_ids"].pop()
    write_json(dataset / "scopes.json", scopes)
    reseal(dataset)
    with pytest.raises(ValueError, match="source-only scope coverage incomplete"):
        validate_dataset(dataset)


def test_boundary_membership_must_match_source_judgment(dataset):
    rows = read_jsonl(dataset / "questions.jsonl")
    q = next(row for row in rows if row["id"] == SOURCE_ONLY_IDS[0])
    q["scope_judgments"]["engineering_system_design"]["grade"] = "ACCEPT"
    write_jsonl(dataset / "questions.jsonl", rows)
    reseal(dataset)
    with pytest.raises(ValueError, match="source-only grade/evidence mismatch"):
        validate_dataset(dataset)


def test_duplicate_source_only_membership_is_rejected(dataset):
    scopes = read_json(dataset / "scopes.json")
    scopes["engineering_system_design"]["source_only_boundary_gold_ids"].append(SOURCE_ONLY_IDS[0])
    write_json(dataset / "scopes.json", scopes)
    reseal(dataset)
    with pytest.raises(ValueError, match="source-only list malformed/duplicate"):
        validate_dataset(dataset)


def test_old_v1_without_boundary_field_still_validates_but_omitted_source_only_fails(tmp_path):
    assert len(validate_dataset(BASE)["scopes"]["engineering_system_design"]["source_only_positive_gold_ids"]) == 2
    target = tmp_path / "v1"
    shutil.copytree(BASE, target)
    scopes = read_json(target / "scopes.json")
    for field in ["source-only_positive_gold_ids", "source_only_positive_gold_ids"]:
        scopes["engineering_system_design"][field] = []
    write_json(target / "scopes.json", scopes)
    reseal(target)
    with pytest.raises(ValueError, match="source-only scope coverage incomplete"):
        validate_dataset(target)


def test_revision_refuses_to_overwrite_frozen_output(reference, tmp_path):
    with pytest.raises(ValueError, match="immutable"):
        revise(BASE, reference[0], tmp_path / "unused")


def test_narrow_revision_check_rejects_unrelated_question_changes(reference):
    before = validate_dataset(BASE)
    after = deepcopy(validate_dataset(reference[0]))
    after["questions"][0]["raw_question"] = "unrelated change"
    with pytest.raises(ValueError, match="unrelated question fields"):
        verify_narrow_revision(before, after)
