"""Compile bounded, independently sourced evidence for a question comparison.

Only the reviewed target spans define the question. Nearby text is explicitly
reference material: it may resolve a subject or pronoun, never append another
question's task. The compiler does not use labels, canonical IDs, or the peer.
"""
from __future__ import annotations

import hashlib
import re
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from interview_intelligence.contracts import StrictModel
from interview_intelligence.ingestion.snapshot import decode_source


VERSION = "dedup_source_context_v1"


class SourceSpan(StrictModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    quote: str = Field(min_length=1)

    @model_validator(mode="after")
    def valid_interval(self):
        if self.end_char <= self.start_char or len(self.quote) != self.end_char - self.start_char:
            raise ValueError("source evidence interval length mismatch")
        return self


class ReferenceExcerpt(SourceSpan):
    role: Literal["section_heading", "target_line_prefix", "target_line_suffix", "reference_before", "reference_after"]
    use: Literal["reference_only"] = "reference_only"


class SourceQuestionEvidence(StrictModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    version: Literal[VERSION] = VERSION
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_path: str | None = None
    original_path: str | None = None
    raw_question: str = Field(min_length=1)
    source_spans: list[SourceSpan] = Field(min_length=1)
    references: list[ReferenceExcerpt] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def target_is_quoted_spans(self):
        if self.raw_question != "\n".join(s.quote for s in self.source_spans):
            raise ValueError("source evidence raw_question differs from target spans")
        if any(b.start_char < a.end_char for a, b in zip(self.source_spans, self.source_spans[1:])):
            raise ValueError("source evidence target spans overlap or are unordered")
        if sum(len(s.quote) for s in self.references) > 4000:
            raise ValueError("source evidence reference limit exceeded")
        return self


def question_evidence(value: dict | SourceQuestionEvidence | None, question: str) -> dict | None:
    """Check endpoint binding; source-byte authentication belongs to the compiler."""
    if value is None:
        return None
    evidence = SourceQuestionEvidence.model_validate(value)
    if evidence.raw_question != question:
        raise ValueError("source evidence belongs to a different question")
    return evidence.model_dump(mode="json")


def compile_source_evidence(raw_bytes: bytes, source_spans: list[dict], *, raw_question: str,
                            source_hash: str, source_path: str | None = None,
                            original_path: str | None = None) -> dict:
    """Authenticate original bytes/spans, then expose bounded, exact quotations.

    Positions address the ingestion decoder's normalized Unicode text. Hashes
    address the untouched bytes, including BOM and original newline encoding.
    """
    if hashlib.sha256(raw_bytes).hexdigest() != source_hash:
        raise ValueError("source evidence snapshot hash mismatch")
    text = decode_source(raw_bytes)
    spans = [SourceSpan.model_validate({key: s[key] for key in ("start_char", "end_char", "quote")})
             for s in source_spans]
    if not spans:
        raise ValueError("source evidence target spans missing")
    for span in spans:
        if text[span.start_char:span.end_char] != span.quote:
            raise ValueError("source evidence original span mismatch")
    evidence = SourceQuestionEvidence(source_hash=source_hash, source_path=source_path,
        original_path=original_path, raw_question=raw_question, source_spans=spans)

    # Retain exact offsets even for truncated references. Never expand the
    # target across a following question or silently rewrite raw_question.
    lines = []
    offset = 0
    for line in text.splitlines(keepends=True):
        end = offset + len(line.rstrip("\n"))
        lines.append((offset, end))
        offset += len(line)
    start, end = spans[0].start_char, spans[-1].end_char
    first = next(i for i, (a, b) in enumerate(lines) if a <= start <= b)
    # A target may include the newline itself; the line's text end excludes it.
    last = max(i for i, (a, _) in enumerate(lines) if a < end)
    references = []

    def add(role, a, b, *, tail=False):
        if b <= a or not text[a:b].strip():
            return
        limit = min(500, 4000 - sum(len(s.quote) for s in references))
        if limit <= 0:
            return
        if b - a > limit:
            a, b = (b - limit, b) if tail else (a, a + limit)
        references.append(ReferenceExcerpt(role=role, start_char=a, end_char=b, quote=text[a:b]))

    # A section heading is separate from neighboring questions. Plain short
    # topic introductions ("问…" / a trailing colon) support unformatted notes.
    for a, b in reversed(lines[max(0, first - 24):first]):
        line = text[a:b].strip()
        if len(line) <= 120 and (re.match(r"^#{1,6}\s+\S", line)
                or (len(line) <= 40 and not re.search(r"[?？。；;]", line)
                    and (line.endswith((":", "：")) or re.match(r"^问\S+", line)))):
            add("section_heading", a, b)
            break
    add("target_line_prefix", lines[first][0], start, tail=True)
    add("target_line_suffix", end, lines[last][1])
    for a, b in lines[max(0, first - 4):first]:
        add("reference_before", a, b, tail=True)
    for a, b in lines[last + 1:last + 3]:
        add("reference_after", a, b)
    # Repeated excerpts with different roles are intentional: the roles expose
    # why a quotation was selected, while every quotation stays independently
    # auditable against the same source hash.
    evidence.references = references
    return SourceQuestionEvidence.model_validate(evidence.model_dump()).model_dump(mode="json")
