import pytest

from interview_intelligence.agent.task_context import compile_task_context


def span(text, quote):
    start = text.index(quote)
    return {"start_char": start, "end_char": start + len(quote), "quote": quote}


def test_inline_verbal_qualifier_and_post_quote_evidence_survive_context_compilation():
    text = "# 一面\n15.讲思路，不用写代码：数组过半数字，答完即可，不需手写\n16.下一题"
    result = compile_task_context(text, [span(text, "数组过半数字")])
    assert "讲思路，不用写代码" in result["context_before"]
    assert "不需手写" in result["context_after"]


def test_distant_task_section_survives_bounding_without_invented_context():
    text = "# 一面\n手撕：\n" + "很长的原文解释" * 1000 + "\n目标问题\n后续问题"
    result = compile_task_context(text, [span(text, "目标问题")])
    assert result["context_before"].startswith("手撕：")
    assert len(result["context_before"]) <= 1000
    assert len(result["context_after"]) <= 400
    assert "后续问题" in result["context_after"]


def test_context_refuses_stale_or_fabricated_span():
    with pytest.raises(ValueError, match="SOURCE_MISMATCH"):
        compile_task_context("真实原文", [{"start_char": 0, "end_char": 4, "quote": "虚构原文"}])
