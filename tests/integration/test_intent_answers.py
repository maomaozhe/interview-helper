"""Intent tools return whole-corpus counts and durable, grounded answers."""
from copy import deepcopy

import pytest
from sqlalchemy import select

from interview_intelligence.agent.query_contract import QueryRequest, QuerySpec
from interview_intelligence.agent.query_service import QueryService
from interview_intelligence.domain.models import (
    CanonicalQuestion, Interview, QuestionOccurrence,
)
from test_analytics import seed_corpus
from test_conversation_quality import app_fixture
from test_query_agent import ranked_corpus


def answer_plan(text, *, basis="GENERAL_KNOWLEDGE", ids=(), kind="EXPLAIN"):
    return QuerySpec(action="ANSWER", answer_text=text, answer_kind=kind,
                     answer_basis=basis, question_ids=list(ids))


def test_count_uses_all_matching_occurrences_independently_of_page_size():
    db, _ = ranked_corpus()
    with db.session() as session, session.begin():
        interview = session.scalar(select(Interview).where(Interview.round == "FIRST"))
        canonical = CanonicalQuestion(canonical_text="PostgreSQL 如何实现 MVCC？",
            primary_topic_id="database.transaction", taxonomy_version="v1", question_type="PRINCIPLE")
        session.add(canonical)
        session.flush()
        session.add(QuestionOccurrence(interview_id=interview.id, canonical_question_id=canonical.id,
            raw_question=canonical.canonical_text, normalized_question=canonical.canonical_text,
            question_order=10000, source_spans=[{"quote": canonical.canonical_text}],
            topic_id=canonical.primary_topic_id, taxonomy_version="v1", question_type="PRINCIPLE", confidence=1))

    service = QueryService(db, signing_key="test")
    expected = {
        "canonical_questions": 58, "occurrences": 1614,
        "interviews": 3, "source_documents": 3, "known_companies": 2,
    }
    global_run = service.begin(QueryRequest(message="总共有多少道题", request_id="all-count"))
    global_result = service.execute(global_run, "get_question_count",
        QuerySpec(action="COUNT", page_size=1, top_n=None))
    assert global_result["intent"] == "COUNT"
    assert global_result["facts"]["meta"]["counts"] == expected
    service.finish(global_run, provider="fixture")

    redis_run = service.begin(QueryRequest(message="Redis 有多少道题", request_id="redis-count",
        conversation_id=global_run.conversation_id, expected_version=1))
    redis_result = service.execute(redis_run, "get_question_count",
        QuerySpec(action="COUNT", filters={"topic_l1": "Redis"}, page_size=1, top_n=None))
    assert redis_result["facts"]["meta"]["counts"] == {
        **expected, "canonical_questions": 57, "occurrences": 1613,
    }
    service.finish(redis_run, provider="fixture")

    # A later global count has an independent SQL scope; the Redis plan and
    # the earlier one-row page size cannot silently constrain the total.
    global_again = service.begin(QueryRequest(message="整个题库呢", request_id="global-count-again",
        conversation_id=global_run.conversation_id, expected_version=2))
    result = service.execute(global_again, "get_question_count", QuerySpec(action="COUNT"))
    assert result["facts"]["meta"]["counts"] == expected
    service.finish(global_again, provider="fixture")


def test_details_then_answer_exposes_source_evidence_and_preserves_next_page():
    db, expected = ranked_corpus()
    service = QueryService(db, signing_key="test")
    listing = service.begin(QueryRequest(message="前 40 道算法题", request_id="answer-page"))
    first = service.execute(listing, "list_questions",
        QuerySpec(action="LIST", filters={"coding_focus": "ALGORITHM"}, top_n=40, page_size=20))
    service.finish(listing, provider="fixture")
    first_ids = [row["canonical_question_id"] for row in first["facts"]["data"]]
    saved_page = deepcopy({key: listing.state[key] for key in ("current_page_ids", "list_request", "filters")})

    run = service.begin(QueryRequest(message="解释第一题并给解题思路", request_id="explain-first",
        conversation_id=listing.conversation_id, expected_version=1))
    details = service.execute(run, "get_question_details", QuerySpec(action="DETAILS",
        filters={"coding_focus": "ALGORITHM"}, question_ids=[first_ids[0]], final=False))
    assert details["facts"]["data"][0]["canonical_question_id"] == first_ids[0]
    assert details["facts"]["data"][0]["sources"]
    context = service.model_context(run)
    evidence = context["completed_tools"][-1]
    assert evidence["intent"] == "DETAILS"
    assert evidence["data"][0]["canonical_question_id"] == first_ids[0]
    assert evidence["data"][0]["canonical_text"] == details["facts"]["data"][0]["canonical_text"]
    assert evidence["data"][0]["sources"][0]["revision_id"] == details["facts"]["data"][0]["sources"][0]["revision_id"]

    text = "先确认输入输出和边界条件，再选择数据结构，最后分析时间与空间复杂度。"
    answer = service.execute(run, "answer_question", answer_plan(text, basis="MIXED", ids=[first_ids[0]], kind="SOLVE"))
    assert answer["intent"] == "ANSWER" and answer["answer"] == text
    assert {key: run.state[key] for key in saved_page} == saved_page
    finished = service.finish(run, provider="fixture")
    assert [item["intent"] for item in finished["facts"]["components"]] == ["DETAILS"]

    next_run = service.begin(QueryRequest(message="下一页", request_id="next-after-answer",
        conversation_id=listing.conversation_id, expected_version=2))
    next_result = service.execute(next_run, "list_questions", QuerySpec(action="NEXT"))
    assert [row["canonical_question_id"] for row in next_result["facts"]["data"]] == [i for i, _ in expected[20:40]]
    assert next_result["facts"]["meta"]["pagination"]["next_cursor"] is None
    service.finish(next_run, provider="fixture")


def test_general_knowledge_answer_needs_no_retrieval_and_replays_from_history(tmp_path):
    text = "Redis 使用内存存储，并以事件循环处理网络请求；主线程串行执行大多数命令。"

    class Retriever:
        def retrieve(self, *args, **kwargs):
            raise AssertionError("A general explanation must not require corpus retrieval")

    class Planner:
        calls = 0

        def plan(self, run, context):
            self.calls += 1
            return answer_plan(text)

    planner = Planner()
    client, _, _, _ = app_fixture(tmp_path, planner, Retriever())
    body = {"message": "Redis 为什么快，解释一下原理", "request_id": "general-explanation"}
    response = client.post("/api/query", json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["meta"]["answer"] == text
    assert result["data"] == []
    assert result["meta"]["planning"]["spec"]["answer_basis"] == "GENERAL_KNOWLEDGE"
    assert client.post("/api/query", json=body).json() == result
    assert planner.calls == 1
    history = client.get(f"/api/conversations/{result['meta']['conversation_id']}").json()["data"]
    assert len(history["turns"]) == 1
    assert history["turns"][0]["result"]["intent"] == "ANSWER"
    assert history["turns"][0]["result"]["answer"] == text
    assert history["state"]["last_response"]["message"] == body["message"]
    assert history["state"]["last_response"]["answer"] == text


def test_restarted_service_restores_previous_answer_for_follow_up():
    db, _, _ = seed_corpus()
    service = QueryService(db, signing_key="test")
    first = service.begin(QueryRequest(message="为什么 Redis 快", request_id="restore-explanation"))
    text = "主要原因包括内存存储、事件驱动网络处理和适合操作的数据结构。"
    service.execute(first, "answer_question", answer_plan(text))
    service.finish(first, provider="fixture")

    restarted = QueryService(db, signing_key="test")
    follow_up = restarted.begin(QueryRequest(message="第二点展开讲讲", request_id="expand-second",
        conversation_id=first.conversation_id, expected_version=1))
    previous = restarted.model_context(follow_up)["session"]["last_response"]
    assert previous["message"] == "为什么 Redis 快"
    assert previous["intent"] == "ANSWER"
    assert previous["answer"] == text
    assert previous["provenance"] == "previous_assistant_response"
    restarted.execute(follow_up, "answer_question", answer_plan("事件循环通过 I/O 多路复用监听连接就绪事件。"))
    restarted.finish(follow_up, provider="fixture")


def test_answer_rejects_question_id_outside_the_current_page():
    db, _, _ = seed_corpus()
    service = QueryService(db, signing_key="test")
    listing = service.begin(QueryRequest(message="列出第一题", request_id="bound-page"))
    service.execute(listing, "list_questions", QuerySpec(action="LIST", page_size=1))
    service.finish(listing, provider="fixture")
    with db.session() as session:
        other_id = session.scalar(select(CanonicalQuestion.id).where(
            CanonicalQuestion.id.not_in(listing.state["current_page_ids"])))
    assert other_id
    run = service.begin(QueryRequest(message="解释第一题", request_id="foreign-reference",
        conversation_id=listing.conversation_id, expected_version=1))
    with pytest.raises(ValueError, match="QUESTION_NOT_IN_SESSION"):
        service.execute(run, "answer_question", answer_plan("这个题目涉及 Redis 持久化。", basis="CORPUS", ids=[other_id]))
    service.fail(run)


@pytest.mark.parametrize("basis", ["CORPUS", "MIXED"])
def test_corpus_answer_without_current_evidence_is_rejected(basis):
    db, _, _ = seed_corpus()
    service = QueryService(db, signing_key="test")
    run = service.begin(QueryRequest(message="根据面经总结 Redis 的特点", request_id="no-evidence"))
    with pytest.raises(ValueError, match="QUERY_ANSWER_EVIDENCE_REQUIRED"):
        service.execute(run, "answer_question", answer_plan("这些面经普遍关注 Redis 性能。", basis=basis))
    service.fail(run)


def test_general_knowledge_cannot_claim_question_references_as_corpus_evidence():
    db, _, _ = seed_corpus()
    service = QueryService(db, signing_key="test")
    listing = service.begin(QueryRequest(message="题目列表", request_id="basis-page"))
    service.execute(listing, "list_questions", QuerySpec(action="LIST", page_size=1))
    service.finish(listing, provider="fixture")
    run = service.begin(QueryRequest(message="解释第一题", request_id="basis-conflict",
        conversation_id=listing.conversation_id, expected_version=1))
    with pytest.raises(ValueError, match="QUERY_ANSWER_BASIS_CONFLICT"):
        service.execute(run, "answer_question", answer_plan("Redis 的常见优化包括内存存储。",
            ids=listing.state["current_page_ids"]))
    service.fail(run)


def test_previous_count_receipt_does_not_substitute_for_current_run_evidence():
    db, _, _ = seed_corpus()
    service = QueryService(db, signing_key="test")
    counted = service.begin(QueryRequest(message="总共有多少道题", request_id="old-count"))
    service.execute(counted, "get_question_count", QuerySpec(action="COUNT"))
    service.finish(counted, provider="fixture")
    run = service.begin(QueryRequest(message="现在呢", request_id="count-again-without-tool",
        conversation_id=counted.conversation_id, expected_version=1))
    with pytest.raises(ValueError, match="QUERY_ANSWER_EVIDENCE_REQUIRED"):
        service.execute(run, "answer_question", answer_plan("现在仍有两道题。", basis="CORPUS"))
    service.fail(run)


def test_count_company_and_round_filters_apply_to_the_same_occurrence():
    db, _, _ = seed_corpus()
    service = QueryService(db, signing_key="test")
    run = service.begin(QueryRequest(message="字节二面有多少道题", request_id="count-filter-scope"))
    result = service.execute(run, "get_question_count",
        QuerySpec(action="COUNT", filters={"company": "字节", "round": "SECOND"}))
    assert result["facts"]["meta"]["counts"] == {
        "canonical_questions": 1, "occurrences": 1, "interviews": 1,
        "source_documents": 1, "known_companies": 1,
    }
    service.finish(run, provider="fixture")


def test_count_scope_has_its_own_durable_filters_and_preserves_existing_listing_cursor():
    db, fast_id, persistence_id = seed_corpus()
    service = QueryService(db, signing_key="test")
    listing = service.begin(QueryRequest(message="字节的题目", request_id="count-independent-list"))
    service.execute(listing, "list_questions", QuerySpec(action="LIST", filters={"company": "字节"}, page_size=1))
    service.finish(listing, provider="fixture")
    old_listing = deepcopy(listing.state["list_request"])
    old_ids = listing.state["current_page_ids"][:]
    assert old_listing["cursor"]

    counted = service.begin(QueryRequest(message="腾讯共有多少题", request_id="count-independent-first",
        conversation_id=listing.conversation_id, expected_version=1))
    service.execute(counted, "get_question_count", QuerySpec(action="COUNT", filters={"company": "腾讯"}))
    service.finish(counted, provider="fixture")
    assert counted.state["last_count"]["filters"]["company"] == "腾讯"
    assert counted.state["last_count"]["counts"]["canonical_questions"] == 1
    assert counted.state["filters"]["company"] == "字节"
    assert counted.state["list_request"] == old_listing
    assert counted.state["current_page_ids"] == old_ids

    restarted = QueryService(db, signing_key="test")
    follow_up = restarted.begin(QueryRequest(message="那 Redis 呢", request_id="count-independent-follow-up",
        conversation_id=listing.conversation_id, expected_version=2))
    context = restarted.model_context(follow_up)
    count_scope = context["session"]["last_count"]
    assert count_scope["filters"]["company"] == "腾讯"
    assert count_scope["message"] == "腾讯共有多少题"
    assert context["session"]["filters"]["company"] == "字节"
    restarted.execute(follow_up, "get_question_count", QuerySpec(action="COUNT",
        filters={**count_scope["filters"], "topic_l1": "Redis"}))
    restarted.finish(follow_up, provider="fixture")
    assert follow_up.state["last_count"]["filters"]["company"] == "腾讯"
    assert follow_up.state["last_count"]["filters"]["topic_l1"] == "Redis"
    assert follow_up.state["list_request"] == old_listing

    next_page = restarted.begin(QueryRequest(message="下一页", request_id="count-independent-next",
        conversation_id=listing.conversation_id, expected_version=3))
    result = restarted.execute(next_page, "list_questions", QuerySpec(action="NEXT"))
    assert {row["canonical_question_id"] for row in result["facts"]["data"]} == {fast_id, persistence_id} - set(old_ids)
    assert result["facts"]["meta"]["applied_filters"]["company"] == "字节"
    restarted.finish(next_page, provider="fixture")


@pytest.mark.parametrize("company,expected_total", [(None, 2), ("不存在的公司", 0)])
def test_current_list_pagination_total_can_ground_answer_without_ids_including_empty_results(company, expected_total):
    db, _, _ = seed_corpus()
    service = QueryService(db, signing_key="test")
    run = service.begin(QueryRequest(message="列题并说明共有多少道", request_id=f"list-total-{expected_total}"))
    filters = {"company": company} if company else {}
    listed = service.execute(run, "list_questions", QuerySpec(action="LIST", filters=filters,
        page_size=1, final=False))
    assert listed["facts"]["meta"]["pagination"]["result_total"] == expected_total
    assert len(listed["facts"]["data"]) == min(expected_total, 1)
    evidence = service.model_context(run)["completed_tools"][-1]
    assert evidence["meta"]["pagination"]["result_total"] == expected_total
    plan = answer_plan(f"当前筛选共有 {expected_total} 道去重题目。", basis="CORPUS")
    plan = plan.model_copy(update={"filters": QuerySpec(action="LIST", filters=filters).filters})
    service.execute(run, "answer_question", plan)
    finished = service.finish(run, provider="fixture")
    assert finished["intent"] == "ANSWER"
    assert finished["planning"]["spec"]["question_ids"] == []
    assert finished["facts"]["components"][0]["facts"]["meta"]["pagination"]["result_total"] == expected_total


@pytest.mark.parametrize("action,tool", [("COUNT", "get_question_count"), ("STATS", "get_question_stats")])
def test_same_run_count_or_stats_can_ground_an_answer_without_question_references(action, tool):
    db, _, _ = seed_corpus()
    service = QueryService(db, signing_key="test")
    run = service.begin(QueryRequest(message="统计题库数量并解释统计口径", request_id=f"grounded-{action}"))
    kwargs = {"group_by": "company"} if action == "STATS" else {}
    service.execute(run, tool, QuerySpec(action=action, final=False, **kwargs))
    evidence = service.model_context(run)["completed_tools"][-1]
    assert evidence["intent"] == action
    if action == "COUNT":
        assert evidence["meta"]["counts"]["canonical_questions"] == 2
    else:
        assert evidence["data"]
    text = "题目去重后的数量和面试中的实际提问次数是两个不同口径。"
    service.execute(run, "answer_question", answer_plan(text, basis="CORPUS"))
    result = service.finish(run, provider="fixture")
    assert result["intent"] == "ANSWER" and result["answer"] == text
    assert result["facts"]["components"][0]["intent"] == action


def test_stats_can_group_a_single_topic_into_distinct_second_level_categories():
    db, _, _ = seed_corpus()
    service = QueryService(db, signing_key="test")
    run = service.begin(QueryRequest(message="Redis 各小类分别问了多少次", request_id="redis-subtopic-stats"))
    result = service.execute(run, "get_question_stats", QuerySpec(action="STATS",
        filters={"topic_l1": "Redis"}, group_by="topic", topic_level="L2"))
    assert {row["key"]: row["occurrence_count"] for row in result["facts"]["data"]} == {
        "redis.performance": 2, "redis.persistence": 1,
    }
    assert result["facts"]["meta"]["pagination"]["result_total"] == 2
    assert result["planning"]["spec"]["topic_level"] == "L2"
    assert run.state["list_request"]["topic_level"] == "L2"
    service.finish(run, provider="fixture")
