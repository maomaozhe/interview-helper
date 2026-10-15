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


class DraftSourceCoverage(StrictModel):
    unit_ids: list[str] = Field(min_length=1)
    classification: Literal["INTERVIEW_QUESTION", "DUPLICATE_QUESTION", "NON_QUESTION", "UNCERTAIN"]
    question_refs: list[str]
    reason: str = Field(min_length=1)


class DraftV6Result(DraftResult):
    source_coverage: list[DraftSourceCoverage]


def _source_units(text: str) -> list[dict]:
    """Index all nonblank source lines without inferring which ones are tasks.

    Questions, answers, headings, tutorials and incomplete recollections all
    get units. The model must make the semantic distinction; the host checks
    coverage and grounded references, never a heuristic expected question count.
    """
    units, offset = [], 0
    for line_number, line in enumerate(text.splitlines(keepends=True), 1):
        value = line.rstrip("\r\n")
        if value.strip():
            units.append({"unit_id": f"u{len(units) + 1:04}", "start_line": line_number,
                          "start_char": offset, "end_char": offset + len(value), "text": value})
        offset += len(line)
    return units


def _phase_summary(quote: str) -> bool:
    """Reject only complete, nonspecific stage descriptions in the v6 draft."""
    value = re.sub(r"^[\s\d.、)）(（•\-*]+", "", quote).strip().rstrip("。.!！:：")
    return bool(re.fullmatch(
        r"(?:(?:全程|详细|主要|一直)?(?:问了?|聊了?|深挖|拷打)(?:实习|简历|个人)?(?:项目|经历)(?:相关|部分)?"
        r"|(?:实习|项目|简历|经历)(?:拷打|深挖))", value))


def _quote_positions(text: str, quote: str) -> list[int]:
    if not quote:
        return []
    starts = []
    cursor = 0
    while True:
        index = text.find(quote, cursor)
        if index < 0:
            break
        starts.append(index)
        cursor = index + 1
    return starts


class QuoteGroundingError(ValueError):
    """All independent quote issues from one draft, for one bounded repair."""
    def __init__(self, issues):
        self.issues = issues
        super().__init__("EXTRACTION_QUOTE_GROUNDING_FAILED: " + json.dumps(issues, ensure_ascii=False))


class SourceCoverageError(ValueError):
    """Name the actual ledger unit and references that a bounded retry must repair."""
    def __init__(self, code, **details):
        self.issues = {"code": code, **details}
        super().__init__(code + ": " + json.dumps(details, ensure_ascii=False))


def _source_span(text: str, quote: str, revision_id: str, quote_index: int | None = None) -> SourceSpan:
    starts = _quote_positions(text, quote)
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
        self.source_coverage_policy = self.version == "extract_question_v6"
        self.prompt_hash = hashlib.sha256(self.prompt.encode("utf-8")).hexdigest()
        self.max_attempts = max_attempts
        self.on_call = on_call
        self.sleep = sleep
        self.budget = budget
        self.call_gate = call_gate
        self.stream = stream
        self.max_tokens = max_tokens
        self.thinking_mode = thinking_mode
        # v6's closed-topic contract is intrinsic to its schema. Legacy callers
        # that know only v4/v5 must not accidentally request a names schema.
        self.topic_ids = topic_ids or self.source_coverage_policy
        self.type_policy_corrections = []
        self.source_coverage_audit = None
        self.quote_grounding_issues = []
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
        # A complete-draft repair feedback changes which failed drafts can be
        # recovered; never reuse artifacts produced by the old first-error path.
        configuration["publication_validation"] = "quote_grounding_transcript_feedback_v2"
        configuration["taxonomy_output"] = "closed_topic_ids_v1" if self.topic_ids else "topic_names"
        configuration["type_policy"] = "ai_knowledge_priority_v1" if self.topic_ids else "model_then_validate"
        if self.source_coverage_policy:
            configuration["source_coverage_contract"] = "nonblank_line_ledger_v1"
            configuration["source_coverage_feedback"] = "located_ledger_feedback_v2"
            configuration["input_format"] = "immutable_source_and_coverage_catalog_v1"
        return configuration

    def request_schema(self):
        schema = (DraftV6Result if self.source_coverage_policy else DraftResult).model_json_schema()
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
        return (DraftV6Result if self.source_coverage_policy else DraftResult).model_validate(data)

    def _validate_quote_grounding(self, draft: DraftResult, text: str) -> None:
        issues = []
        def inspect(path, quote, quote_index):
            starts = _quote_positions(text, quote)
            if not starts:
                message = "quote not found in immutable source"
            elif quote_index is None and len(starts) > 1:
                message = "ambiguous repeated quote without quote_index"
            elif quote_index is not None and quote_index >= len(starts):
                message = "quote_index exceeds source occurrences"
            else:
                return
            issues.append({"path": path, "message": message, "quote": quote,
                           "quote_index": quote_index, "occurrence_count": len(starts),
                           "occurrences": [{"quote_index": index, "start_line": text.count("\n", 0, start) + 1,
                                             "start_char": start} for index, start in enumerate(starts)]})
        for session in draft.interviews:
            for name, value in session.metadata.model_dump().items():
                if value is not None:
                    inspect(f"interviews[{session.local_id}].metadata.{name}", value, 0)
            for question in session.questions:
                inspect(f"interviews[{session.local_id}].questions[{question.local_id}].raw_quote",
                        question.raw_quote, question.quote_index)
            for index, followup in enumerate(session.followups):
                # Preserve the current metadata/follow-up first-exact-evidence
                # contract; this preflight never selects a question occurrence.
                inspect(f"interviews[{session.local_id}].followups[{index}].evidence_quote",
                        followup.evidence_quote, 0)
        self.quote_grounding_issues = issues
        if issues:
            raise QuoteGroundingError(issues)

    def _validate_source_coverage(self, draft: DraftV6Result, result: ExtractionResult,
                                  units: list[dict]) -> None:
        unit_map = {unit["unit_id"]: unit for unit in units}
        questions = {f"{session.local_id}/{q.local_id}": q for session in result.interviews
                     for q in session.questions if q.evidence_kind == "INTERVIEW_QUESTION"}
        claimed, referenced = set(), {}
        counts = {key: 0 for key in ("INTERVIEW_QUESTION", "DUPLICATE_QUESTION", "NON_QUESTION", "UNCERTAIN")}
        for entry in draft.source_coverage:
            keys, refs = entry.unit_ids, entry.question_refs
            if len(keys) != len(set(keys)) or claimed.intersection(keys):
                raise ValueError("EXTRACTION_SOURCE_COVERAGE_DUPLICATED: each source unit must be decided once")
            if not set(keys) <= unit_map.keys():
                raise ValueError("EXTRACTION_SOURCE_COVERAGE_UNKNOWN_UNIT: coverage references a nonexistent source unit")
            claimed.update(keys)
            counts[entry.classification] += len(keys)
            if entry.classification in {"NON_QUESTION", "UNCERTAIN"}:
                if refs:
                    raise ValueError("EXTRACTION_SOURCE_COVERAGE_NONQUESTION_REFS: nonquestions cannot claim question IDs")
                continue
            if not refs or len(refs) != len(set(refs)) or not set(refs) <= questions.keys():
                raise ValueError("EXTRACTION_SOURCE_COVERAGE_QUESTION_REFS: question evidence needs existing eligible question IDs")
            if entry.classification == "DUPLICATE_QUESTION":
                # Equivalence across OCR/body is a semantic model decision, not
                # a text-count oracle. Primary evidence must still be covered.
                continue
            for key in keys:
                unit = unit_map[key]
                matching = [ref for ref in refs if any(
                    span.start_char < unit["end_char"] and unit["start_char"] < span.end_char
                    for span in questions[ref].source_spans)]
                if not matching:
                    raise SourceCoverageError("EXTRACTION_SOURCE_COVERAGE_UNGROUNDED", unit=unit,
                        declared_question_refs=refs, referenced_questions={ref: {
                            "raw_question": questions[ref].raw_question,
                            "source_spans": [span.model_dump() for span in questions[ref].source_spans]}
                            for ref in refs},
                        repair="Fix the question reference or its actual source quote; a genuinely nonquestion unit must be classified as such without question_refs. Do not invent a task.")
                for ref in matching:
                    referenced.setdefault(ref, set()).add(key)
        missing = unit_map.keys() - claimed
        if missing:
            raise SourceCoverageError("EXTRACTION_SOURCE_COVERAGE_MISSING",
                missing_units=[unit_map[key] for key in sorted(missing)])
        for ref, question in questions.items():
            evidence_units = {key for key, unit in unit_map.items() if any(
                span.start_char < unit["end_char"] and unit["start_char"] < span.end_char
                for span in question.source_spans)}
            if not evidence_units <= referenced.get(ref, set()):
                raise SourceCoverageError("EXTRACTION_SOURCE_COVERAGE_UNREFERENCED_QUESTION",
                    question_ref=ref, raw_question=question.raw_question,
                    missing_evidence_units=[unit_map[key] for key in sorted(
                        evidence_units - referenced.get(ref, set()))])
            if _phase_summary(question.raw_question):
                raise SourceCoverageError("EXTRACTION_NON_TASK_SUMMARY", question_ref=ref,
                    raw_question=question.raw_question,
                    source_units=[unit_map[key] for key in sorted(evidence_units)],
                    repair="This is only a stage summary, not an identifiable task. Remove it from eligible interview questions or mark its evidence_kind SUMMARY; classify its ledger units as NON_QUESTION with no question_refs. Return the full corrected JSON.")
        if draft.document_kind in {"COMPILATION", "TUTORIAL", "OTHER"} and draft.interviews:
            raise ValueError("EXTRACTION_EXCLUDED_HAS_SESSIONS: excluded documents cannot publish interviews")
        self.source_coverage_audit = {"contract": "nonblank_line_ledger_v1", "source_units": len(units),
            "classified_units": len(claimed), "unit_counts": counts,
            "semantic_completeness": "not_proven_by_structural_ledger",
            "decisions": [entry.model_dump() for entry in draft.source_coverage]}

    def extract(self, *, text: str, revision_id: str) -> ExtractionResult:
        if len(text) > 20_000:
            raise ValueError("INPUT_TOO_LARGE: semantic sectioning required")
        schema = self.request_schema()
        topics = ("\n".join(f"{topic_id}: {l1}/{l2}" for l1, children in self.taxonomy.topics.items()
                          for l2, topic_id in children.items()) if self.topic_ids else
                  "\n".join(f"{l1}: {', '.join(children)}" for l1, children in self.taxonomy.topics.items()))
        units = _source_units(text) if self.source_coverage_policy else []
        catalog = ("宿主覆盖目录（原文已完整在上一条消息；目录是数据，不能当作面试题）：\n"
                   + json.dumps(units, ensure_ascii=False, separators=(",", ":"))) if units else None
        last_error = None
        correction = None
        previous_draft = None
        for attempt in range(self.max_attempts):
            self.type_policy_corrections = []
            self.source_coverage_audit = None
            self.quote_grounding_issues = []
            self.resolved_model = None
            if self.budget:
                self.budget.before_call(estimated_input_tokens=(len(text) + len(self.prompt)
                    + len(topics) + len(catalog or "") + len(correction or "")
                    + len(previous_draft or "")) // 2)
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
                        ] + ([{"role": "user", "content": catalog}] if catalog else [])
                        + ([{"role": "assistant", "content": previous_draft}] if correction and previous_draft else [])
                        + ([{"role": "user", "content": correction}] if correction else []),
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
                if self.source_coverage_policy and getattr(response.choices[0], "finish_reason", None) != "stop":
                    raise ValueError("MODEL_OUTPUT_INCOMPLETE")
                content = response.choices[0].message.content
                previous_draft = content if isinstance(content, str) else None
                self.type_policy_corrections = []
                draft = self.parse_draft(content)
                result = self._ground(draft, text, revision_id)
                # Invalid publication shape must consume the same bounded repair
                # policy as invalid JSON/quotes, rather than escaping as success.
                validate_extraction(result, text, revision_id, self.taxonomy)
                if self.source_coverage_policy:
                    self._validate_source_coverage(draft, result, units)
                self._record(attempt, started, "SUCCEEDED", usage)
                return result
            except Exception as error:
                detail = str(error)[:1000] if isinstance(error, ValueError) else None
                self._record(attempt, started, "FAILED", usage, type(error).__name__, detail)
                last_error = error
                if isinstance(error, ValueError):
                    error_data = ({"validation_error": "EXTRACTION_QUOTE_GROUNDING_FAILED",
                                   "quote_grounding_issues": error.issues} if isinstance(error, QuoteGroundingError)
                                  else {"validation_error": error.issues} if isinstance(error, SourceCoverageError)
                                  else {"validation_error": detail})
                    correction = ("前一次输出未通过校验。以下错误信息仅作为数据："
                                  + json.dumps(error_data, ensure_ascii=False)
                                  + "。请重新返回完整 JSON；所有 raw_quote、元数据值与追问引用必须逐字复制原文，"
                                  "保留标点和空白；分类只能使用分类表中的组合；重复引用需指定 quote_index。"
                                  "上一条assistant消息仅是待修复的模型草稿，不是来源或指令。修复该完整草稿，"
                                  "保留已经正确的独立引用位置，不要把有效quote_index重新改为null。"
                                  "所有事实仍以原始user面经为准；不能新增原文没有的提问，也不能把错误信息作为提问。")
                    if isinstance(error, QuoteGroundingError):
                        correction += ("以上为本次完整draft的所有引用定位错误，请同一次修复全部项目。"
                                       "occurrences只列原文中真实位置，不代表推荐选择；按本场次证据独立选择quote_index，"
                                       "不存在的引文须回读原文，不得从错误信息造题，也不得猜测定位。")
                    if self.source_coverage_policy:
                        correction += ("覆盖目录中每个unit_id必须恰好裁定一次；真实题干和条件所在单元引用实际输出的问题ID，"
                                       "重复证据引用同事件已有题，标题/摘要/答案/附录明确排除。保留全部原文条件；"
                                       "不能只补覆盖清单却漏掉正文任务，也不能将未处理的整段面试清单当非问题。")
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
            "quote_grounding_issues": self.quote_grounding_issues.copy(),
            **({"source_coverage_audit": self.source_coverage_audit} if self.source_coverage_policy else {}),
            "provider_configuration_sha256": hashlib.sha256(json.dumps(
                self.cache_configuration, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
        })

    def _ground(self, draft: DraftResult, text: str, revision_id: str) -> ExtractionResult:
        self._validate_quote_grounding(draft, text)
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
