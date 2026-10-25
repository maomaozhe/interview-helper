import pytest

from eval.metrics import classification_metrics, efficiency, retrieval_metrics, routing_metrics


def test_classification_counts_micro_and_zero_denominators():
    metrics = classification_metrics(["SAME", "DIFFERENT", "SAME"],
                                     ["SAME", "SAME", "DIFFERENT"], positive="SAME")
    assert metrics["precision"] == 0.5
    assert metrics["recall"] == 0.5
    assert metrics["confusion"]["SAME"]["DIFFERENT"] == 1
    assert classification_metrics([], [], positive="SAME")["f1"] is None


def test_retrieval_metrics_use_grades_and_do_not_drop_failed_query():
    samples = [
        {"relevant": {"a": 2, "b": 1}, "ranked": ["b", "x", "a"]},
        {"relevant": {"c": 2}, "ranked": [], "failed": True},
    ]
    result = retrieval_metrics(samples)
    assert result["recall_at_10"] == 0.5
    assert result["failed_queries"] == 1
    assert result["mrr"] == 0.5
    assert 0 < result["ndcg_at_10"] < 1


def test_routing_disallows_unexpected_write_even_if_required_read_present():
    samples = [{"required_tools": ["query_question_stats"],
                "used_tools": ["query_question_stats", "record_review"],
                "allow_write": False, "task_success": False}]
    result = routing_metrics(samples)
    assert result["tool_selection_accuracy"] == 0
    assert result["unauthorized_write_count"] == 1


@pytest.mark.parametrize("operation", ["QUERY_PLAN", "EMBEDDING"])
@pytest.mark.parametrize("field", ["input_tokens", "output_tokens"])
@pytest.mark.parametrize("invalid", [-1, True, False, 1.5, "3"])
def test_efficiency_invalid_usage_is_unknown_and_excluded_from_known_totals(operation, field, invalid):
    call = {"operation_type": operation, "input_tokens": 5, "output_tokens": 2, field: invalid}
    metrics = efficiency([{"model_calls": [call]}])
    assert metrics["unknown_usage_calls"] == 1
    assert metrics["input_tokens_known"] == (0 if field == "input_tokens" else 5)
    assert metrics["output_tokens_known"] == (0 if field == "output_tokens" else 2)


@pytest.mark.parametrize("operation,inputs,outputs,unknown", [
    ("QUERY_PLAN", 0, 0, 0), ("EMBEDDING", 0, 0, 0),
    ("QUERY_PLAN", 5, None, 1), ("QUERY_PLAN", None, 2, 1),
    ("EMBEDDING", 5, None, 0), ("EMBEDDING", None, None, 1),
])
def test_efficiency_zero_and_embedding_null_output_have_explicit_meaning(operation, inputs, outputs, unknown):
    metrics = efficiency([{"model_calls": [{"operation_type": operation,
        "input_tokens": inputs, "output_tokens": outputs}]}])
    assert metrics["unknown_usage_calls"] == unknown
    assert metrics["input_tokens_known"] == (inputs or 0)
    assert metrics["output_tokens_known"] == (outputs or 0)
