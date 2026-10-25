"""Offline tests of independent pilot evidence, using only fabricated sources."""
from copy import deepcopy

import pytest

from eval.common import digest, read_json, write_json
from eval.pilot import (ANSWER_RUBRIC, SEARCH_MESSAGE, active_facts, assess_pilot,
                        build_pilot, company_oracle)


@pytest.fixture
def pilot_inputs(tmp_path):
    text = "".join(f"Synthetic question {i}.\n" for i in range(1, 14))
    source = tmp_path / "source.md"
    source.write_text(text, encoding="utf-8")
    questions = []
    position = 0
    for i, line in enumerate(text.splitlines(keepends=True), 1):
        questions.append({"id": f"q{i:02}", "interview_id": "byte" if i <= 11 else "tencent",
            "canonical_question_id": "c01" if i == 11 else f"c{i:02}",
            "raw_question": line.rstrip(), "source_spans": [{"start_char": position,
                "end_char": position + len(line.rstrip()), "quote": line.rstrip()}]})
        position += len(line)
    facts = {"snapshot": {"corpus_revision": 1, "indexed_revision": 1,
              "task_annotation_revision": 1, "as_of": "2026-10-15", "task_annotation_policy": "KNOWN"},
        "documents": [{"id": "d1", "build_id": "b1", "decision": "INCLUDED", "revision_id": "r1",
                       "source_hash": digest(source.read_bytes()), "source_path": "source.md"}],
        "interviews": [{"id": key, "build_id": "b1", "analytics_eligible": True,
                        "company_normalized": company, "round": "FIRST"}
                       for key, company in [("byte", "字节"), ("tencent", "腾讯")]],
        "questions": questions, "canonicals": [{"id": q["canonical_question_id"]} for q in questions if q["id"] != "q11"],
        "physical_indices": ["synthetic-index"]}
    semantic = {"facts_sha256": digest(facts), "query": SEARCH_MESSAGE, "reviewer": "synthetic-reviewer",
        "reviewed_at": "2026-10-15T00:00:00Z", "labels": [
            {"occurrence_id": key, "role": role, "reason": "Synthetic test assignment"}
            for key, role in [("q01", "POSITIVE"), ("q02", "POSITIVE"), ("q03", "POSITIVE"), ("q04", "NEGATIVE")]]}
    write_json(tmp_path / "facts.json", facts)
    write_json(tmp_path / "semantic.json", semantic)
    return tmp_path, facts


@pytest.fixture
def dataset(pilot_inputs):
    root, facts = pilot_inputs
    target = root / "gold"
    build_pilot(root / "facts.json", root / "semantic.json", target,
                authorization={"thread_id": "synthetic-test", "user_instruction": "Offline fixture only"})
    return target, facts


def source_row(facts, number, *, frequency=1):
    question = facts["questions"][number - 1]
    return {"canonical_question_id": question["canonical_question_id"], "occurrence_count": frequency,
            "source_summary": {"revision_id": "r1", "spans": question["source_spans"]}}


def turn(action, *, rows=None, company=None, extra_plan=None, meta=None, state=None, version=1):
    plan = {"action": action, "filters": {"company": company}, **(extra_plan or {})}
    return {"failed": False, "run_status": "SUCCEEDED", "tool_trace": [], "result": {
        "plan": plan, "rows": rows or [], "write_event_delta": 0,
        "meta": {"conversation_id": "synthetic-conversation", "conversation_version": version, **(meta or {})},
        "turn_state": state or {}}}


def observations(facts):
    first = [source_row(facts, i, frequency=2 if i == 1 else 1) for i in range(1, 6)]
    second = [source_row(facts, i) for i in range(6, 11)]
    search = [source_row(facts, i) for i in range(1, 4)]
    ids = lambda rows: [row["canonical_question_id"] for row in rows]
    byte_counts = {"canonical_questions": 10, "occurrences": 11, "interviews": 1, "source_documents": 1}
    tencent_counts = {"canonical_questions": 2, "occurrences": 2, "interviews": 1, "source_documents": 1}
    empty_counts = {key: 0 for key in byte_counts}
    listing = lambda rows, action="LIST", version=1: turn(action, rows=rows, company="字节", version=version,
        extra_plan={"sort": "frequency", "page_size": 5}, state={"current_page_ids": ids(rows)})
    count = lambda company, values, version=1: turn("COUNT", company=company, version=version,
        extra_plan={"top_n": None}, meta={"counts": values}, state={"last_count": {"filters": {"company": company}}})
    turns = [
        [listing(first)],
        [count("星际菠萝研究院", empty_counts)],
        [turn("SEARCH", rows=search, extra_plan={"page_size": 5}, meta={"relevance_status": "VERIFIED"},
              state={"current_page_ids": ids(search)}),
         turn("DETAILS", rows=search[:1], version=2, state={"current_page_ids": ids(search)})],
        [turn("STATS", company="字节", extra_plan={"group_by": "round"}, rows=[{"key": "FIRST", "occurrence_count": 11}])],
        [turn("ANSWER", extra_plan={"answer_basis": "GENERAL_KNOWLEDGE",
            "answer_text": "COUNT(*) counts all 3 rows. COUNT(x) ignores NULL: 2. COUNT(DISTINCT x) ignores NULL and duplicates: 1. This is general SQL knowledge."},
              meta={"evidence_question_ids": []})],
        [listing(first), listing(second, action="NEXT", version=2)],
        [turn("CLARIFY", extra_plan={"clarification": "Which subject?"}), listing(first, version=2)],
        [count("字节", byte_counts), count("腾讯", tencent_counts, version=2)],
    ]
    names = ["p01-list", "p02-negative", "p03-search-reference", "p04-stats", "p05-answer", "p06-pagination", "p07-clarification", "p08-correction"]
    return [{"id": name, "snapshot": facts["snapshot"], "failed": False, "turns": items} for name, items in zip(names, turns)]


def test_first_stage_has_four_turns_and_complete_pilot_twelve(dataset):
    path, _ = dataset
    manifest = read_json(path / "manifest.json")
    assert [len(c["turns"]) for c in manifest["routing"]] == [1, 1, 2, 1, 1, 2, 2, 2]
    assert manifest["release_eligible"] is False
    assert all(not c["human_verified"] and c["split"] == "dev" for c in manifest["routing"])


def test_oracle_excludes_ineligible_and_excluded_builds(pilot_inputs):
    _, facts = pilot_inputs
    facts["interviews"].append({"id": "excluded", "build_id": "b1", "analytics_eligible": False,
                                "company_normalized": "字节", "round": "SECOND"})
    facts["questions"].append({**facts["questions"][0], "id": "ineligible", "interview_id": "excluded"})
    oracle = company_oracle(active_facts(facts), "字节")
    assert oracle["counts"] == {"canonical_questions": 10, "occurrences": 11, "interviews": 1, "source_documents": 1}
    assert oracle["ids"] == [f"c{i:02}" for i in range(1, 11)]
    assert oracle["frequencies"] == [2] + [1] * 9
    facts["documents"][0]["decision"] = "EXCLUDED"
    assert active_facts(facts) == []


def test_stale_source_review_and_changed_source_are_rejected(pilot_inputs):
    root, facts = pilot_inputs
    facts["snapshot"]["corpus_revision"] = 2
    write_json(root / "facts.json", facts)
    kwargs = {"authorization": {"thread_id": "test", "user_instruction": "test"}}
    with pytest.raises(ValueError, match="REVIEW_FACTS_MISMATCH"):
        build_pilot(root / "facts.json", root / "semantic.json", root / "gold", **kwargs)
    review = read_json(root / "semantic.json")
    review["facts_sha256"] = digest(facts)
    write_json(root / "semantic.json", review)
    (root / "source.md").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="SOURCE_HASH_MISMATCH"):
        build_pilot(root / "facts.json", root / "semantic.json", root / "gold", **kwargs)


def test_independent_first_stage_passes_without_self_reported_success(dataset):
    path, facts = dataset
    result = assess_pilot(path, observations(facts)[:3], limit=3)
    assert result["counts"] == {"PASS": 3}
    assert result["complete"] is False


@pytest.mark.parametrize("corruption", ["frequency", "canonical", "source", "reference", "state", "conversation"])
def test_wrong_results_cannot_hide_behind_success_or_correct_tools(dataset, corruption):
    path, facts = dataset
    rows = observations(facts)[:3]
    if corruption == "frequency":
        rows[0]["turns"][0]["result"]["rows"][0]["occurrence_count"] = 99
    elif corruption == "canonical":
        rows[0]["turns"][0]["result"]["rows"][0]["canonical_question_id"] = "c12"
    elif corruption == "source":
        rows[2]["turns"][0]["result"]["rows"][0]["source_summary"] = source_row(facts, 4)["source_summary"]
    elif corruption == "reference":
        rows[2]["turns"][1]["result"]["rows"] = [source_row(facts, 2)]
    elif corruption == "state":
        rows[2]["turns"][1]["result"]["turn_state"]["current_page_ids"] = ["c02"]
    else:
        rows[2]["turns"][1]["result"]["meta"]["conversation_id"] = "other-conversation"
    assert assess_pilot(path, rows, limit=3)["counts"].get("FAIL") == 1


def test_unreviewed_search_result_stays_unknown_and_negative_fails(dataset):
    path, facts = dataset
    rows = observations(facts)[:3]
    rows[2]["turns"][0]["result"]["rows"].append(source_row(facts, 5))
    assert assess_pilot(path, rows, limit=3)["counts"] == {"PASS": 2, "UNKNOWN": 1}
    rows[2]["turns"][0]["result"]["rows"][-1] = source_row(facts, 4)
    assert assess_pilot(path, rows, limit=3)["counts"] == {"PASS": 2, "FAIL": 1}


def test_search_duplicates_do_not_inflate_a_correct_page(dataset):
    path, facts = dataset
    rows = observations(facts)[:3]
    rows[2]["turns"][0]["result"]["rows"].extend([source_row(facts, 1)] * 3)
    result = assess_pilot(path, rows, limit=3)
    failures = {c["id"] for c in result["cases"][2]["checks"] if c["status"] == "FAIL"}
    assert {"turn0.unique_search_questions", "turn0.requested_page_bound"} <= failures


def test_answer_requires_bound_independent_review_and_evidence_quote(dataset):
    path, facts = dataset
    rows = observations(facts)
    assert assess_pilot(path, rows)["counts"] == {"PASS": 7, "UNKNOWN": 1}
    review = {"predictions_sha256": digest(rows), "rubric_sha256": digest(ANSWER_RUBRIC),
        "reviewer_kind": "agent", "reviewer": "independent-reviewer", "reviewed_at": "2026-10-15T00:00:00Z",
        "assessments": {key: {"passed": True, "evidence_quote": "COUNT(*) counts all 3 rows.", "reason": "Synthetic correct answer"}
                        for key in ANSWER_RUBRIC}}
    assert assess_pilot(path, rows, answer_review=review)["counts"] == {"PASS": 8}
    review["assessments"]["count_distinct"]["passed"] = False
    assert assess_pilot(path, rows, answer_review=review)["counts"] == {"PASS": 7, "FAIL": 1}
    review["assessments"]["count_distinct"]["evidence_quote"] = "Not in the answer"
    with pytest.raises(ValueError, match="EVIDENCE_INVALID"):
        assess_pilot(path, rows, answer_review=review)
    review["predictions_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="IDENTITY_MISMATCH"):
        assess_pilot(path, rows, answer_review=review)


def test_gold_oracle_and_prediction_identity_are_sealed(dataset):
    path, facts = dataset
    rows = observations(facts)[:3]
    rows[0]["snapshot"] = {**rows[0]["snapshot"], "corpus_revision": 5}
    with pytest.raises(ValueError, match="PREDICTION_SNAPSHOT_MISMATCH"):
        assess_pilot(path, rows, limit=3)
    (path / "pilot-oracle.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="frozen label file changed"):
        assess_pilot(path, observations(facts)[:3], limit=3)
