"""The public recipe pins the original queries; private evidence is never fabricated."""
from pathlib import Path

import pytest

from eval.common import digest, read_json
from eval.validate_gold import validate_gold

ROOT = Path(__file__).resolve().parents[2]


def test_original_five_queries_and_existing_thresholds_are_pinned():
    recipe = read_json(ROOT / "eval/scenarios/user_scope_five.json")
    assert [case["query"] for case in recipe["cases"]] == [
        "agent工程设计题", "字节的算法题", "工程场景设计题", "redis场景题", "mq场景题目"]
    assert [case["minimum_positive"] for case in recipe["cases"]] == [5, 5, 10, 5, 5]
    assert recipe["cases"][2]["minimum_families"] == 4
    pointer = read_json(ROOT / "eval/scenarios/user_scope_five.reference.json")
    assert pointer["release_eligible"] is False
    assert [case["message"] for case in pointer["queries"]] == [case["query"] for case in recipe["cases"]]


def test_private_frozen_reference_matches_public_identity():
    pointer = read_json(ROOT / "eval/scenarios/user_scope_five.reference.json")
    dataset = ROOT / pointer["dataset"]
    if not dataset.is_dir():
        pytest.skip("Private r295 source snapshot and delegated reviews are not distributed; no historical score reproduced.")
    manifest = validate_gold(dataset, "routing", split="dev", review_policy="delegated_agent")
    assert digest(manifest) == pointer["dataset_sha256"]
    assert manifest["query_result_reference"]["facts_sha256"] == pointer["facts_sha256"]
    assert len(manifest["routing"]) == 5
    assert len(manifest["query_result_reference"]["review_artifacts"]) == 2
