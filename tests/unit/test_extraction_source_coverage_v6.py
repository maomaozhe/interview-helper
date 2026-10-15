"""Test v6's evidence coverage repairs; model quality needs a real paired run."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from interview_intelligence.config import Settings
from interview_intelligence.extraction.provider import OpenAICompatibleExtractor, _source_units
from interview_intelligence.ingestion.extraction_cache import extraction_config_hash
from interview_intelligence.resources import resource_path
from interview_intelligence.taxonomy import load_taxonomy


class Client:
    def __init__(self, payloads, finish_reasons=None):
        self.payloads = list(payloads)
        self.finish_reasons = list(finish_reasons or ["stop"] * len(self.payloads))
        self.calls = []
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason=self.finish_reasons.pop(0),
            message=SimpleNamespace(content=json.dumps(self.payloads.pop(0), ensure_ascii=False)))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=80), model="fixture")


def question(local_id, quote, quote_index=None):
    return {"local_id": local_id, "raw_quote": quote, "quote_index": quote_index,
            "normalized_question": quote, "topic_id": "fundamentals.operating_systems",
            "question_type": "KNOWLEDGE", "evidence_kind": "INTERVIEW_QUESTION", "confidence": .9,
            "response_form": "VERBAL", "coding_focus": "NONE"}


def interview(local_id, questions):
    return {"local_id": local_id, "session_kind": "SPECIFIC_ROUND",
            "metadata": {key: None for key in ("company_raw", "position_raw", "round_raw", "interview_date_raw", "publish_date_raw")},
            "questions": questions, "followups": []}


def coverage(units, classification, refs=(), reason="原文单元独立裁定"):
    return {"unit_ids": list(units), "classification": classification,
            "question_refs": list(refs), "reason": reason}


def payload(interviews, ledger, *, document_kind="INTERVIEW_REPORT"):
    return {"document_kind": document_kind, "exclusion_reason": None,
            "interviews": interviews, "source_coverage": ledger}


def extractor(client, **kwargs):
    return OpenAICompatibleExtractor(client=client, model="fixture",
        prompt_path=resource_path("prompts/extract_question_v6.md"), sleep=lambda _: None, **kwargs)


def single_source_payload():
    return payload([interview("s1", [question("q1", "事件循环是什么？")])], [
        coverage(["u0001"], "NON_QUESTION", reason="场次标题"),
        coverage(["u0002"], "INTERVIEW_QUESTION", ["s1/q1"]),
    ])


def test_mismatched_ledger_feedback_identifies_the_actual_unit_and_question():
    source = "# 面试记录\n事件循环是什么？\n另一个真实提问？"
    bad = single_source_payload()
    bad["source_coverage"].append(coverage(["u0003"], "INTERVIEW_QUESTION", ["s1/q1"]))
    fixed = deepcopy(bad)
    fixed["interviews"][0]["questions"].append(question("q2", "另一个真实提问？"))
    fixed["source_coverage"][-1]["question_refs"] = ["s1/q2"]
    client = Client([bad, fixed])
    result = extractor(client).extract(text=source, revision_id="immutable")
    feedback = client.calls[1]["messages"][-1]["content"]
    assert "u0003" in feedback and "另一个真实提问？" in feedback
    assert "s1/q1" in feedback and "事件循环是什么？" in feedback
    assert len(result.interviews[0].questions) == 2 and len(client.calls) == 2


def test_summary_feedback_names_the_question_and_requires_consistent_ledger_repair():
    source = "# 面试记录\n项目深挖\n事件循环是什么？"
    bad = payload([interview("s1", [question("summary", "项目深挖"), question("q1", "事件循环是什么？")])], [
        coverage(["u0001"], "NON_QUESTION"), coverage(["u0002"], "INTERVIEW_QUESTION", ["s1/summary"]),
        coverage(["u0003"], "INTERVIEW_QUESTION", ["s1/q1"])])
    fixed = deepcopy(bad)
    fixed["interviews"][0]["questions"] = fixed["interviews"][0]["questions"][1:]
    fixed["source_coverage"][1] = coverage(["u0002"], "NON_QUESTION")
    client = Client([bad, fixed])
    result = extractor(client).extract(text=source, revision_id="immutable")
    feedback = client.calls[1]["messages"][-1]["content"]
    assert "s1/summary" in feedback and "项目深挖" in feedback and "NON_QUESTION" in feedback
    assert len(result.interviews[0].questions) == 1 and len(client.calls) == 2


def test_v6_repairs_prefix_only_output_using_all_source_units_without_expected_counts():
    source = "# 面试记录\n第一轮\n事件循环是什么？\n第二轮\n如何判断进程退出？"
    short = payload([interview("s1", [question("q1", "事件循环是什么？")])], [
        coverage(["u0001", "u0002"], "NON_QUESTION"),
        coverage(["u0003"], "INTERVIEW_QUESTION", ["s1/q1"]),
    ])
    complete = payload([interview("s1", [question("q1", "事件循环是什么？")]),
                        interview("s2", [question("q1", "如何判断进程退出？")])], [
        *short["source_coverage"], coverage(["u0004"], "NON_QUESTION"),
        coverage(["u0005"], "INTERVIEW_QUESTION", ["s2/q1"]),
    ])
    client = Client([short, complete]); logs = []
    result = extractor(client, on_call=logs.append).extract(text=source, revision_id="immutable")
    assert len(result.interviews) == 2
    assert [c["status"] for c in logs] == ["FAILED", "SUCCEEDED"]
    assert "EXTRACTION_SOURCE_COVERAGE_MISSING" in logs[0]["error_detail"]
    assert client.calls[0]["messages"][1]["content"] == client.calls[1]["messages"][1]["content"] == source
    assert "u0005" in client.calls[1]["messages"][-1]["content"]
    assert logs[1]["source_coverage_audit"]["classified_units"] == 5
    assert logs[1]["source_coverage_audit"]["semantic_completeness"] == "not_proven_by_structural_ledger"


def test_mixed_source_can_exclude_a_tutorial_without_forcing_extra_questions():
    source = "# 一次面试与备考笔记\n事件循环是什么？\n以下是教程\n1.线程练习\n2.答案中的问题\n3.资料目录"
    draft = payload([interview("s1", [question("q1", "事件循环是什么？")])], [
        coverage(["u0001"], "NON_QUESTION"), coverage(["u0002"], "INTERVIEW_QUESTION", ["s1/q1"]),
        coverage(["u0003", "u0004", "u0005", "u0006"], "NON_QUESTION", reason="明确附录教程与答案，不属于面试事件"),
    ], document_kind="MIXED")
    client = Client([draft])
    result = extractor(client).extract(text=source, revision_id="immutable")
    assert len(result.interviews[0].questions) == 1 and len(client.calls) == 1


def test_body_and_ocr_same_event_can_reference_one_primary_question():
    source = "# 面试记录\n事件循环是什么？\n## 图片内容（OCR）\n事件循环是什么？"
    draft = payload([interview("s1", [question("q1", "事件循环是什么？", 1)])], [
        coverage(["u0001", "u0003"], "NON_QUESTION"),
        coverage(["u0002"], "DUPLICATE_QUESTION", ["s1/q1"], "正文和截图同一事件重复"),
        coverage(["u0004"], "INTERVIEW_QUESTION", ["s1/q1"]),
    ])
    result = extractor(Client([draft])).extract(text=source, revision_id="immutable")
    assert len(result.interviews[0].questions) == 1
    assert result.interviews[0].questions[0].source_spans[0].start_line == 4


def test_same_text_in_distinct_rounds_remains_two_grounded_occurrences():
    source = "# 面试记录\n第一轮\n事件循环是什么？\n第二轮\n事件循环是什么？"
    draft = payload([interview("s1", [question("q1", "事件循环是什么？", 0)]),
                     interview("s2", [question("q1", "事件循环是什么？", 1)])], [
        coverage(["u0001", "u0002", "u0004"], "NON_QUESTION"),
        coverage(["u0003"], "INTERVIEW_QUESTION", ["s1/q1"]),
        coverage(["u0005"], "INTERVIEW_QUESTION", ["s2/q1"]),
    ])
    result = extractor(Client([draft])).extract(text=source, revision_id="immutable")
    assert [s.questions[0].source_spans[0].start_line for s in result.interviews] == [3, 5]


def test_continuation_constraints_must_be_in_the_referenced_grounded_question():
    source = "# 面试记录\n设计一个进程通信接口。\n要求不创建临时文件，并支持超时。"
    quote = "设计一个进程通信接口。\n要求不创建临时文件，并支持超时。"
    partial = payload([interview("s1", [question("q1", "设计一个进程通信接口。")])], [
        coverage(["u0001"], "NON_QUESTION"),
        coverage(["u0002", "u0003"], "INTERVIEW_QUESTION", ["s1/q1"]),
    ])
    complete = deepcopy(partial)
    complete["interviews"][0]["questions"][0].update(raw_quote=quote, normalized_question=quote)
    client = Client([partial, complete]); logs = []
    result = extractor(client, on_call=logs.append).extract(text=source, revision_id="immutable")
    assert result.interviews[0].questions[0].raw_question == quote
    assert "EXTRACTION_SOURCE_COVERAGE_UNGROUNDED" in logs[0]["error_detail"]
    assert result.interviews[0].questions[0].source_spans[0].end_line == 3


def test_phase_summary_cannot_become_a_question_by_normalization():
    source = "# 面试记录\n项目深挖\n事件循环是什么？"
    bad = payload([interview("s1", [question("q1", "项目深挖"), question("q2", "事件循环是什么？")])], [
        coverage(["u0001"], "NON_QUESTION"), coverage(["u0002"], "INTERVIEW_QUESTION", ["s1/q1"]),
        coverage(["u0003"], "INTERVIEW_QUESTION", ["s1/q2"]),
    ])
    bad["interviews"][0]["questions"][0]["normalized_question"] = "请介绍你的项目"
    corrected = payload([interview("s1", [question("q2", "事件循环是什么？")])], [
        coverage(["u0001", "u0002"], "NON_QUESTION", reason="标题与阶段概述"),
        coverage(["u0003"], "INTERVIEW_QUESTION", ["s1/q2"]),
    ])
    client = Client([bad, corrected]); logs = []
    result = extractor(client, on_call=logs.append).extract(text=source, revision_id="immutable")
    assert [q.raw_question for q in result.interviews[0].questions] == ["事件循环是什么？"]
    assert "EXTRACTION_NON_TASK_SUMMARY" in logs[0]["error_detail"]


@pytest.mark.parametrize("mutation, code", [
    (lambda d: d["source_coverage"].append(deepcopy(d["source_coverage"][0])), "DUPLICATED"),
    (lambda d: d["source_coverage"][0].update(unit_ids=["u9999"]), "UNKNOWN_UNIT"),
    (lambda d: d["source_coverage"][1].update(question_refs=["s1/absent"]), "QUESTION_REFS"),
    (lambda d: d["source_coverage"][0].update(question_refs=["s1/q1"]), "NONQUESTION_REFS"),
    (lambda d: d["source_coverage"][1].update(classification="DUPLICATE_QUESTION"), "UNREFERENCED_QUESTION"),
])
def test_fabricated_or_contradictory_coverage_never_qualifies_as_success(mutation, code):
    draft = single_source_payload(); mutation(draft)
    client = Client([draft]); logs = []
    with pytest.raises(ValueError, match=code):
        extractor(client, max_attempts=1, on_call=logs.append).extract(
            text="# 面试记录\n事件循环是什么？", revision_id="immutable")
    assert len(client.calls) == 1 and logs[0]["status"] == "FAILED"


def test_repairs_stay_in_retry_budget_and_account_for_the_extra_catalog_input():
    draft = single_source_payload(); draft["source_coverage"].pop()
    class Budget:
        def __init__(self): self.estimates, self.usage = [], []
        def before_call(self, *, estimated_input_tokens): self.estimates.append(estimated_input_tokens)
        def after_call(self, *, input_tokens, output_tokens): self.usage.append((input_tokens, output_tokens))
    budget = Budget(); client = Client([draft, draft]); logs = []
    model = extractor(client, max_attempts=2, budget=budget, on_call=logs.append)
    with pytest.raises(ValueError, match="COVERAGE_MISSING"):
        model.extract(text="# 面试记录\n事件循环是什么？", revision_id="immutable")
    assert len(client.calls) == len(budget.usage) == len(logs) == 2
    assert budget.estimates[1] > budget.estimates[0] > len(model.prompt) // 2
    assert [c["retry_count"] for c in logs] == [0, 1]


@pytest.mark.parametrize("finish_reason", [None, "length", "content_filter"])
def test_nonstream_v6_requires_an_explicit_complete_response(finish_reason):
    client = Client([single_source_payload()], [finish_reason]); logs = []
    with pytest.raises(ValueError, match="MODEL_OUTPUT_TRUNCATED|MODEL_OUTPUT_INCOMPLETE"):
        extractor(client, max_attempts=1, on_call=logs.append).extract(
            text="# 面试记录\n事件循环是什么？", revision_id="immutable")
    assert logs[0]["status"] == "FAILED"


def test_v6_is_opt_in_closed_topics_with_a_distinct_stage_cache_identity():
    client = Client([])
    legacy = OpenAICompatibleExtractor(client=client, model="fixture")
    current = extractor(client)
    assert Settings.model_fields["extraction_prompt_version"].default == "extract_question_v2"
    assert Settings(extraction_prompt_version="extract_question_v6").extraction_prompt_version == "extract_question_v6"
    assert current.topic_ids and not legacy.topic_ids
    assert "source_coverage" not in legacy.request_schema()["properties"]
    schema = current.request_schema()
    assert "source_coverage" in schema["required"]
    assert "topic_id" in schema["$defs"]["DraftQuestion"]["required"]
    assert "topic_l1" not in schema["$defs"]["DraftQuestion"]["properties"]
    assert current.cache_configuration["source_coverage_contract"] == "nonblank_line_ledger_v1"
    assert "source_coverage_contract" not in legacy.cache_configuration
    assert extraction_config_hash(current, load_taxonomy()) != extraction_config_hash(legacy, load_taxonomy())


def test_source_units_keep_actual_offsets_blank_lines_and_carriage_returns():
    source = "# 标题\r\n\r\n  一条记录\r\n另一条记录"
    units = _source_units(source)
    assert [unit["start_line"] for unit in units] == [1, 3, 4]
    assert [unit["unit_id"] for unit in units] == ["u0001", "u0002", "u0003"]
    assert all(source[u["start_char"]:u["end_char"]] == u["text"] for u in units)


def test_oversized_input_is_rejected_without_truncation_or_hidden_model_calls():
    client = Client([])
    with pytest.raises(ValueError, match="INPUT_TOO_LARGE"):
        extractor(client).extract(text="x" * 20001, revision_id="immutable")
    assert client.calls == []
