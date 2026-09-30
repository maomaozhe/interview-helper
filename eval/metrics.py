"""Pure metric functions; no gold generation or score imputation."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from statistics import mean


def classification_metrics(gold: list[str], predicted: list[str], *, positive: str) -> dict:
    if len(gold) != len(predicted):
        raise ValueError("gold and predictions must have equal length")
    confusion = defaultdict(Counter)
    for expected, actual in zip(gold, predicted):
        confusion[expected][actual] += 1
    tp = sum(expected == positive and actual == positive for expected, actual in zip(gold, predicted))
    fp = sum(expected != positive and actual == positive for expected, actual in zip(gold, predicted))
    fn = sum(expected == positive and actual != positive for expected, actual in zip(gold, predicted))
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * precision * recall / (precision + recall) if precision is not None and recall is not None and precision + recall else None
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision,
            "recall": recall, "f1": f1,
            "confusion": {key: dict(values) for key, values in confusion.items()}}


def _dcg(grades: list[int]) -> float:
    return sum((2 ** grade - 1) / math.log2(index + 2)
               for index, grade in enumerate(grades))


def retrieval_metrics(samples: list[dict]) -> dict:
    scored = []
    negative = []
    failed = 0
    for sample in samples:
        relevant = {key: grade for key, grade in sample["relevant"].items() if grade > 0}
        ranked = sample.get("ranked", [])[:50]
        if sample.get("failed"):
            ranked = []
            failed += 1
        if not relevant:
            negative.append(int(not ranked))
            continue
        first_hit = next((index for index, canonical_id in enumerate(ranked, 1)
                          if canonical_id in relevant), None)
        dcg = _dcg([relevant.get(canonical_id, 0) for canonical_id in ranked[:10]])
        ideal = _dcg(sorted(relevant.values(), reverse=True)[:10])
        scored.append({
            "recall_at_5": len(set(ranked[:5]) & relevant.keys()) / len(relevant),
            "recall_at_10": len(set(ranked[:10]) & relevant.keys()) / len(relevant),
            "mrr": 1 / first_hit if first_hit else 0.0,
            "ndcg_at_10": dcg / ideal if ideal else 0.0,
        })
    return {**{key: mean(item[key] for item in scored) if scored else None
               for key in ("recall_at_5", "recall_at_10", "mrr", "ndcg_at_10")},
            "evaluated_queries": len(scored), "negative_queries": len(negative),
            "negative_correct_empty_rate": mean(negative) if negative else None,
            "failed_queries": failed}


def routing_metrics(samples: list[dict]) -> dict:
    tool_correct = []
    task_success = []
    unauthorized_writes = 0
    for sample in samples:
        required = set(sample["required_tools"])
        used = set(sample["used_tools"])
        bad_write = "record_review" in used and not sample.get("allow_write", False)
        unauthorized_writes += int(bad_write)
        tool_correct.append(int(required <= used and not bad_write))
        task_success.append(int(sample.get("task_success", False)))
    return {"tool_selection_accuracy": mean(tool_correct) if tool_correct else None,
            "task_success_rate": mean(task_success) if task_success else None,
            "unauthorized_write_count": unauthorized_writes,
            "evaluated_queries": len(samples)}
