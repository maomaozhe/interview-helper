"""Explicit delegated review preserves the default human-only gate."""
import json

import pytest

from eval.validate_gold import GoldValidationError, validate_gold


def corpus(tmp_path):
    rows = [{"id": f"sql-{i}", "group_id": f"scope-{i}", "split": "test", "human_verified": False,
             "agent_verified": True, "request": {}, "review": {"reviewer": "Codex",
                 "reviewer_kind": "agent", "reviewed_at": "2026-10-05T11:30:00+00:00",
                 "guide_version": "annotation_v2", "basis": "independent exported-facts oracle"},
             "assertions": [{"id": "ids", "path": "result.rows", "op": "eq", "value": []}]} for i in range(20)]
    value = {"version": "delegated-test", "kind": "corpus", "status": "frozen", "sql": rows,
             "snapshot": {"corpus_revision": 293, "indexed_revision": 293,
                 "task_annotation_revision": 30, "as_of": "2026-10-05"},
             "review_authorization": {"policy": "delegated_agent", "thread_id": "current-user-task",
                 "user_instruction": "涉及人工的决定，你来做"}}
    (tmp_path / "manifest.json").write_text(json.dumps(value), encoding="utf-8")
    return value


def test_agent_labels_never_pass_the_default_human_gate(tmp_path):
    corpus(tmp_path)
    with pytest.raises(GoldValidationError, match="human verification"):
        validate_gold(tmp_path, "sql")
    valid = validate_gold(tmp_path, "sql", review_policy="delegated_agent")
    assert all(not r["human_verified"] for r in valid["sql"])


@pytest.mark.parametrize("mutation", ["authorization", "basis", "human", "agent", "size"])
def test_delegated_review_requires_explicit_provenance_and_keeps_minimum_size(tmp_path, mutation):
    value = corpus(tmp_path)
    if mutation == "authorization": value.pop("review_authorization")
    if mutation == "basis": value["sql"][0]["review"].pop("basis")
    if mutation == "human": value["sql"][0]["human_verified"] = True
    if mutation == "agent": value["sql"][0]["agent_verified"] = False
    if mutation == "size": value["sql"].pop()
    (tmp_path / "manifest.json").write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(GoldValidationError):
        validate_gold(tmp_path, "sql", review_policy="delegated_agent")
