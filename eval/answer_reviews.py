"""Load independent answer assessments without modifying collected predictions."""
from __future__ import annotations

from eval.common import digest, read_json
from eval.validate_gold import require


CONTRACT = "query_answer_reviews_v1"


def load_answer_reviews(path, manifest, predictions, golds, rows):
    """Bind reviews to exact input bytes, case and zero-based conversation turn.

    Missing reviews are intentionally allowed: the answer scorer emits UNKNOWN.
    A supplied, stale or ambiguous assessment is an invalid input, never ignored.
    """
    if path is None:
        return {}
    document = read_json(path)
    require(isinstance(document, dict) and document.get("schema") == CONTRACT,
            "ANSWER_REVIEWS_SCHEMA_INVALID")
    require(document.get("dataset_sha256") == digest(manifest), "ANSWER_REVIEWS_DATASET_CHANGED")
    require(document.get("predictions_sha256") == digest(predictions.read_bytes()),
            "ANSWER_REVIEWS_PREDICTIONS_CHANGED")
    entries = document.get("reviews")
    require(isinstance(entries, list), "ANSWER_REVIEWS_LIST_REQUIRED")
    cases, observations = {g["id"]: g for g in golds}, {p["id"]: p for p in rows}
    result = {}
    from eval.answer_quality import score_answer
    for entry in entries:
        require(isinstance(entry, dict), "ANSWER_REVIEW_OBJECT_REQUIRED")
        case_id, index = entry.get("case_id"), entry.get("turn_index")
        require(isinstance(case_id, str) and case_id in cases, "ANSWER_REVIEW_CASE_INVALID")
        turns = cases[case_id].get("turns", [cases[case_id]])
        require(type(index) is int and 0 <= index < len(turns), "ANSWER_REVIEW_TURN_INVALID")
        key = (case_id, index)
        require(key not in result, "ANSWER_REVIEW_DUPLICATE")
        require(turns[index].get("answer_rubric") is not None, "ANSWER_REVIEW_RUBRIC_REQUIRED")
        review = {k: v for k, v in entry.items() if k not in {"case_id", "turn_index"}}
        observed = observations[case_id].get("turns", [observations[case_id]])
        require(index < len(observed), "ANSWER_REVIEW_OBSERVATION_MISSING")
        score_answer(turns[index], observed[index], review=review)
        result[key] = review
    return result
