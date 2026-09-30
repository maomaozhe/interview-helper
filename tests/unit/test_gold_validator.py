import importlib
import json

import pytest


def test_gold_manifest_rejects_missing_human_labels(tmp_path):
    validator = importlib.import_module("eval.validate_gold")
    manifest = {
        "version": "v1", "status": "draft",
        "extraction": [], "dedup": [], "retrieval": [], "routing": [],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(validator.GoldValidationError, match="30"):
        validator.validate_gold(tmp_path)


def test_gold_manifest_detects_dev_test_source_leakage(tmp_path):
    validator = importlib.import_module("eval.validate_gold")
    same = {"source_hash": "a" * 64, "split": "test", "human_verified": True}
    manifest = {
        "version": "v1", "status": "frozen",
        "extraction": [same] * 30,
        "dedup": [{"label": "SAME", "split": "test", "human_verified": True}] * 150,
        "retrieval": [{"query": "q", "split": "test", "human_verified": True}] * 50,
        "routing": [{"query": "q", "split": "test", "human_verified": True}] * 50,
    }
    manifest["extraction"][0] = {**same, "split": "dev"}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(validator.GoldValidationError, match="leakage"):
        validator.validate_gold(tmp_path)
