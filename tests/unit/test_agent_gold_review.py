"""Delegated extraction review cannot impersonate humans or conceal misses."""
from copy import deepcopy

import pytest

from eval.common import digest, read_json, write_json, write_jsonl
from eval.scoring import score_extraction
from eval.validate_gold import GoldValidationError, MINIMUM_TEST_SIZE, _validate_item, load_dataset, validate_gold
from evals.agent_gold.adjudicate import apply_decisions
from evals.agent_gold.align import finalize, propose
from evals.agent_gold.build import review_row, stamp


@pytest.fixture
def observation(tmp_path):
    text = "Redis 为什么快？\n"
    (tmp_path / "source.md").write_text(text, encoding="utf-8")
    q = {"id": "g", "session_id": "s", "raw_question": text.strip(), "topic_l1": "Redis",
         "topic_l2": "性能优化", "question_type": "PRINCIPLE",
         "source_spans": [{"start_char": 0, "end_char": len(text.strip()), "quote": text.strip()}]}
    gold = {"id": "doc", "source_path": "source.md", "source_hash": digest((tmp_path / "source.md").read_bytes()),
            "sample_kind": "positive", "sessions": [{"id": "s", "metadata": {"company": None, "round": "FIRST", "position": None}}],
            "questions": [q], "followups": []}
    pred = {"result": {"questions": [{**q, "id": "p", "session_id": "ps"}], "sessions": [
        {"id": "ps", "metadata": deepcopy(gold["sessions"][0]["metadata"])}], "followups": []}}
    alignment = {"id": "doc", "human_verified": False, "agent_verified": True,
        "prediction_sha256": digest(pred["result"]), "review": stamp("独立原文审核，匹配同一提问意图。"),
        "pairs": [{"gold_id": "g", "prediction_id": "p"}], "sessions": [{"gold_id": "s", "prediction_id": "ps"}]}
    return tmp_path, gold, pred, alignment


def test_delegated_alignment_requires_explicit_policy(observation):
    root, gold, pred, alignment = observation
    with pytest.raises(GoldValidationError, match="human review"):
        score_extraction([gold], [pred], root, {"doc": alignment})
    metrics, rows = score_extraction([gold], [pred], root, {"doc": alignment}, review_policy="delegated_agent")
    assert metrics["f1"] == 1 and rows[0]["status"] == "PASS"
    assert alignment["human_verified"] is False


@pytest.mark.parametrize("field,value", [("agent_verified", False), ("human_verified", True),
                                        ("reviewer_kind", "human"), ("basis", "")])
def test_delegated_alignment_rejects_unsigned_or_false_provenance(observation, field, value):
    root, gold, pred, alignment = observation
    (alignment if field.endswith("verified") else alignment["review"])[field] = value
    with pytest.raises(GoldValidationError):
        score_extraction([gold], [pred], root, {"doc": alignment}, review_policy="delegated_agent")


def test_review_pending_is_unknown_and_never_conceals_a_missing_question(observation):
    root, gold, pred, alignment = observation
    gold["questions"][0].update(attributes_to_review=["question_type"], review_basis="原文未明确题型。")
    gold["sessions"][0].update(metadata_to_review=["company"], review_basis="平台名称与雇主身份不明确。")
    pred["result"]["sessions"][0]["metadata"]["company"] = "猜测的雇主"
    alignment["prediction_sha256"] = digest(pred["result"])
    metrics, rows = score_extraction([gold], [pred], root, {"doc": alignment}, review_policy="delegated_agent")
    assert rows[0]["status"] == "UNKNOWN"
    assert metrics["metadata_pending_review_count"] == metrics["matched_attribute_pending_review_count"] == 1
    assert "company" not in metrics["metadata_field_counts"]
    assert "question_type" not in metrics["matched_attribute_counts"]
    pred["result"]["questions"] = []
    metrics, rows = score_extraction([gold], [pred], root, {}, review_policy="delegated_agent")
    assert metrics["fn"] == 1 and metrics["f1"] == 0 and rows[0]["status"] == "FAIL"


@pytest.mark.parametrize("target,key", [("question", "raw_question"), ("session", "source_path")])
def test_pending_masks_cannot_disable_identity_or_evidence_validation(observation, target, key):
    root, gold, _, _ = observation
    item = gold["questions"][0] if target == "question" else gold["sessions"][0]
    item["attributes_to_review" if target == "question" else "metadata_to_review"] = [key]
    item["review_basis"] = "明确待审核依据。"
    with pytest.raises(GoldValidationError, match="invalid pending"):
        _validate_item(root, "extraction", gold, formal=True, labels=True, sources=True)


def test_alignment_finalization_requires_decisions_for_unmatched_items(tmp_path):
    proposals = tmp_path / "alignment.proposals.jsonl"
    write_jsonl(proposals, [{"id": "doc", "pairs": [], "sessions": []}])
    write_jsonl(tmp_path / "alignment.review.jsonl", [{"sample_id": "doc", "gold_id": "g", "candidates": []}])
    choices = tmp_path / "decisions.json"
    write_json(choices, {})
    with pytest.raises(GoldValidationError, match="every unresolved"):
        finalize(proposals, choices, tmp_path / "signed.jsonl")
    write_json(choices, {"g": {"prediction_id": "invented", "basis": "不能凭空匹配。"}})
    with pytest.raises(GoldValidationError, match="outside same-source"):
        finalize(proposals, choices, tmp_path / "signed.jsonl")
    assert not (tmp_path / "signed.jsonl").exists()


@pytest.fixture
def adjudication_base(observation, monkeypatch):
    root, gold, _, _ = observation
    monkeypatch.setitem(MINIMUM_TEST_SIZE, "extraction", 1)
    monkeypatch.setitem(MINIMUM_TEST_SIZE, "task_labels", 1)
    gold.update(review_row(gold["id"], gold["source_hash"], [], "独立阅读原文。"))
    gold["sessions"][0].update(metadata_to_review=["company"], review_basis="雇主待确认。")
    gold["questions"][0].update(question_type="OTHER", attributes_to_review=["question_type"], review_basis="作答方式待确认。")
    label = {**review_row("old", gold["source_hash"], [], "明确代码任务。"),
        "source_path": gold["source_path"], "source_hash": gold["source_hash"], "raw_question": gold["questions"][0]["raw_question"],
        "source_spans": gold["questions"][0]["source_spans"], "response_form": "CODE", "coding_focus": "ENGINEERING"}
    write_json(root / "manifest.json", {"version": "v2", "kind": "corpus", "status": "frozen",
        "extraction": "extraction.jsonl", "task_labels": "task_labels.jsonl",
        "review_authorization": {"policy": "delegated_agent", "thread_id": "test", "user_instruction": "委托标注"},
        "snapshot": {"corpus_revision": 293, "indexed_revision": 293, "task_annotation_revision": 30, "as_of": "2026-10-05"}})
    write_jsonl(root / "extraction.jsonl", [gold])
    write_jsonl(root / "task_labels.jsonl", [label])
    write_jsonl(root / "needs_user_review.jsonl", [
        {"id": "review-cpp-template", "sample_id": "new", "quote": label["raw_question"], "candidate": {**label, "id": "new"}},
        {"id": "review-boss-company", "sample_id": "doc", "session_id": "s", "quote": "boss"},
        {"id": "review-lru-task", "sample_id": "doc", "question_id": "g", "quote": label["raw_question"]}])
    write_json(root / "freeze.json", {"dataset_sha256": digest(load_dataset(root)),
        "file_hashes": {p.name: digest(p.read_bytes()) for p in root.iterdir() if p.is_file()}})
    decisions = [{"id": key, "value": value, "answer": answer, "reviewer_kind": "human", "evidence": "direct_user_reply",
        "reviewed_at": "2026-10-06T01:00:00+08:00", "question_item_id": str(i)}
        for i, (key, value, answer) in enumerate((("review-cpp-template", "MIXED", "MIXED：同时包含两者"),
                                                ("review-boss-company", "BOSS直聘", "确认是BOSS直聘公司")))]
    file = root.with_name(root.name + "-user-decisions.json")
    write_json(file, decisions)
    return root, file, decisions


def test_user_field_adjudication_creates_new_seal_without_claiming_human_rows(adjudication_base):
    base, file, decisions = adjudication_base
    before = {p.name: p.read_bytes() for p in base.iterdir() if p.is_file()}
    output = base.with_name(base.name + "-v3")
    result = apply_decisions(base, file, output)
    dataset = validate_gold(output, "task_labels", review_policy="delegated_agent")
    assert result["resolved"] == 2 and result["human_verified_rows"] == 0
    assert dataset["task_labels"][-1]["coding_focus"] == "MIXED"
    assert dataset["task_labels"][-1]["field_reviews"]["coding_focus"]["answer"] == decisions[0]["answer"]
    session = dataset["extraction"][0]["sessions"][0]
    assert session["metadata"]["company"] == "BOSS直聘" and session["metadata_to_review"] == []
    assert dataset["parent_dataset_sha256"] == read_json(base / "freeze.json")["dataset_sha256"]
    assert {p.name: p.read_bytes() for p in base.iterdir() if p.is_file()} == before
    with pytest.raises(GoldValidationError, match="human verification"):
        validate_gold(output, "task_labels")
    with pytest.raises(GoldValidationError, match="already exists"):
        apply_decisions(base, file, output)


def test_failed_collection_without_result_stays_an_explicit_unmatched_question(adjudication_base):
    from eval.common import read_jsonl
    base, _, _ = adjudication_base
    predictions = base.with_name(base.name + "-failed.jsonl")
    write_jsonl(predictions, [{"id": "doc", "failed": True, "error_code": "MODEL_OUTPUT_TRUNCATED"}])
    work = base.with_name(base.name + "-alignment")
    propose(base, predictions, work)
    todo = read_jsonl(work / "alignment.review.jsonl")
    assert len(todo) == 1 and todo[0]["candidates"] == []
    decisions = work / "decisions.json"
    write_json(decisions, {"g": {"prediction_id": None, "basis": "Failed collection remains FN."}})
    finalize(work / "alignment.proposals.jsonl", decisions, work / "signed.jsonl")
    alignment = read_jsonl(work / "signed.jsonl")[0]
    assert alignment["pairs"] == [] and alignment["human_verified"] is False
    metrics, _ = score_extraction(load_dataset(base)["extraction"], read_jsonl(predictions),
                                 base, {"doc": alignment}, review_policy="delegated_agent")
    assert metrics["fn"] == 1 and metrics["tp"] == 0


@pytest.mark.parametrize("field,value", [("reviewer_kind", "agent"), ("evidence", "model_prediction"),
                                        ("answer", ""), ("value", "invented"), ("id", "another-sample")])
def test_user_adjudication_requires_a_pending_item_and_direct_reply(adjudication_base, field, value):
    base, file, decisions = adjudication_base
    decisions[0][field] = value
    write_json(file, decisions)
    output = base.with_name(base.name + "-rejected")
    with pytest.raises(GoldValidationError):
        apply_decisions(base, file, output)
    assert not output.exists()


def test_user_adjudication_rejects_tampered_parent(adjudication_base):
    base, file, _ = adjudication_base
    (base / "source.md").write_text("被修改的来源", encoding="utf-8")
    with pytest.raises(GoldValidationError, match="frozen label file changed"):
        apply_decisions(base, file, base.with_name(base.name + "-rejected"))


def test_provenance_erratum_keeps_labels_and_human_field_history(adjudication_base):
    base, file, _ = adjudication_base
    reviewed = base.with_name(base.name + "-v3")
    apply_decisions(base, file, reviewed)
    note = base.with_name(base.name + "-provenance.json")
    write_json(note, {"manifest_updates": {"blind_review": "Initial blind review followed by source adjudication."}})
    revised = base.with_name(base.name + "-v4")
    apply_decisions(reviewed, None, revised, note)
    for name in ("extraction.jsonl", "task_labels.jsonl", "human_review_decisions.json"):
        assert (revised / name).read_bytes() == (reviewed / name).read_bytes()
    assert read_json(revised / "manifest.json")["blind_review"].endswith("source adjudication.")
    write_json(note, {"manifest_updates": {"review_authorization": {"policy": "human"}}})
    with pytest.raises(GoldValidationError, match="cannot change"):
        apply_decisions(reviewed, None, base.with_name(base.name + "-rejected"), note)


def test_later_handwritten_adjudication_clears_mask_and_retains_earlier_user_replies(adjudication_base):
    base, file, earlier = adjudication_base
    reviewed = base.with_name(base.name + "-reviewed")
    apply_decisions(base, file, reviewed)
    reply = {"id": "review-lru-task", "value": "ALGORITHM", "answer": "LRU 缓存裁定为手写",
        "reviewer_kind": "human", "evidence": "direct_user_reply", "reviewed_at": "2026-10-06T02:00:00+08:00", "question_item_id": "lru-review"}
    later_file = base.with_name(base.name + "-lru-reply.json")
    write_json(later_file, [reply])
    final = base.with_name(base.name + "-final")
    result = apply_decisions(reviewed, later_file, final)
    dataset = validate_gold(final, "extraction", review_policy="delegated_agent")
    question = dataset["extraction"][0]["questions"][0]
    assert result["pending"] == 0 and result["human_verified_rows"] == 0
    assert question["question_type"] == "ALGORITHM" and question["attributes_to_review"] == []
    assert question["field_reviews"]["question_type"]["answer"] == reply["answer"]
    assert read_json(final / "human_review_decisions.json") == earlier + [reply]
    assert dataset["human_field_adjudications"] == earlier + [reply]
    assert validate_gold(reviewed, "extraction", review_policy="delegated_agent")["extraction"][0]["questions"][0]["attributes_to_review"] == ["question_type"]
