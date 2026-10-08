import pytest

from eval.report import gates


def test_new_label_gate_blocks_engineering_errors_even_when_overall_accuracy_is_high():
    metrics = {"joint_accuracy": .96, "predicted_coverage": .98,
               "coding_focus": {"per_class": {"ENGINEERING": {"precision": .73, "recall": 1}}}}
    legacy = gates("task_labels", metrics, eligible=True)
    current = gates("task_labels", metrics, eligible=True, threshold_version="quality_gate_v3")
    assert legacy["status"] == "NOT_RUN"
    assert current["status"] == "BELOW_GATE"
    assert current["checks"][2]["threshold"] == .90


def test_missing_engineering_measurement_cannot_turn_into_a_passing_label_gate():
    result = gates("task_labels", {"joint_accuracy": .99, "predicted_coverage": 1},
                   eligible=True, threshold_version="quality_gate_v3")
    assert result["status"] == "NOT_RUN"
    with pytest.raises(ValueError, match="UNKNOWN_THRESHOLD_VERSION"):
        gates("task_labels", {}, eligible=True, threshold_version="made_up")


def test_business_engineering_filter_counts_mixed_but_preserves_strict_class_error():
    from eval.scoring import score_labels
    golds = [{"id": str(i), "tags": [], "response_form": "CODE", "coding_focus": focus}
             for i, focus in enumerate(("MIXED", "ENGINEERING", "NONE", "ALGORITHM"))]
    predictions = [{"result": {"response_form": "CODE", "coding_focus": "ENGINEERING"}} for _ in golds]
    metrics, _ = score_labels(golds, predictions)
    assert metrics["coding_focus"]["per_class"]["ENGINEERING"]["precision"] == .25
    assert metrics["task_filter_metrics"]["engineering"]["precision"] == .5
    assert metrics["task_filter_metrics"]["code_engineering"]["tp"] == 2
