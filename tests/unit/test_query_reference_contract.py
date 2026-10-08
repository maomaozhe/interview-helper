from eval.reference_contract import query_reference_issues
from eval.common import write_json
from eval.validate_gold import GoldValidationError, validate_gold
import pytest


def test_partial_next_and_explicitly_cleared_filters_are_valid_references():
    assert query_reference_issues([{"id": "next", "turns": [{"expected_plan": {"action": "NEXT"}},
        {"expected_plan": {"action": "LIST", "filters.round": None, "top_n": None}}]}]) == []


def test_chinese_round_display_labels_do_not_pass_the_typed_reference_contract():
    cases = [{"id": "round", "turns": [{"expected_plan": {"filters.round": "二面"}}]}]
    issues = query_reference_issues(cases)
    assert issues[0]["id"] == "round" and issues[0]["kind"] == "invalid_filter_reference"
    assert cases[0]["turns"][0]["expected_plan"]["filters.round"] == "二面"


def test_flat_reference_and_unknown_plan_fields_are_checked():
    issues = query_reference_issues([{"id": "flat", "expected_plan": {"action": "NOT_AN_ACTION", "invented": 3}}])
    assert {issue["kind"] for issue in issues} == {"invalid_plan_reference", "unknown_plan_reference"}


def test_declared_typed_contract_blocks_bad_gold_while_legacy_replay_is_preserved(tmp_path):
    manifest = {"version": "typed-dev", "reference_contract": "typed_query_plan_v1", "routing": [{
        "id": "round", "group_id": "round", "split": "dev", "human_verified": False,
        "message": "只看二面", "expected_plan": {"filters.round": "二面"},
        "assertions": [{"id": "rows", "path": "result.rows", "op": "exists"}]}]}
    write_json(tmp_path / "manifest.json", manifest)
    with pytest.raises(GoldValidationError, match="invalid typed query references"):
        validate_gold(tmp_path, "routing", split="dev")
    del manifest["reference_contract"]
    write_json(tmp_path / "manifest.json", manifest)
    assert validate_gold(tmp_path, "routing", split="dev")["version"] == "typed-dev"
