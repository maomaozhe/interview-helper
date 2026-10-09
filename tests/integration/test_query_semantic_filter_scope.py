"""Semantic labels cannot silently become SQL scope; trusted scopes remain exact."""
from copy import deepcopy
import hashlib

import pytest
from sqlalchemy import select

from interview_intelligence.agent.query_contract import QueryRequest, QuerySpec
from interview_intelligence.agent.query_service import SEARCH_DIMENSION_POLICY_VERSION
from interview_intelligence.domain.models import AgentConversation, AgentTurn, OccurrenceTaskAnnotation, QuestionOccurrence
from test_query_question_type_scope import fixture, search


def test_inferred_topic_pair_and_coding_focus_do_not_exclude_business_candidates():
    _, project, scenario, service = fixture()
    run = search(service, QueryRequest(message="业务场景设计题", request_id="soft-dimensions"),
                 topic_l1="Redis", topic_l2="缓存问题", coding_focus="ENGINEERING")
    assert set(service.retriever.calls[-1]["eligible"]) == {project, scenario}
    plan = run.result["planning"]["spec"]["filters"]
    assert all(plan[key] is None for key in ("topic_l1", "topic_l2", "coding_focus", "annotation_status"))
    policy = run.result["facts"]["meta"]["search_filter_policy"]
    assert policy["version"] == SEARCH_DIMENSION_POLICY_VERSION and policy["automatic_annotation_cleared"] is True
    for key, value in (("topic_l1", "Redis"), ("topic_l2", "缓存问题"), ("coding_focus", "ENGINEERING")):
        assert policy["fields"][key] == {"planned": value, "applied": None, "source": "model_intent",
                                          "decision": "model_inferred_softened"}
    assert "search_filter_scope" not in run.state


def test_model_supplied_annotation_policy_is_not_silently_cleared():
    _, _, _, service = fixture()
    run = search(service, QueryRequest(message="设计题", request_id="existing-annotation"),
                 coding_focus="ENGINEERING", annotation_status="KNOWN")
    assert run.result["planning"]["spec"]["filters"]["coding_focus"] is None
    assert run.result["planning"]["spec"]["filters"]["annotation_status"] == "KNOWN"
    assert run.result["facts"]["meta"]["search_filter_policy"]["automatic_annotation_cleared"] is False


@pytest.mark.parametrize("response_form", ["CODE", "SQL"])
def test_programming_form_keeps_coding_task_filter_hard(response_form):
    database, project, _, service = fixture()
    with database.session() as session, session.begin():
        for occurrence in session.scalars(select(QuestionOccurrence).where(QuestionOccurrence.canonical_question_id == project)):
            session.add(OccurrenceTaskAnnotation(occurrence_id=occurrence.id, response_form=response_form,
                coding_focus="ENGINEERING", classification_status="VERIFIED", producer_version="fixture"))
    run = search(service, QueryRequest(message="工程实现练习", request_id="programming-form"),
                 response_form=response_form, coding_focus="ENGINEERING", topic_l1="Redis", topic_l2="缓存问题")
    assert service.retriever.calls[-1]["eligible"] == [project]
    assert run.result["planning"]["spec"]["filters"]["coding_focus"] == "ENGINEERING"
    policy = run.result["facts"]["meta"]["search_filter_policy"]["fields"]
    assert policy["coding_focus"]["decision"] == "programming_task_preserved"
    assert policy["topic_l2"]["applied"] is None


def test_explicit_coding_focus_and_sql_inherited_focus_remain_hard_without_code_form():
    database, project, _, service = fixture()
    with database.session() as session, session.begin():
        for occurrence in session.scalars(select(QuestionOccurrence).where(QuestionOccurrence.canonical_question_id == project)):
            session.add(OccurrenceTaskAnnotation(occurrence_id=occurrence.id, response_form="DESIGN",
                coding_focus="ENGINEERING", classification_status="VERIFIED", producer_version="fixture"))
    explicit = search(service, QueryRequest(message="选中工程分类", request_id="focus-ui",
        filters={"coding_focus": "ENGINEERING"}), coding_focus="ENGINEERING")
    assert service.retriever.calls[-1]["eligible"] == [project]
    assert explicit.result["facts"]["meta"]["search_filter_policy"]["fields"]["coding_focus"]["decision"] == "explicit"
    listed = service.begin(QueryRequest(message="按工程分类列出", request_id="focus-list"))
    service.execute(listed, "list_questions", QuerySpec(action="LIST", filters={"coding_focus": "ENGINEERING"}))
    service.finish(listed, provider="fixture")
    following = search(service, QueryRequest(message="这些分类继续找", request_id="focus-list-search",
        conversation_id=listed.conversation_id), coding_focus="ENGINEERING")
    assert service.retriever.calls[-1]["eligible"] == [project]
    assert following.result["facts"]["meta"]["search_filter_policy"]["fields"]["coding_focus"]["source"] == "inherited_sql_list"


def test_explicit_topic_scope_is_preserved_in_followup_and_historical_requery():
    _, project, _, service = fixture()
    scoped = {"topic_l1": "Redis", "topic_l2": "性能优化"}
    original = search(service, QueryRequest(message="按选中范围找", request_id="topic-ui", filters=scoped), **scoped)
    assert service.retriever.calls[-1]["eligible"] == [project]
    following = search(service, QueryRequest(message="这类题", request_id="topic-inherited",
        conversation_id=original.conversation_id), **scoped)
    policy = following.result["facts"]["meta"]["search_filter_policy"]["fields"]
    assert policy["topic_l1"]["source"] == policy["topic_l2"]["source"] == "inherited_explicit_ui"
    pending = service.begin(QueryRequest(message="这类题", request_id="topic-requery", requery_of_run_id=following.id))
    context = service.model_context(pending)
    assert context["explicit_filters"] == scoped
    assert context["session"]["search_filter_scope"]["fields"]["topic_l2"]["source"] == "explicit_ui"
    with pytest.raises(ValueError, match="EXPLICIT_FILTER_CONFLICT"):
        service.execute(pending, "search_questions", QuerySpec(action="SEARCH", search_query="query"))
    service.fail(pending)
    cleared = search(service, QueryRequest(message="清除主题范围", request_id="topic-clear", requery_of_run_id=following.id,
        filters={"topic_l1": None, "topic_l2": None}))
    assert set(service.retriever.calls[-1]["eligible"]) != {project}
    assert "search_filter_scope" not in cleared.state
    chained = service.begin(QueryRequest(message="再检索", request_id="topic-clear-chain", requery_of_run_id=cleared.id))
    assert service.model_context(chained)["explicit_filters"] == {"topic_l1": None, "topic_l2": None}
    service.fail(chained)


def test_changed_topic_value_does_not_inherit_old_ui_scope():
    _, project, scenario, service = fixture()
    original = search(service, QueryRequest(message="选中主题", request_id="old-topic", filters={"topic_l1": "Redis"}), topic_l1="Redis")
    switched = search(service, QueryRequest(message="换为数据库相关", request_id="changed-topic",
        conversation_id=original.conversation_id), topic_l1="数据库")
    assert set(service.retriever.calls[-1]["eligible"]) == {project, scenario}
    assert switched.result["facts"]["meta"]["search_filter_policy"]["fields"]["topic_l1"]["decision"] == "model_inferred_softened"
    assert "search_filter_scope" not in switched.state


@pytest.mark.parametrize("action,tool", [("LIST", "list_questions"), ("STATS", "get_question_stats")])
def test_sql_topic_scope_remains_exact_and_legacy_list_can_be_inherited(action, tool):
    database, project, _, service = fixture()
    listed = service.begin(QueryRequest(message="按主题列出", request_id="sql-topic"))
    service.execute(listed, tool, QuerySpec(action=action, filters={"topic_l1": "Redis", "topic_l2": "性能优化"}))
    service.finish(listed, provider="fixture")
    assert [row["canonical_question_id"] for row in listed.result["facts"]["data"]] == [project]
    with database.session() as session, session.begin():
        conversation = session.get(AgentConversation, listed.conversation_id)
        state = deepcopy(conversation.state); state.pop("search_filter_scope"); conversation.state = state
    following = search(service, QueryRequest(message="这些主题再找", request_id="legacy-topic-followup",
        conversation_id=listed.conversation_id), topic_l1="Redis", topic_l2="性能优化")
    assert service.retriever.calls[-1]["eligible"] == [project]
    assert following.result["facts"]["meta"]["search_filter_policy"]["fields"]["topic_l2"]["source"] == "inherited_sql_list"


def test_old_bare_model_topic_state_does_not_gain_trusted_provenance():
    database, project, scenario, service = fixture()
    original = search(service, QueryRequest(message="场景题", request_id="bare-model"))
    with database.session() as session, session.begin():
        conversation = session.get(AgentConversation, original.conversation_id)
        state = deepcopy(conversation.state); state["filters"].update(topic_l1="Redis", topic_l2="缓存问题", coding_focus="ENGINEERING")
        state["last_plan"]["filters"].update(state["filters"]); conversation.state = state
    following = search(service, QueryRequest(message="继续", request_id="bare-followup", conversation_id=original.conversation_id),
                       topic_l1="Redis", topic_l2="缓存问题", coding_focus="ENGINEERING")
    assert set(service.retriever.calls[-1]["eligible"]) == {project, scenario}
    assert "search_filter_scope" not in following.state


@pytest.mark.parametrize("field", ["topic_l1", "topic_l2", "coding_focus"])
def test_explicit_dimension_null_presence_is_idempotent_and_distinct_from_omission(field):
    _, _, _, service = fixture()
    omitted = QueryRequest(message="不限主题", request_id="omit-first")
    original = search(service, omitted)
    with pytest.raises(ValueError, match="IDEMPOTENCY_CONFLICT"):
        service.begin(QueryRequest(message=omitted.message, request_id=omitted.request_id, filters={field: None}))
    body = QueryRequest(message="明确清除", request_id="clear-first", filters={field: None})
    cleared = search(service, body)
    assert service.begin(QueryRequest.model_validate(body.model_dump(mode="json", exclude_unset=True))) == cleared.result
    with pytest.raises(ValueError, match="IDEMPOTENCY_CONFLICT"):
        service.begin(QueryRequest(message=body.message, request_id=body.request_id))
    # A pre-policy completed receipt keeps its historical digest and response.
    with service.database.session() as session, session.begin():
        receipt = session.get(AgentTurn, cleared.id)
        receipt.payload_hash = hashlib.sha256(body.model_dump_json(exclude={"requery_of_run_id"}).encode()).hexdigest()
        payload = dict(receipt.request_payload); payload.pop("search_dimension_policy_version"); receipt.request_payload = payload
    assert service.begin(body) == cleared.result


def test_only_softened_type_is_forwarded_as_optional_preference():
    _, project, scenario, service = fixture()
    class Retriever:
        supports_question_type_preference = True
        calls = []
        def retrieve(self, query, eligible, pipeline, top_k, **options):
            self.calls.append((eligible, options))
            return {"data": [{"canonical_question_id": qid} for qid in eligible],
                    "meta": {"pipeline": pipeline, "rerank_status": "COMPLETED"}}
    service.retriever = Retriever()
    search(service, QueryRequest(message="场景设计", request_id="soft-hint"), question_type="SCENARIO")
    assert set(service.retriever.calls[-1][0]) == {project, scenario}
    assert service.retriever.calls[-1][1] == {"preferred_question_type": "SCENARIO", "preferred_eligible_ids": [scenario]}
    search(service, QueryRequest(message="显式分类", request_id="hard-hint", filters={"question_type": "SCENARIO"}), question_type="SCENARIO")
    assert service.retriever.calls[-1] == ([scenario], {})
    search(service, QueryRequest(message="取消分类", request_id="no-hint", filters={"question_type": None}))
    assert set(service.retriever.calls[-1][0]) == {project, scenario} and service.retriever.calls[-1][1] == {}
