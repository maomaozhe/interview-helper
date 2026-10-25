"""One validated query plan for Pi, Jev and explicit UI actions."""
from typing import Annotated, Literal

from pydantic import Field, model_validator

from interview_intelligence.contracts import FilterSpec, ReviewItem, StrictModel, QuestionType
from interview_intelligence.analytics.listing import ListRequest, StatsListRequest
from interview_intelligence.analytics.semantic_scope import SemanticTask
from interview_intelligence.agent.tool_definitions import ACTION_REQUIRED_FIELDS, COMMON_REQUIRED, TOOL_ACTIONS
from interview_intelligence.agent.goal_contract import UserGoal


QUERY_AGENT_VERSION = "query_agent_v16"
QUERY_MODEL_SCHEMA_VERSION = "QuerySpec_v3"
QUERY_PI_TOOL_WIRE_VERSION = "query_step_v1"


class QuerySpec(StrictModel):
    action: Literal["LIST", "COUNT", "STATS", "SEARCH", "DETAILS", "ANSWER", "REVIEW_STATE", "RECORD_REVIEW", "CLARIFY", "NEXT"]
    user_goal: UserGoal | None = None
    semantic_task: SemanticTask | None = None
    filters: FilterSpec = Field(default_factory=FilterSpec)
    sort: Literal["frequency", "importance", "gap"] = "frequency"
    top_n: int | None = Field(default=None, ge=1, le=1000)
    page_size: int = Field(default=20, ge=1, le=100)
    group_by: Literal["question", "topic", "company", "round"] = "question"
    topic_level: Literal["L1", "L2"] = "L1"
    search_query: str | None = Field(default=None, min_length=1, max_length=500)
    relevance_query: str | None = Field(default=None, min_length=1, max_length=500)
    lexical_facets: list[Annotated[str, Field(min_length=1, max_length=150)]] = Field(default_factory=list, max_length=3)
    preferred_question_type: QuestionType | None = None
    pipeline: Literal["BM25", "HYBRID", "DENSE", "HYBRID_RERANK"] = "HYBRID_RERANK"
    question_ids: list[str] = Field(default_factory=list, max_length=100)
    answer_text: str | None = Field(default=None, min_length=1, max_length=12000)
    answer_kind: Literal["EXPLAIN", "COMPARE", "SOLVE", "STUDY_PLAN", "CHAT"] = "EXPLAIN"
    answer_basis: Literal["GENERAL_KNOWLEDGE", "CORPUS", "MIXED"] = "GENERAL_KNOWLEDGE"
    evidence_tool_ordinals: list[Annotated[int, Field(strict=True, ge=1, le=8)]] = Field(default_factory=list, max_length=8)
    review_items: list[ReviewItem] = Field(default_factory=list, max_length=100)
    review_statuses: list[Literal["UNSEEN","WEAK","REVIEWED","MASTERED"]] = Field(default_factory=list,max_length=4)
    review_order: Literal["BEFORE_TOP_N","AFTER_TOP_N"] = "BEFORE_TOP_N"
    scope: Literal["current_page","full_scope"] = "current_page"
    final: bool = True
    clarification: str | None = Field(default=None, max_length=1000)
    clarification_options: list[str] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def validate_action(self):
        if self.action != "ANSWER" and self.evidence_tool_ordinals:
            raise ValueError("tool evidence references require ANSWER")
        if len(self.evidence_tool_ordinals) != len(set(self.evidence_tool_ordinals)):
            raise ValueError("duplicate tool evidence reference")
        if self.semantic_task and self.action not in {"LIST", "NEXT", "COUNT", "STATS"}:
            raise ValueError("SEMANTIC_SCOPE_ACTION_CONFLICT")
        if self.semantic_task == "ALGORITHM_PROBLEM" and (self.filters.coding_focus or self.filters.annotation_status):
            raise ValueError("SEMANTIC_SCOPE_LEGACY_TASK_FILTER_CONFLICT")
        if self.action == "COUNT" and self.top_n is not None:
            raise ValueError("count requires top_n=null; use LIST pagination.result_total for a Top N set")
        if self.action == "SEARCH" and (not self.search_query or self.page_size > 50 or self.top_n):
            raise ValueError("semantic search requires a query, <=50 candidates and no global top_n")
        if any(not facet.strip() or len(facet) > 150 for facet in self.lexical_facets):
            raise ValueError("lexical facets require 1-150 characters")
        if self.lexical_facets and self.action != "SEARCH":
            raise ValueError("lexical facets require SEARCH")
        if self.preferred_question_type is not None and self.action != "SEARCH":
            raise ValueError("question type preference requires SEARCH")
        if self.action == "CLARIFY" and not self.clarification:
            raise ValueError("clarification text required")
        if any(not choice.strip() or len(choice) > 100 for choice in self.clarification_options):
            raise ValueError("invalid clarification option")
        if self.clarification_options and self.action != "CLARIFY":
            raise ValueError("clarification options require CLARIFY")
        if self.action == "CLARIFY" and not self.final:
            raise ValueError("clarification must end the turn before retrieval")
        if self.action in {"DETAILS", "REVIEW_STATE"} and not self.question_ids and self.scope=="current_page":
            raise ValueError("question IDs required")
        if self.action=="DETAILS" and self.scope=="full_scope":
            raise ValueError("full-scope details are unbounded; choose current-page IDs")
        if self.action == "DETAILS" and len(self.question_ids) > 10:
            raise ValueError("detail batch is at most 10 questions")
        if self.action == "ANSWER":
            if not self.answer_text or not self.answer_text.strip():
                raise ValueError("answer text required")
            if not self.final or self.scope != "current_page":
                raise ValueError("answers must be final and references bound to the displayed page")
            if len(self.question_ids) > 10:
                raise ValueError("answer references are at most 10 questions")
        elif self.answer_text is not None:
            raise ValueError("answer text requires ANSWER")
        if self.action == "RECORD_REVIEW" and not self.review_items:
            raise ValueError("review items required")
        if self.action=="RECORD_REVIEW" and (not self.final or self.scope!="current_page"):
            raise ValueError("review writes must be final and bound to the displayed page")
        if self.action == "STATS" and self.group_by != "question" and self.sort != "frequency":
            raise ValueError("grouped stats require frequency sort")
        return self


def query_model_schema(tool_policy=None, *, require_user_goal=False):
    """Require explicit scope from a model; host/UI contracts keep their defaults."""
    schema = QuerySpec.model_json_schema()
    schema["required"] = list(COMMON_REQUIRED)
    if require_user_goal:
        schema["required"].append("user_goal")
        schema["properties"]["user_goal"] = {"$ref": "#/$defs/UserGoal"}
    schema["$defs"]["FilterSpec"]["required"] = list(FilterSpec.model_fields)
    schema["properties"]["semantic_task"]["description"] = (
        "Only LIST/NEXT/COUNT/STATS may select an audited SQL semantic collection. "
        "SEARCH must set semantic_task=null; its clarified meaning is bound separately by the host.")
    # Pydantic's after-validator is not represented in its generated schema.
    # Expose the same mutually exclusive routes to the Python JSON planner as
    # the action-specific Pi tools, while retaining strict host validation.
    schema["allOf"] = [
        {"if": {"properties": {"action": {"not": {"enum": ["LIST", "NEXT", "COUNT", "STATS"]}}},
                "required": ["action"]},
         "then": {"properties": {"semantic_task": {"type": "null"}}}},
        {"if": {"properties": {"action": {"const": "SEARCH"}}, "required": ["action"]},
         "then": {"properties": {"top_n": {"type": "null"},
                    "page_size": {"type": "integer", "minimum": 1, "maximum": 50},
                    "search_query": {"type": "string", "minLength": 1, "maxLength": 500}},
                  "required": ["search_query"]}},
    ]
    if tool_policy is not None:
        allowed = {action for name, actions in tool_policy["allowed_actions"].items()
                   for action in actions if action in TOOL_ACTIONS.get(name, ())}
        schema["properties"]["action"]["enum"] = sorted(allowed)
    return schema


def validate_model_plan(payload, *, require_user_goal=False):
    if not isinstance(payload, dict):
        raise ValueError("QUERY_PLAN_INCOMPLETE: provide a complete query object")
    required = set(COMMON_REQUIRED)
    if require_user_goal:
        required.add("user_goal")
    required.update(ACTION_REQUIRED_FIELDS.get(payload.get("action"), ()))
    missing = required - payload.keys()
    if isinstance(payload.get("filters"), dict):
        missing.update("filters." + k for k in FilterSpec.model_fields if k not in payload["filters"])
    if missing:
        raise ValueError("QUERY_PLAN_INCOMPLETE: explicitly provide " + ", ".join(sorted(missing)))
    if require_user_goal and payload.get("user_goal") is None:
        raise ValueError("QUERY_PLAN_INCOMPLETE: user_goal must describe the current request")
    return QuerySpec.model_validate(payload)


class QueryRequest(StrictModel):
    message: str = Field(min_length=1, max_length=2000)
    filters: FilterSpec = Field(default_factory=FilterSpec)
    page_size: int = Field(default=20, ge=1, le=100)
    pipeline: Literal["BM25", "HYBRID", "DENSE", "HYBRID_RERANK"] = "HYBRID_RERANK"
    feedback_ids: list[str] = Field(default_factory=list, max_length=3)
    requery_of_run_id: str | None = Field(default=None, min_length=1, max_length=36)
    conversation_id: str | None = Field(default=None, max_length=36)
    expected_version: int | None = Field(default=None, ge=0)
    request_id: str = Field(min_length=1, max_length=128)
    usage_purpose: Literal["practice", "evaluation"] = "practice"


class StructuredQueryRequest(QueryRequest):
    """Host-only query receipt includes the full SQL request in its digest."""
    list_request: ListRequest | StatsListRequest


class ListQueryRequest(StrictModel):
    list_request: ListRequest | StatsListRequest
    conversation_id: str | None = Field(default=None, max_length=36)
    expected_version: int | None = Field(default=None, ge=0)
    request_id: str = Field(min_length=1, max_length=128)
    usage_purpose: Literal["practice", "evaluation"] = "practice"

    def as_query(self):
        request = self.list_request
        filters = FilterSpec.model_validate({k:getattr(request,k) for k in FilterSpec.model_fields})
        return StructuredQueryRequest(message="结构化列表操作", filters=filters,
            page_size=request.page_size, list_request=request, conversation_id=self.conversation_id,
            expected_version=self.expected_version, request_id=self.request_id, usage_purpose=self.usage_purpose)
