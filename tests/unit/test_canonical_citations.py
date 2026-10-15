import json

import pytest

from interview_intelligence.search.citations import primary_candidate_payload, primary_wire_payload
from interview_intelligence.search.reranker import LLMReranker
from test_relevance_retry import Client as BaseClient


class SelectionClient(BaseClient):
    def __init__(self, *, object_id="q0", focus_id="q0", task="DESIGN_TASK", task_evidence=None):
        super().__init__()
        self.object_id, self.focus_id, self.task = object_id, focus_id, task
        self.task_evidence = task_evidence

    def create(self, **kwargs):
        response = super().create(**kwargs)
        value = json.loads(response.choices[0].message.content)
        if "rankings" in value:
            for row in value["rankings"]:
                row.update(object_evidence=self.object_id, focus_evidence=self.focus_id, candidate_task=self.task)
                if self.task_evidence is not None:
                    row["task_evidence"] = self.task_evidence
            response.choices[0].message.content = json.dumps(value)
        return response


def test_selection_resolves_canonical_spaces_and_punctuation_without_using_original_variant():
    text = "设计一个 Cache，支持高并发、高 QPS 读以及新条目插入。"
    candidate = {"canonical_question_id": "cache", "canonical_text": text,
        "source_context": [{"source_context_status": "VERIFIED", "original_question": "设计一个Cache，高qps读；新条目插入"}]}
    client = SelectionClient(object_id="q0", focus_id="q1", task_evidence="设计一个 Cache")
    rows = LLMReranker(client=client, model="test")._rank_candidates("独立系统设计题", [candidate])
    assert len(rows) == 1
    assert rows[0]["relevance_evidence"] == {"object": "设计一个 Cache，", "focus": "支持高并发、高 QPS 读以及新条目插入。"}
    assert all(quote in text for quote in rows[0]["relevance_evidence"].values())
    schema = client.requests[0]["response_format"]["json_schema"]["schema"]
    assert schema["$defs"]["CandidateRank"]["properties"]["object_evidence"]["enum"] == ["", "q0", "q1"]


@pytest.mark.parametrize("bad_id", ["q9", "q500", "q0+q1", "其他候选:q0"])
def test_unknown_or_concatenated_ids_cannot_bypass_exact_evidence_checks(bad_id):
    candidate = {"canonical_question_id": "cache", "canonical_text": "如何设计缓存服务？"}
    rows = LLMReranker(client=SelectionClient(focus_id=bad_id), model="test")._rank_candidates("独立系统设计题", [candidate])
    assert rows == []
    assert rows.audit["candidates"][0]["decision"] == "INVALID_EVIDENCE"


def test_selection_id_is_resolved_within_the_current_candidate_only():
    payload = primary_candidate_payload([
        {"canonical_question_id": "cache", "canonical_text": "缓存服务如何设计？"},
        {"canonical_question_id": "other", "canonical_text": "请求参数。并发多少？"},
    ])
    assert "q1" not in payload[0]["canonical_evidence_options"]
    candidate = {"canonical_question_id": "cache", "canonical_text": payload[0]["question"]}
    rows = LLMReranker(client=SelectionClient(focus_id="q1"), model="test")._rank_candidates("独立系统设计题", [candidate])
    assert rows == []


def test_valid_selection_does_not_override_a_non_design_source_task():
    candidate = {"canonical_question_id": "project", "canonical_text": "介绍缓存服务如何实现？"}
    rows = LLMReranker(client=SelectionClient(task="PROJECT_REPORT"), model="test")._rank_candidates("独立系统设计题", [candidate])
    assert rows == [] and rows.audit["candidates"][0]["decision"] == "TASK_REJECTED"


def test_wrong_literal_original_excerpt_is_still_rejected_instead_of_repaired():
    candidate = {"canonical_question_id": "cache", "canonical_text": "设计一个 Cache，支持高并发。"}
    rows = LLMReranker(client=SelectionClient(object_id="设计一个Cache"), model="test")._rank_candidates("独立系统设计题", [candidate])
    assert rows == [] and rows.audit["candidates"][0]["quote_grounded"] is False


def test_long_question_choices_are_bounded_continuous_canonical_excerpts():
    text = "设计独立存储服务：" + "可靠存储并发读写" * 30 + "；保留恢复机制。"
    payload = primary_candidate_payload([{"canonical_text": text}])[0]
    choices = payload["canonical_evidence_options"]
    assert len(choices) >= 3
    assert all(0 < len(value) <= 120 and value in text for value in choices.values())
    assert "".join(choices.values()) == text


def test_wire_preserves_bound_fragments_and_source_context_without_repeating_question():
    candidates = [{"canonical_question_id": "one", "canonical_text": "设计独立服务，要求可靠读写。",
        "source_context": [{"source_context_status": "VERIFIED", "original_question": "设计独立服务，要求可靠读写。",
                            "occurrence_id": "opaque-occurrence", "revision_id": "opaque-revision"}]}]
    payload = primary_candidate_payload(candidates)
    wire = primary_wire_payload(payload)
    assert "canonical_evidence_options" not in wire[0]
    assert wire[0]["question"] == payload[0]["canonical_evidence_options"]
    assert "".join(wire[0]["question"].values()) == candidates[0]["canonical_text"]
    assert wire[0]["source_context"] == [{"source_context_status": "VERIFIED", "original_question": "设计独立服务，要求可靠读写。"}]
    assert wire[0]["id"] == payload[0]["id"] and payload[0]["source_context"][0]["occurrence_id"] == "opaque-occurrence"
    assert isinstance(payload[0]["question"], str)
