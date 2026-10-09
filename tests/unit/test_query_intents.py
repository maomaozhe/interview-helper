"""Intent actions preserve query defaults and require bounded terminal answers."""
from pathlib import Path
import tomllib

import pytest

from interview_intelligence.agent.query_contract import (
    QUERY_AGENT_VERSION, TOOL_ACTIONS, QuerySpec, query_model_schema, validate_model_plan,
)
from interview_intelligence.config import Settings
from interview_intelligence.contracts import FilterSpec


def model_plan(**values):
    return {
        "action": "ANSWER", "filters": FilterSpec().model_dump(mode="json"),
        "sort": "frequency", "top_n": None, "page_size": 20,
        "answer_text": "以下为参考解答（模型知识）：事务隔离用于控制并发事务之间的可见性。",
        **values,
    }


def test_v13_is_default_and_ships_without_removing_previous_prompts():
    root = Path(__file__).parents[2]
    manifest = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    files = manifest["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert QUERY_AGENT_VERSION == Settings().query_prompt_version == "query_agent_v13"
    for version in ("query_agent_v9", "query_agent_v10", "query_agent_v11", "query_agent_v12", "query_agent_v13"):
        assert files[f"prompts/{version}.md"].endswith(f"/{version}.md")
        assert Settings(query_prompt_version=version).query_prompt_version == version


def test_v12_distinguishes_finding_counting_and_answering_before_retrieval_rules():
    root = Path(__file__).parents[2]
    prompt = (root / "prompts/query_agent_v12.md").read_text(encoding="utf-8")
    introduction = prompt[:prompt.index("检索、澄清与重检")]
    assert "get_question_count" in introduction and "answer_question" in introduction
    assert "一共有多少条数据？" in introduction and "继承当前讨论范围" in introduction
    assert "这些有多少？" in introduction and "conversation_focus" in introduction
    assert "last_count.filters/review_statuses" in introduction and "不能永久继承旧Redis计数" in introduction
    assert "AFTER_TOP_N" in introduction and "pagination.result_total" in introduction
    assert "final=false" in introduction and "ANSWER final=true" in introduction
    assert "模型知识" in introduction and "没有核验的标准答案" in introduction
    assert "完整技术问句默认解答" in prompt and "电商客服助手如何设计" in prompt
    assert "requery_origin" in prompt and "lexical_facets" in prompt


@pytest.mark.parametrize("version", ["query_agent_v11", "query_agent_v12", "query_agent_v13"])
def test_intent_prompt_stays_within_the_multistep_model_budget(version):
    root = Path(__file__).parents[2]
    prompt = (root / f"prompts/{version}.md").read_bytes()
    # The host reserves UTF-8 request bytes conservatively. Leave room for
    # dynamic tool schemas, evidence and a second/third planning request.
    assert len(prompt) <= 14_000


def test_v12_scope_corrections_preserve_counting_and_require_explicit_global_scope():
    root = Path(__file__).parents[2]
    prompt = (root / "prompts/query_agent_v12.md").read_text(encoding="utf-8")
    assert "仅明确“全库/整个题库/所有分类”" in prompt
    assert "没有可恢复的讨论范围才统计全库" in prompt
    assert "上一轮询问数量则继续 COUNT" in prompt
    assert "我说的是redis的高频率问题啊，这个会话不是才说过" in prompt
    assert "不能返回20道题替代计数" in prompt
    assert "仅 conversation_focus.intent=COUNT 时才用对应 last_count.filters/review_statuses" in prompt
    assert "从 LIST 转 COUNT 使用 focus.filters" in prompt
    assert "旧 ANSWER 不代表旧 list_request 是回答范围" in prompt
    assert "即便上一COUNT已经正确回答Redis数量" in prompt
    assert "仍调用 get_question_count" in prompt
    assert "不能因已答正确改为 LIST" in prompt
    assert "仍保留本轮 explicit_filters" in prompt
    assert "默认全库：清除上一轮" not in prompt


def test_v12_separates_implicit_paging_from_an_explicit_top_n_limit():
    root = Path(__file__).parents[2]
    prompt = (root / "prompts/query_agent_v12.md").read_text(encoding="utf-8")
    assert "page_size 只是单页大小，不等于 top_n 或总数" in prompt
    assert "未明确要求N道时 top_n=null，不得填默认20" in prompt
    assert "同话题保留用户明确N，新话题清除" in prompt
    assert "Redis高频问题有哪些" in prompt and "Redis高频前20题" in prompt


def test_count_is_a_read_action_and_can_continue_to_an_answer():
    assert TOOL_ACTIONS["get_question_count"] == {"COUNT"}
    assert QuerySpec(action="COUNT").final is True
    continued = QuerySpec(action="COUNT", final=False, filters={"topic_l1": "Redis"})
    assert continued.final is False and continued.filters.topic_l1 == "Redis"
    assert continued.answer_text is None


def test_topic_statistics_support_both_levels_without_breaking_existing_plans():
    assert QuerySpec(action="STATS", group_by="topic").topic_level == "L1"
    plan = validate_model_plan(model_plan(action="STATS", answer_text=None,
        group_by="topic", topic_level="L2", filters=FilterSpec(topic_l1="Redis").model_dump(mode="json")))
    assert plan.topic_level == "L2" and plan.filters.topic_l1 == "Redis"
    field = query_model_schema()["properties"]["topic_level"]
    assert field["enum"] == ["L1", "L2"] and field["default"] == "L1"
    with pytest.raises(ValueError):
        QuerySpec(action="STATS", group_by="topic", topic_level="L3")


def test_default_query_budget_allows_three_planning_steps_with_bounded_reservations():
    settings = Settings()
    assert settings.query_max_tokens == 96000
    assert settings.query_max_model_calls == 4
    assert Settings(query_max_tokens=128000).query_max_tokens == 128000
    with pytest.raises(ValueError):
        Settings(query_max_tokens=128001)


@pytest.mark.parametrize("top_n", [1, 40, 1000])
def test_count_rejects_top_n_instead_of_silently_counting_the_entire_scope(top_n):
    with pytest.raises(ValueError, match="count requires top_n=null.*pagination.result_total"):
        QuerySpec(action="COUNT", top_n=top_n)
    with pytest.raises(ValueError, match="count requires top_n=null"):
        validate_model_plan(model_plan(action="COUNT", answer_text=None, top_n=top_n))


def test_answer_defaults_and_reference_limits_preserve_other_actions():
    assert TOOL_ACTIONS["answer_question"] == {"ANSWER"}
    answer = QuerySpec(action="ANSWER", answer_text="参考解答。", question_ids=[str(i) for i in range(10)])
    assert answer.final is True and answer.scope == "current_page"
    assert answer.answer_kind == "EXPLAIN" and answer.answer_basis == "GENERAL_KNOWLEDGE"
    assert QuerySpec(action="LIST").answer_text is None
    assert len(QuerySpec(action="REVIEW_STATE", question_ids=[str(i) for i in range(100)]).question_ids) == 100


@pytest.mark.parametrize("values", [
    {}, {"answer_text": None}, {"answer_text": ""}, {"answer_text": " \n\t "},
    {"answer_text": "x" * 12001}, {"answer_text": "answer", "final": False},
    {"answer_text": "answer", "scope": "full_scope"},
    {"answer_text": "answer", "question_ids": [str(i) for i in range(11)]},
    {"answer_text": "answer", "answer_kind": "SEARCH"},
    {"answer_text": "answer", "answer_basis": "OFFICIAL_ANSWER"},
])
def test_answer_rejects_missing_empty_unbounded_or_nonterminal_content(values):
    with pytest.raises(ValueError):
        QuerySpec(action="ANSWER", **values)


@pytest.mark.parametrize("kind", ["EXPLAIN", "COMPARE", "SOLVE", "STUDY_PLAN", "CHAT"])
@pytest.mark.parametrize("basis", ["GENERAL_KNOWLEDGE", "CORPUS", "MIXED"])
def test_answer_accepts_declared_content_and_provenance_kinds(kind, basis):
    answer = validate_model_plan(model_plan(answer_kind=kind, answer_basis=basis))
    assert answer.answer_kind == kind and answer.answer_basis == basis


def test_model_answer_requires_content_while_existing_scope_fields_stay_explicit():
    payload = model_plan()
    payload.pop("answer_text")
    with pytest.raises(ValueError, match="QUERY_PLAN_INCOMPLETE.*answer_text"):
        validate_model_plan(payload)
    payload = model_plan(filters={})
    with pytest.raises(ValueError, match="QUERY_PLAN_INCOMPLETE.*filters"):
        validate_model_plan(payload)
    with pytest.raises(ValueError, match="answer text requires ANSWER"):
        QuerySpec(action="LIST", answer_text="This must not bypass the answer action.")


def test_model_schema_exposes_answer_fields_and_filters_actions_by_host_policy():
    schema = query_model_schema({"allowed_actions": {
        "get_question_count": ["COUNT"], "answer_question": ["ANSWER", "RECORD_REVIEW"],
    }})
    assert schema["properties"]["action"]["enum"] == ["ANSWER", "COUNT"]
    assert schema["required"] == ["action", "filters", "sort", "top_n", "page_size"]
    assert set(schema["$defs"]["FilterSpec"]["required"]) == set(FilterSpec.model_fields)
    text_schema = schema["properties"]["answer_text"]
    content_schema = next(value for value in text_schema["anyOf"] if value.get("type") == "string")
    assert content_schema["minLength"] == 1 and content_schema["maxLength"] == 12000
