from eval.metrics import classification_metrics, retrieval_metrics, routing_metrics


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
