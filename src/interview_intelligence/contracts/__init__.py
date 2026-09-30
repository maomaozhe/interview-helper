"""Public, strict request and extraction contracts."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DateBasis(StrEnum):
    INTERVIEW = "INTERVIEW"
    PUBLISH = "PUBLISH"
    BEST_AVAILABLE = "BEST_AVAILABLE"


class QuestionType(StrEnum):
    KNOWLEDGE = "KNOWLEDGE"
    PRINCIPLE = "PRINCIPLE"
    SCENARIO = "SCENARIO"
    SYSTEM_DESIGN = "SYSTEM_DESIGN"
    PROJECT = "PROJECT"
    ALGORITHM = "ALGORITHM"
    AI = "AI"
    HR = "HR"
    OTHER = "OTHER"


class ReviewStatus(StrEnum):
    UNSEEN = "UNSEEN"
    REVIEWED = "REVIEWED"
    WEAK = "WEAK"
    MASTERED = "MASTERED"


class FilterSpec(StrictModel):
    company: str | None = None
    position: str | None = None
    job_family: Literal["BACKEND", "AI_APPLICATION", "ALGORITHM", "OTHER"] | None = None
    language: str | None = None
    topic_l1: Literal[
        "Java", "数据库", "Redis", "Spring", "中间件", "分布式",
        "计算机基础", "系统设计", "算法", "AI", "项目", "其他",
    ] | None = None
    topic_l2: str | None = None
    question_type: QuestionType | None = None
    round: Literal["FIRST", "SECOND", "THIRD", "FOURTH_PLUS", "HR", "OTHER"] | None = None
    start_date: date | None = None
    end_date: date | None = None
    date_basis: DateBasis = DateBasis.BEST_AVAILABLE

    @model_validator(mode="after")
    def valid_window(self) -> FilterSpec:
        if self.start_date and self.end_date and self.start_date >= self.end_date:
            raise ValueError("start_date must precede end_date")
        return self


class StatsRequest(FilterSpec):
    group_by: Literal["question", "topic", "company", "round"] = "question"
    topic_level: Literal["L1", "L2"] = "L2"
    time_bucket: Literal["month", "week"] | None = None
    sort: Literal["frequency", "importance", "gap"] = "frequency"
    limit: int = Field(default=20, ge=1, le=100)
    cursor: str | None = None

    @model_validator(mode="after")
    def valid_group_sort(self) -> StatsRequest:
        if self.group_by != "question" and self.sort != "frequency":
            raise ValueError("importance and gap sorting require question grouping")
        return self


class SourceSpan(StrictModel):
    revision_id: str
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    quote: str = Field(min_length=1)
    origin: Literal["TEXT", "OCR"]

    @model_validator(mode="after")
    def valid_bounds(self) -> SourceSpan:
        if self.end_char <= self.start_char or self.end_line < self.start_line:
            raise ValueError("invalid source span bounds")
        return self

    def matches(self, text: str) -> bool:
        if text[self.start_char:self.end_char] != self.quote:
            return False
        first_line = text.count("\n", 0, self.start_char) + 1
        last_line = text.count("\n", 0, self.end_char - 1) + 1
        return self.start_line == first_line and self.end_line == last_line


class SourceRef(StrictModel):
    occurrence_id: str
    interview_id: str
    source_document_id: str
    revision_id: str
    raw_file_hash: str
    source_url: str | None = None
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    quote: str
    source_api_url: str


class ReviewItem(StrictModel):
    canonical_question_id: str | None = None
    raw_question: str | None = None
    status: ReviewStatus
    score: int | None = Field(default=None, ge=0, le=5)
    note: str | None = None
    occurred_at: datetime | None = None
    expected_version: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def exactly_one_question(self) -> ReviewItem:
        if bool(self.canonical_question_id) == bool(self.raw_question):
            raise ValueError("provide exactly one question identifier")
        return self


class ReviewRequest(StrictModel):
    operation: Literal["create", "resolve"] = "create"
    idempotency_key: str = Field(min_length=1)
    items: list[ReviewItem] = Field(default_factory=list, max_length=20)
    resolve_event_id: str | None = None
    canonical_question_id: str | None = None
    expected_version: int | None = None

    @model_validator(mode="after")
    def valid_operation(self) -> ReviewRequest:
        if self.operation == "create" and (not self.items or self.resolve_event_id):
            raise ValueError("create requires items and no resolve_event_id")
        if self.operation == "resolve" and (self.items or not self.resolve_event_id or not self.canonical_question_id):
            raise ValueError("resolve requires event and canonical ID without items")
        return self


class ExtractedQuestion(StrictModel):
    local_id: str
    raw_question: str = Field(min_length=1)
    normalized_question: str = Field(min_length=1)
    source_spans: list[SourceSpan] = Field(min_length=1)
    context_before: str | None = None
    context_after: str | None = None
    evidence_kind: Literal["INTERVIEW_QUESTION", "CANDIDATE_QUESTION", "ANSWER", "SUMMARY", "OTHER", "UNCERTAIN"]
    confidence: float = Field(ge=0, le=1)
    algorithm_description_spans: list[SourceSpan] = Field(default_factory=list)
    topic_l1: str | None = None
    topic_l2: str | None = None
    question_type: QuestionType | None = None


class ExtractedFollowup(StrictModel):
    source_local_id: str
    target_local_id: str
    evidence_spans: list[SourceSpan] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class ExtractedInterview(StrictModel):
    local_id: str
    metadata: dict[str, str | None] = Field(default_factory=dict)
    metadata_evidence: dict[str, list[SourceSpan]] = Field(default_factory=dict)
    session_spans: list[SourceSpan] = Field(default_factory=list)
    session_kind: Literal["SINGLE", "SPECIFIC_ROUND", "UNSPECIFIED_ROUNDS"]
    questions: list[ExtractedQuestion] = Field(default_factory=list)
    followups: list[ExtractedFollowup] = Field(default_factory=list)


class ExtractionResult(StrictModel):
    schema_version: str
    document_kind: Literal["INTERVIEW_REPORT", "COMPILATION", "TUTORIAL", "MIXED", "OTHER", "UNKNOWN"]
    exclusion_reason: str | None = None
    interviews: list[ExtractedInterview] = Field(default_factory=list)


class APIError(StrictModel):
    code: str
    message: str
    details: dict | None = None
    retryable: bool = False


class ResponseMeta(StrictModel):
    request_id: str
    corpus_revision: int = Field(ge=0)
    applied_filters: FilterSpec | None = None
    as_of: date | None = None
    user_state_revision: int | None = None
