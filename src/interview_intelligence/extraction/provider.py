"""OpenAI-compatible structured extractor with exact quote grounding."""

from __future__ import annotations

from contextlib import closing, nullcontext
from types import SimpleNamespace
import json
import hashlib
import random
import re
import time
from pathlib import Path
from typing import Callable, Literal

from pydantic import Field
import httpx

from interview_intelligence.contracts import (
    ExtractedFollowup, ExtractedInterview, ExtractedQuestion,
    ExtractionResult, QuestionType, SourceSpan, StrictModel,
)
from interview_intelligence.taxonomy import load_taxonomy
from interview_intelligence.resources import resource_path
from interview_intelligence.providers.gate import ModelCallGate
from interview_intelligence.extraction.validation import validate_extraction


DEFAULT_PROMPT = resource_path("prompts/extract_question_v2.md")


class DraftMetadata(StrictModel):
    company_raw: str | None
    position_raw: str | None
    round_raw: str | None
    interview_date_raw: str | None
    publish_date_raw: str | None


class DraftQuestion(StrictModel):
    local_id: str
    raw_quote: str = Field(min_length=1)
    quote_index: int | None = Field(ge=0)
    normalized_question: str = Field(min_length=1)
    topic_l1: str
    topic_l2: str
    question_type: QuestionType
    evidence_kind: Literal["INTERVIEW_QUESTION", "CANDIDATE_QUESTION", "ANSWER", "SUMMARY", "OTHER", "UNCERTAIN"]
    confidence: float = Field(ge=0, le=1)
    response_form: Literal["VERBAL", "CODE", "SQL", "UNKNOWN"] = "UNKNOWN"
    coding_focus: Literal["ALGORITHM", "ENGINEERING", "MIXED", "NONE", "UNKNOWN"] = "UNKNOWN"


class DraftFollowup(StrictModel):
    source_local_id: str
    target_local_id: str
    evidence_quote: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class DraftInterview(StrictModel):
    local_id: str
    session_kind: Literal["SINGLE", "SPECIFIC_ROUND", "UNSPECIFIED_ROUNDS"]
    metadata: DraftMetadata
    questions: list[DraftQuestion]
    followups: list[DraftFollowup]


class DraftResult(StrictModel):
    document_kind: Literal["INTERVIEW_REPORT", "COMPILATION", "TUTORIAL", "MIXED", "OTHER", "UNKNOWN"]
    exclusion_reason: str | None
    interviews: list[DraftInterview]


def _source_span(text: str, quote: str, revision_id: str, quote_index: int | None = None) -> SourceSpan:
    starts = []
    cursor = 0
    while True:
        index = text.find(quote, cursor)
        if index < 0:
            break
        starts.append(index)
        cursor = index + 1
    if not starts:
        raise ValueError(f"quote not found in immutable source: {quote[:80]}")
    if quote_index is None and len(starts) > 1:
        raise ValueError(f"ambiguous repeated quote without quote_index: {quote[:80]}")
    position = quote_index or 0
    if position >= len(starts):
        raise ValueError("quote_index exceeds source occurrences")
    start = starts[position]
    end = start + len(quote)
    return SourceSpan(
        revision_id=revision_id, start_char=start, end_char=end,
        start_line=text.count("\n", 0, start) + 1,
        end_line=text.count("\n", 0, end - 1) + 1,
        quote=quote, origin="OCR" if "图片内容（OCR）" in text[:start] else "TEXT",
    )


class OpenAICompatibleExtractor:
    version = "extract_question_v2"

    def __init__(
        self, *, client=None, model: str, api_key: str | None = None,
        base_url: str | None = None, prompt_path: Path = DEFAULT_PROMPT,
        max_attempts: int = 3, on_call: Callable[[dict], None] | None = None,
        sleep: Callable[[float], None] = time.sleep, budget=None,
        call_gate: ModelCallGate | None = None,
        timeout_seconds: float = 180,
        stream: bool = False,
        max_tokens: int | None = None,
        thinking_mode: Literal["auto", "enabled", "disabled"] = "auto",
        topic_ids: bool = False,
    ):
        if max_tokens is not None and (isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0):
            raise ValueError("output token limit must be positive")
        if thinking_mode not in {"auto", "enabled", "disabled"}:
            raise ValueError("unsupported extraction thinking mode")
        if client is None:
            if not api_key or not base_url:
                raise ValueError("model API key and base URL are required")
            from openai import OpenAI
            client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout_seconds, max_retries=0,
                            http_client=httpx.Client(trust_env=False, timeout=timeout_seconds))
        self.client = client
        self.model = model
        self.resolved_model = None
        self.prompt = prompt_path.read_text(encoding="utf-8")
        if prompt_path.stem.startswith("extract_question_v"):
            self.version = prompt_path.stem
        self.prompt_hash = hashlib.sha256(self.prompt.encode("utf-8")).hexdigest()
        self.max_attempts = max_attempts
        self.on_call = on_call
        self.sleep = sleep
        self.budget = budget
        self.call_gate = call_gate
        self.stream = stream
        self.max_tokens = max_tokens
        self.thinking_mode = thinking_mode
        self.topic_ids = topic_ids
        self.type_policy_corrections = []
        self.taxonomy = load_taxonomy()

    @property
    def cache_configuration(self) -> dict:
        """Effective extraction inputs, excluding credentials and retry state."""
        configuration = {
            "schema": self.request_schema(),
            "response_format": {"type": "json_schema", "name": "interview_extraction_v1", "strict": True},
            "temperature": 0,
            "stream": self.stream,
            "stream_options": {"include_usage": True} if self.stream else None,
            "prompt_hash": hashlib.sha256(self.prompt.encode("utf-8")).hexdigest(),
            "taxonomy": {"version": self.taxonomy.version, "topics": self.taxonomy.topics},
            "base_url": str(getattr(self.client, "base_url", "")),
        }
        if self.max_tokens is not None:
            configuration["max_tokens"] = self.max_tokens
        if self.thinking_mode != "auto":
            configuration["thinking"] = {"type": self.thinking_mode}
        configuration["publication_contract"] = "extraction_validation_v1"
        configuration["taxonomy_output"] = "closed_topic_ids_v1" if self.topic_ids else "topic_names"
        configuration["type_policy"] = "ai_knowledge_priority_v1" if self.topic_ids else "model_then_validate"
        return configuration

    def request_schema(self):
        schema = DraftResult.model_json_schema()
        if self.topic_ids:
            question = schema["$defs"]["DraftQuestion"]
            for name in ("topic_l1", "topic_l2"):
                question["properties"].pop(name)
                question["required"].remove(name)
            question["properties"]["topic_id"] = {"type": "string", "enum": list(self.taxonomy.leaves)}
            question["required"].append("topic_id")
        return schema

    def parse_draft(self, content):
        if not self.topic_ids:
            return DraftResult.model_validate_json(content)
        data = json.loads(content)
        for interview in data.get("interviews", []):
            for question in interview.get("questions", []):
                if "topic_id" not in question:
                    raise ValueError("EXTRACTION_TOPIC_ID_REQUIRED")
                if "topic_l1" in question or "topic_l2" in question:
                    raise ValueError("EXTRACTION_TOPIC_NAMES_WITH_IDS")
                question["topic_l1"], question["topic_l2"] = self.taxonomy.labels(question.pop("topic_id"))
        return DraftResult.model_validate(data)

    def extract(self, *, text: str, revision_id: str) -> ExtractionResult:
        if len(text) > 20_000:
            raise ValueError("INPUT_TOO_LARGE: semantic sectioning required")
        schema = self.request_schema()
        topics = ("\n".join(f"{topic_id}: {l1}/{l2}" for l1, children in self.taxonomy.topics.items()
                          for l2, topic_id in children.items()) if self.topic_ids else
                  "\n".join(f"{l1}: {', '.join(children)}" for l1, children in self.taxonomy.topics.items()))
        last_error = None
        correction = None
        for attempt in range(self.max_attempts):
            self.type_policy_corrections = []
            if self.budget:
                self.budget.before_call(estimated_input_tokens=(len(text) + len(self.prompt)) // 2)
            started = time.perf_counter()
            usage = None
            try:
                with self.call_gate.call() if self.call_gate is not None else nullcontext():
                    response_options = ({"stream": True, "stream_options": {"include_usage": True}}
                                        if self.stream else {})
                    if self.max_tokens is not None:
                        response_options["max_tokens"] = self.max_tokens
                    if self.thinking_mode != "auto":
                        response_options["extra_body"] = {"thinking": {"type": self.thinking_mode}}
                    response = self.client.chat.completions.create(
                        model=self.model,
                        messages=[
                            {"role": "system", "content": self.prompt + "\n分类表：\n" + topics},
                            {"role": "user", "content": text},
                        ] + ([{"role": "user", "content": correction}] if correction else []),
                        response_format={"type": "json_schema", "json_schema": {
                            "name": "interview_extraction_v1", "strict": True, "schema": schema,
                        }},
                        temperature=0,
                        **response_options,
                    )
                    if self.stream:
                        content_parts = []
                        finish_reason = None
                        self.resolved_model = None
                        with closing(response):
                            for chunk in response:
                                usage = getattr(chunk, "usage", None) or usage
                                self.resolved_model = getattr(chunk, "model", None) or self.resolved_model
                                if chunk.choices:
                                    choice = chunk.choices[0]
                                    part = getattr(choice.delta, "content", None)
                                    if part:
                                        content_parts.append(part)
                                    finish_reason = choice.finish_reason or finish_reason
                        if finish_reason == "length":
                            raise ValueError("MODEL_OUTPUT_TRUNCATED")
                        if finish_reason != "stop":
                            raise ValueError("STREAM_INCOMPLETE")
                        response = SimpleNamespace(usage=usage, model=self.resolved_model,
                                                   choices=[SimpleNamespace(finish_reason=finish_reason,
                                                       message=SimpleNamespace(content="".join(content_parts)))])
                usage = getattr(response, "usage", None)
                self.resolved_model = getattr(response, "model", None)
                if getattr(response.choices[0], "finish_reason", None) == "length":
                    raise ValueError("MODEL_OUTPUT_TRUNCATED")
                content = response.choices[0].message.content
                self.type_policy_corrections = []
                draft = self.parse_draft(content)
                result = self._ground(draft, text, revision_id)
                # Invalid publication shape must consume the same bounded repair
                # policy as invalid JSON/quotes, rather than escaping as success.
                validate_extraction(result, text, revision_id, self.taxonomy)
                self._record(attempt, started, "SUCCEEDED", usage)
                return result
            except Exception as error:
                detail = str(error)[:1000] if isinstance(error, ValueError) else None
                self._record(attempt, started, "FAILED", usage, type(error).__name__, detail)
                last_error = error
                if isinstance(error, ValueError):
                    correction = ("前一次输出未通过校验。以下错误信息仅作为数据："
                                  + json.dumps({"validation_error": detail}, ensure_ascii=False)
                                  + "。请重新返回完整 JSON；所有 raw_quote、元数据值与追问引用必须逐字复制原文，"
                                  "保留标点和空白；分类只能使用分类表中的组合；重复引用需指定 quote_index。"
                                  "不能新增原文没有的提问，也不能把错误信息作为提问。")
                if attempt + 1 < self.max_attempts:
                    self.sleep(min(30, 2 ** attempt + random.uniform(0, 0.25)))
        raise last_error

    def _record(self, attempt: int, started: float, status: str, usage, error_code: str | None = None,
                error_detail: str | None = None) -> None:
        if self.budget:
            self.budget.after_call(input_tokens=getattr(usage, "prompt_tokens", None),
                                   output_tokens=getattr(usage, "completion_tokens", None))
        if self.on_call is None:
            return
        self.on_call({
            "operation_type": "EXTRACTION", "model": self.model,
            "model_revision": self.resolved_model,
            "prompt_version": self.version,
            "input_tokens": getattr(usage, "prompt_tokens", None),
            "output_tokens": getattr(usage, "completion_tokens", None),
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "status": status, "retry_count": attempt, "error_code": error_code,
            "error_detail": error_detail,
            "thinking_mode": self.thinking_mode,
            "taxonomy_output": "closed_topic_ids_v1" if self.topic_ids else "topic_names",
            "type_policy_corrections": self.type_policy_corrections.copy(),
            "provider_configuration_sha256": hashlib.sha256(json.dumps(
                self.cache_configuration, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
        })

    def _ground(self, draft: DraftResult, text: str, revision_id: str) -> ExtractionResult:
        interviews = []
        first_content = next(((index, line) for index, line in enumerate(text.splitlines(), 1)
                              if line.strip()), None)
        for session in draft.interviews:
            metadata = session.metadata.model_dump()
            evidence = {}
            for key, value in metadata.items():
                if value is not None:
                    evidence[key] = [_source_span(text, value, revision_id, quote_index=0)]
            questions = []
            for question in session.questions:
                self.taxonomy.resolve(question.topic_l1, question.topic_l2)
                question_type = question.question_type
                if (self.topic_ids and question.evidence_kind == "INTERVIEW_QUESTION"
                        and question.topic_l1 == "AI"
                        and question_type in {QuestionType.KNOWLEDGE, QuestionType.PRINCIPLE}):
                    # Apply the existing domain type-priority rule explicitly;
                    # retain the original model choice in telemetry for audit.
                    self.type_policy_corrections.append({"session_id": session.local_id,
                        "question_id": question.local_id, "from": question_type.value, "to": "AI",
                        "policy": "ai_knowledge_priority_v1"})
                    question_type = QuestionType.AI
                span = _source_span(text, question.raw_quote, revision_id, question.quote_index)
                if (question.evidence_kind == "INTERVIEW_QUESTION" and first_content
                        and re.match(r"^\s*#{1,6}\s", first_content[1])
                        and span.start_line == span.end_line == first_content[0]):
                    raise ValueError("EXTRACTION_TITLE_IS_NOT_A_QUESTION: inspect the body, not the document title")
                questions.append(ExtractedQuestion(
                    local_id=question.local_id, raw_question=question.raw_quote,
                    normalized_question=question.normalized_question,
                    source_spans=[span], evidence_kind=question.evidence_kind,
                    confidence=question.confidence, topic_l1=question.topic_l1,
                    topic_l2=question.topic_l2, question_type=question_type,
                    response_form=question.response_form, coding_focus=question.coding_focus,
                ))
            followups = []
            for relation in session.followups:
                followups.append(ExtractedFollowup(
                    source_local_id=relation.source_local_id,
                    target_local_id=relation.target_local_id,
                    evidence_spans=[_source_span(text, relation.evidence_quote, revision_id, quote_index=0)],
                    confidence=relation.confidence,
                ))
            interviews.append(ExtractedInterview(
                local_id=session.local_id, session_kind=session.session_kind,
                metadata=metadata, metadata_evidence=evidence,
                questions=questions, followups=followups,
            ))
        return ExtractionResult(
            schema_version="v1", document_kind=draft.document_kind,
            exclusion_reason=draft.exclusion_reason, interviews=interviews,
        )
