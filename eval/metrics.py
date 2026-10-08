"""Metrics with explicit failure, empty-denominator and coverage semantics."""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from statistics import mean


def prf(tp, fp, fn):
    return {"tp": tp, "fp": fp, "fn": fn,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
            "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None}


def classification_metrics(gold: list[str], predicted: list[str], *, positive: str) -> dict:
    if len(gold) != len(predicted):
        raise ValueError("gold and predictions must have equal length")
    confusion = defaultdict(Counter)
    for expected, actual in zip(gold, predicted):
        confusion[expected][actual] += 1
    labels = sorted(set(gold) - {"FAILED"})
    per_class = {}
    for label in labels:
        per_class[label] = prf(
            sum(e == label and p == label for e, p in zip(gold, predicted)),
            sum(e != label and p == label for e, p in zip(gold, predicted)),
            sum(e == label and p != label for e, p in zip(gold, predicted)))
    return {**per_class.get(positive, prf(0, sum(p == positive for p in predicted), 0)),
            "accuracy": sum(e == p for e, p in zip(gold, predicted)) / len(gold) if gold else None,
            "macro_f1": mean(m["f1"] or 0 for m in per_class.values()) if labels else None,
            "per_class": per_class, "evaluated_samples": len(gold),
            "confusion": {key: dict(values) for key, values in confusion.items()}}


def _dcg(grades):
    return sum((2 ** grade - 1) / math.log2(index + 2) for index, grade in enumerate(grades))


def retrieval_sample(sample):
    grades = sample["relevant"]
    if not isinstance(grades, dict) or any(type(g) is not int or g not in {0, 1, 2} for g in grades.values()):
        raise ValueError("invalid relevance grades")
    relevant = {key: grade for key, grade in grades.items() if grade > 0}
    ranked = sample.get("ranked", [])
    invalid = not isinstance(ranked, list) or any(not isinstance(i, str) for i in ranked)
    if not invalid:
        invalid = len(ranked) != len(set(ranked))
    failed = bool(sample.get("failed")) or invalid
    ranked = [] if failed else ranked[:50]
    common = {"failed": failed, "invalid_ranking": invalid,
              "negative": not relevant, "unjudged_results": sum(i not in grades for i in ranked),
              "relevant_count": len(relevant)}
    if not relevant:
        return {**common, "negative_correct": int(not failed and not ranked)}
    first = next((i for i, key in enumerate(ranked, 1) if key in relevant), None)
    ideal = _dcg(sorted(relevant.values(), reverse=True)[:10])
    return {**common, "recall_at_5": len(set(ranked[:5]) & relevant.keys()) / len(relevant),
            "recall_at_10": len(set(ranked[:10]) & relevant.keys()) / len(relevant),
            "precision_at_10": len(set(ranked[:10]) & relevant.keys()) / 10,
            "mrr": 1 / first if first else 0.0,
            "ndcg_at_10": _dcg([relevant.get(i, 0) for i in ranked[:10]]) / ideal,
            "recall_at_10_upper_bound": min(10, len(relevant)) / len(relevant)}


def retrieval_metrics(samples):
    rows = [retrieval_sample(sample) for sample in samples]
    scored = [r for r in rows if not r["negative"]]
    negatives = [r for r in rows if r["negative"]]
    keys = ("recall_at_5", "recall_at_10", "precision_at_10", "mrr", "ndcg_at_10", "recall_at_10_upper_bound")
    return {**{key: mean(r[key] for r in scored) if scored else None for key in keys},
            "evaluated_queries": len(scored), "negative_queries": len(negatives),
            "negative_correct_empty_rate": mean(r["negative_correct"] for r in negatives) if negatives else None,
            "failed_queries": sum(r["failed"] for r in rows),
            "invalid_rankings": sum(r["invalid_ranking"] for r in rows),
            "unjudged_results": sum(r["unjudged_results"] for r in rows)}


def routing_metrics(samples):
    correct, success, writes = [], [], 0
    for sample in samples:
        required, used = set(sample["required_tools"]), set(sample["used_tools"])
        bad_write = "record_review" in used and not sample.get("allow_write", False)
        writes += int(bad_write)
        forbidden = set(sample.get("forbidden_tools", []))
        correct.append(int(required <= used and not forbidden & used and not bad_write and not sample.get("failed", False)))
        success.append(int(sample.get("task_success", False) and not sample.get("failed", False)))
    return {"tool_selection_accuracy": mean(correct) if correct else None,
            "task_success_rate": mean(success) if success else None,
            "unauthorized_write_count": writes, "evaluated_queries": len(samples)}


def efficiency(rows):
    def percentile(values, fraction):
        values = sorted(values)
        return values[max(0, math.ceil(len(values) * fraction) - 1)] if values else None
    timings = [r["elapsed_ms"] for r in rows if type(r.get("elapsed_ms")) in {int, float} and math.isfinite(r["elapsed_ms"]) and r["elapsed_ms"] >= 0]
    usages = [call for r in rows for call in r.get("model_calls", [])]
    known = [call for call in usages if type(call.get("input_tokens")) is int
             and (type(call.get("output_tokens")) is int or call.get("operation_type") == "EMBEDDING")]
    unavailable = sum("model_calls" not in r or bool(r.get("telemetry_unavailable")) for r in rows)
    phases = {k: percentile([r.get("timings", {}).get(k) for r in rows
              if type(r.get("timings", {}).get(k)) in {int, float} and math.isfinite(r["timings"][k]) and r["timings"][k] >= 0], .95)
              for k in ("queue_ms", "interval_ms", "provider_ms", "ttft_ms", "tool_ms")}
    return {"latency_samples": len(timings), "p50_ms": percentile(timings, .5),
            "p95_ms": percentile(timings, .95), "percentile_method": "nearest_rank",
            "failed_samples": sum(bool(r.get("failed")) for r in rows),
            "model_calls": None if unavailable else len(usages), "observed_model_calls": len(usages),
            "telemetry_unavailable_samples": unavailable, "unknown_usage_calls": len(usages) - len(known),
            "input_tokens_known": sum(c["input_tokens"] for c in usages if type(c.get("input_tokens")) is int),
            "output_tokens_known": sum(c["output_tokens"] for c in usages if type(c.get("output_tokens")) is int),
            "estimated_cost": None, "phase_p95_ms": phases}
