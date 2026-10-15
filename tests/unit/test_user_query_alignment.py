"""Source identity and unknown coverage must survive query-level scoring."""
import base64
import json

import pytest

from evals.project_quality.user_query_alignment import assess_case, assess_item, assess_turn


def question(identity, occurrence, canonical="shared"):
    return {"id": identity, "occurrence_id": occurrence, "canonical_question_id": canonical,
            "source_revision_id": "revision-" + occurrence, "source_hash": "hash-" + occurrence,
            "source_spans": [{"quote": "真实原题" + occurrence, "start_line": 2}]}


def source(q):
    return {"occurrence_id": q["occurrence_id"], "revision_id": q["source_revision_id"],
            "raw_file_hash": q["source_hash"], **q["source_spans"][0]}


def test_positive_canonical_cannot_repair_a_negative_displayed_occurrence():
    positive, negative = question("p", "a"), question("n", "b")
    result = assess_item({"canonical_question_id": "shared", "sources": [source(negative)]},
                         [positive, negative], {"POSITIVE": {"p"}, "NEGATIVE": {"n"}, "BOUNDARY": set()})
    assert result["canonical_role"] == "POSITIVE"
    assert result["witnesses"][0]["role"] == "NEGATIVE"
    assert not result["source_support_pass"]
    assert result["literal_source_grounded"] and not result["source_scope_accepted"]


def test_authentic_boundary_source_is_grounded_and_queued_for_review():
    q = question("b", "occ", "boundary")
    bundle = {"questions": [q], "scopes": {"design": {"boundary_gold_ids": ["b"]}}}
    turn = {"case_id": "boundary", "turn_index": 0, "message": "找工程设计", "expected": {
        "accepted_actions": ["SEARCH"], "scope_id": "design"}}
    observed = {"result": {"plan": {"action": "SEARCH"}, "rows": [
        {"canonical_question_id": "boundary", "sources": [source(q)]}]}}
    result = assess_turn(turn, observed, bundle)
    assert result["literal_source_grounded"] and result["review_required"]
    assert not result["source_support_pass"] and not result["aligned"]
    observed["result"]["rows"][0]["sources"][0]["quote"] = "伪造引用"
    result = assess_turn(turn, observed, bundle)
    assert not result["literal_source_grounded"] and result["review_required"]


def test_authentic_source_without_a_scope_label_keeps_grounding_but_requires_review():
    q = question("not-labeled", "occ")
    result = assess_item({"canonical_question_id": "shared", "sources": [source(q)]}, [q],
                         {"POSITIVE": set(), "NEGATIVE": set(), "BOUNDARY": set()})
    assert result["literal_source_grounded"]
    assert result["witnesses"][0]["role"] == "UNVERIFIED"
    assert not result["source_scope_accepted"]


def test_neighboring_source_or_a_changed_quote_is_not_accepted_as_support():
    q = question("p", "a")
    wrong = {**source(q), "quote": "同一来源的另一道题"}
    result = assess_item({"canonical_question_id": "shared", "sources": [wrong]}, [q],
                         {"POSITIVE": {"p"}, "NEGATIVE": set(), "BOUNDARY": set()})
    assert result["witnesses"][0]["role"] == "UNVERIFIED"
    assert not result["source_support_pass"]


def test_unreviewed_result_stays_unknown_and_prevents_alignment():
    p = question("p", "a")
    expected = {"accepted_actions": ["SEARCH"], "scope_id": "design", "min_positive_results": 1,
                "positive_gold_ids": ["p"], "require_source_support": True}
    turn = {"case_id": "case", "turn_index": 0, "message": "找系统场景设计题", "expected": expected}
    observation = {"result": {"plan": {"action": "SEARCH", "filters": {}}, "rows": [
        {"canonical_question_id": "shared", "sources": [source(p)]},
        {"canonical_question_id": "unreviewed", "sources": []}]}}
    result = assess_turn(turn, observation, {"scopes": {"design": {}}, "questions": [p]})
    assert result["canonical_role_counts"] == {"POSITIVE": 1, "UNREVIEWED": 1}
    assert not result["aligned"]


def test_top_results_do_not_certify_an_all_results_request():
    a, b = question("p1", "a", "c1"), question("p2", "b", "c2")
    turn = {"case_id": "all", "turn_index": 0, "message": "列出全部", "expected": {
        "accepted_actions": ["LIST"], "scope_id": "design", "requested_completeness": "all",
        "positive_gold_ids": ["p1", "p2"]}}
    observed = {"result": {"plan": {"action": "LIST"}, "rows": [
        {"canonical_question_id": "c1", "sources": [source(a)]}]}}
    result = assess_turn(turn, observed, {"questions": [a, b], "scopes": {
        "design": {"reference_completeness": "corpus_occurrence_census"}}})
    assert result["known_positive_retrieved"] == 1
    assert not result["completeness_pass"]
    assert not result["aligned"]


def test_count_requires_independent_numbers_and_complete_scope():
    turn = {"case_id": "count", "turn_index": 0, "message": "共有几题", "expected": {
        "accepted_actions": ["COUNT"], "scope_id": "design",
        "expected_counts": {"canonical_questions": 2, "occurrences": 3}}}
    bundle = {"questions": [], "scopes": {"design": {"reference_completeness": "corpus_occurrence_census"}}}
    result = assess_turn(turn, {"result": {"plan": {"action": "COUNT"}, "meta": {
        "counts": {"canonical_questions": 2, "occurrences": 3}}}}, bundle)
    assert result["aligned"]
    bundle["scopes"]["design"]["reference_completeness"] = "panel_only"
    assert not assess_turn(turn, {"result": {"plan": {"action": "COUNT"}, "meta": {
        "counts": {"canonical_questions": 2, "occurrences": 3}}}}, bundle)["aligned"]


def test_revision_and_quote_without_position_cannot_borrow_a_reviewed_occurrence():
    q = question("p", "a")
    # A different, unreviewed question can repeat these words in the same file.
    ambiguous = {"revision_id": q["source_revision_id"], "quote": q["source_spans"][0]["quote"]}
    result = assess_item({"canonical_question_id": "shared", "sources": [ambiguous]}, [q],
        {"POSITIVE": {"p"}, "NEGATIVE": set(), "BOUNDARY": set()})
    assert not result["source_support_pass"]
    assert result["witnesses"][0]["role"] == "UNVERIFIED"


def test_source_summary_uses_exact_position_and_rejects_changed_end_line():
    q = question("p", "a")
    q["source_spans"][0]["end_line"] = 2
    roles = {"POSITIVE": {"p"}, "NEGATIVE": set(), "BOUNDARY": set()}
    item = {"canonical_question_id": "shared", "source_summary": {
        "revision_id": q["source_revision_id"], "spans": q["source_spans"]}}
    assert assess_item(item, [q], roles)["source_support_pass"]
    changed = {"canonical_question_id": "shared", "sources": [{**source(q), "end_line": 9}]}
    assert not assess_item(changed, [q], roles)["source_support_pass"]


def fixture_case():
    a, b, c = [question("p" + str(i), str(i), "c" + str(i)) for i in range(3)]
    expected = {"accepted_actions": ["LIST", "NEXT"], "scope_id": "design", "accepted_sort": ["frequency"],
                "positive_gold_ids": [q["id"] for q in (a, b, c)], "min_positive_results": 1}
    case = {"id": "paging", "turns": [
        {"case_id": "paging", "turn_index": 0, "message": "按频次列出设计题", "expected": expected},
        {"case_id": "paging", "turn_index": 1, "message": "下一页", "expected": {**expected,
            "requires_prior_results": True, "no_repeat_previous_results": True,
            "inherits_scope_from_turn": 0, "requires_conversation_continuity": True}}]}
    def page(action, q, frequency, version):
        return {"result": {"plan": {"action": action, "sort": "frequency", "filters": {"company": "字节跳动"}},
            "meta": {"conversation_id": "conversation", "conversation_version": version},
            "rows": [{"canonical_question_id": q["canonical_question_id"], "sources": [source(q)],
                      "occurrence_count": frequency}]}}
    saved = {"turns": [page("LIST", a, 10, 1), page("NEXT", b, 6, 2)]}
    bundle = {"questions": [a, b, c], "scopes": {"design": {"reference_completeness": "corpus_occurrence_census"}}}
    return case, saved, bundle


def test_next_page_with_distinct_results_continuous_conversation_and_scope_passes():
    case, saved, bundle = fixture_case()
    assert all(r["aligned"] for r in assess_case(case, saved, bundle))


@pytest.mark.parametrize("failure", ["repeated_page", "new_conversation", "stale_version", "dropped_scope", "higher_frequency"])
def test_next_page_cannot_pass_by_only_returning_an_individually_relevant_item(failure):
    case, saved, bundle = fixture_case()
    second = saved["turns"][1]["result"]
    if failure == "repeated_page": second["rows"] = saved["turns"][0]["result"]["rows"]
    if failure == "new_conversation": second["meta"]["conversation_id"] = "fresh-unrelated-conversation"
    if failure == "stale_version": second["meta"]["conversation_version"] = 1
    if failure == "dropped_scope": second["plan"]["filters"] = {}
    if failure == "higher_frequency": second["rows"][0]["occurrence_count"] = 15
    assert not assess_case(case, saved, bundle)[1]["aligned"]


def test_frequency_order_is_measured_in_actual_results_not_only_claimed_sort():
    case, saved, bundle = fixture_case()
    row = {"canonical_question_id": "c2", "sources": [source(bundle["questions"][2])], "occurrence_count": 20}
    saved["turns"][0]["result"]["rows"].append(row)
    assessed = assess_case(case, saved, bundle)[0]
    assert assessed["checks"]["sort"]
    assert not assessed["checks"]["frequency_order"] and not assessed["aligned"]


def test_next_result_set_token_refresh_is_not_misreported_as_scope_change():
    case, saved, bundle = fixture_case()
    for i, observed in enumerate(saved["turns"]):
        body = base64.urlsafe_b64encode(json.dumps({"kind": "result_set", "scope": "same-scope",
            "issued_at": 100 + i}).encode()).decode().rstrip("=")
        observed["result"]["meta"]["result_set_id"] = body + ".fixture-signature"
    assert assess_case(case, saved, bundle)[1]["checks"]["same_result_set"]
    body = base64.urlsafe_b64encode(json.dumps({"scope": "different-scope", "issued_at": 102}).encode()).decode().rstrip("=")
    saved["turns"][1]["result"]["meta"]["result_set_id"] = body + ".fixture-signature"
    assert not assess_case(case, saved, bundle)[1]["checks"]["same_result_set"]


@pytest.mark.parametrize("certification", ["MISSING_ANNOTATION", "NOT_CERTIFIED"])
def test_correct_count_numbers_do_not_override_an_uncertified_turn_scope(certification):
    turn = {"case_id": "count", "turn_index": 0, "message": "算法题共有多少", "expected": {
        "accepted_actions": ["COUNT"], "scope_id": "design", "expected_certification": certification,
        "expected_counts": {"canonical_questions": 2, "occurrences": 3}}}
    bundle = {"questions": [], "scopes": {"design": {"reference_completeness": "corpus_occurrence_census"}}}
    observed = {"result": {"plan": {"action": "COUNT"}, "meta": {"counts": {"canonical_questions": 2, "occurrences": 3}}}}
    assessed = assess_turn(turn, observed, bundle)
    assert assessed["certification_blocked"] and not assessed["aligned"]
    assert not assessed["checks"]["count_certified"]


def test_unknown_results_are_reported_for_review_without_becoming_negative_labels():
    case, saved, bundle = fixture_case()
    saved["turns"][0]["result"]["rows"].append({"canonical_question_id": "unreviewed", "occurrence_count": 5})
    assessed = assess_case(case, saved, bundle)[0]
    assert assessed["review_required"] and not assessed["aligned"]
    assert assessed["canonical_role_counts"].get("NEGATIVE", 0) == 0


def test_score_rejects_predictions_changed_since_collection(monkeypatch, tmp_path):
    from eval.common import digest, write_json, write_jsonl
    from eval.validate_gold import GoldValidationError
    from evals.project_quality import user_query_alignment as runner
    bundle = {"manifest": {"snapshot": {}}, "cases": []}
    monkeypatch.setattr(runner, "load_gold", lambda dataset: bundle)
    write_json(tmp_path / "protocol.json", {"gold_sha256": digest(bundle), "runtime_sha256": "runtime"})
    write_jsonl(tmp_path / "predictions.jsonl", [])
    write_json(tmp_path / "collection.json", {"predictions_sha256": "wrong-hash", "gold_sha256": digest(bundle), "runtime_sha256": "runtime"})
    with pytest.raises(GoldValidationError, match="QUERY_PREDICTIONS_CHANGED"):
        runner.score(tmp_path, tmp_path)


@pytest.mark.parametrize("action,completeness,certification,blocked", [
    ("SEARCH", "page", "NOT_CERTIFIED", False),
    ("LIST", "page", "NOT_CERTIFIED", False),
    ("SEARCH", "all", "NOT_CERTIFIED", True),
    ("COUNT", "all", "NOT_CERTIFIED", True),
    ("SEARCH", "page", "MISSING_ANNOTATION", False),
    ("LIST", "page", "MISSING_ANNOTATION", True),
])
def test_page_relevance_and_full_scope_certification_are_independent(action, completeness, certification, blocked):
    q = question("p", "a")
    turn = {"case_id": "case", "turn_index": 0, "message": "找题", "expected": {
        "accepted_actions": [action], "scope_id": "design", "positive_gold_ids": ["p"],
        "requested_completeness": completeness, "expected_certification": certification}}
    bundle = {"questions": [q], "scopes": {"design": {"reference_completeness": "corpus_occurrence_census_with_boundaries"}}}
    observed = {"result": {"plan": {"action": action}, "rows": [{"canonical_question_id": "shared", "sources": [source(q)]}]}}
    assessed = assess_turn(turn, observed, bundle)
    assert assessed["certification_blocked"] == blocked
    assert (assessed["checks"].get("scope_certified") is False) == blocked
    if not blocked:
        assert assessed["aligned"]


def test_case_exclusions_and_unknown_company_override_global_scope_positive():
    from evals.project_quality.user_query_alignment import role_sets
    known, excluded, unknown = question("p", "a", "p"), question("excluded", "b", "n"), question("unknown", "c", "b")
    roles = role_sets({"positive_gold_ids": ["p", "excluded", "unknown"]}, {
        "positive_gold_ids": ["p", "excluded", "unknown"], "excluded_positive_gold_ids": ["excluded"],
        "case_boundary_gold_ids": ["unknown"]})
    assert roles == {"POSITIVE": {"p"}, "NEGATIVE": {"excluded"}, "BOUNDARY": {"unknown"}}
    for q, role in ((known, "POSITIVE"), (excluded, "NEGATIVE"), (unknown, "BOUNDARY")):
        assessed = assess_item({"canonical_question_id": q["canonical_question_id"], "sources": [source(q)]},
                               [known, excluded, unknown], roles)
        assert assessed["witnesses"][0]["role"] == role


def test_family_coverage_counts_only_actual_positive_witnesses_not_canonical_peers():
    a, b = question("a", "a"), question("b", "b")
    turn = {"case_id": "family", "turn_index": 0, "message": "多种工程系统设计", "expected": {
        "accepted_actions": ["SEARCH"], "scope_id": "design", "positive_gold_ids": ["a", "b"],
        "min_distinct_families": 2}}
    bundle = {"questions": [a, b], "scopes": {"design": {"family_by_gold_id": {"a": "cache", "b": "queue"}}}}
    observed = {"result": {"plan": {"action": "SEARCH"}, "rows": [
        {"canonical_question_id": "shared", "sources": [source(a)]}]}}
    assessed = assess_turn(turn, observed, bundle)
    assert assessed["source_support_pass"]
    assert assessed["family_ids"] == ["cache"]
    assert not assessed["checks"]["family_coverage"] and not assessed["aligned"]
    observed["result"]["rows"][0]["sources"].append(source(b))
    assessed = assess_turn(turn, observed, bundle)
    assert assessed["family_ids"] == ["cache", "queue"] and assessed["aligned"]
