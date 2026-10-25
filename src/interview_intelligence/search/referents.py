"""Source-bound preceding frames; identity checks do not certify reference semantics.

The loader is trusted to verify immutable source bytes. This module independently
checks the bounded record/claim contract before any antecedent can supply proof.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re

VERSION = "source_reference_frame_v1"
ITEM_VERSION = "source_item_qualifier_frame_v1"
VIEW_VERSION = "validated_source_reference_view_v1"
MAX_ANTECEDENTS = 4
MAX_TEXT_BYTES = 500
# Grammatical pointers/ellipses, never business names or question identities.
_REFERENCE = re.compile(
    r"你(?:刚才|前面)?提到|(?:刚才|前面|之前|上面|上述|前述|(?<!应)该|这个|这一|这些|这种|其中|它们|它|(?<!尤)其(?!他|余|次|实))"
    r"|具体(?:的)?(?:使用场景|实现方式|准备|如何组合|怎么组合)"
    r"|(?:如果|假如|若)(?:用|采用|通过|改成|这样|这么)"
    r"|\b(?:that|this|these|those|it|they|them|above|previous|earlier|mentioned)\b",
    re.I,
)
_HEX = re.compile(r"[0-9a-f]{64}")
_RECORD_FIELDS = ("occurrence_id", "revision_id", "source_interview_id", "build_id", "question_order",
    "source_sha256", "decoded_text_sha256", "source_spans", "original_question",
    "original_question_complete", "source_context_status", "source_active", "source_reference_preceding_ids",
    "source_item_structure_hash")


def content_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def explicit_reference_anchor(text):
    return isinstance(text, str) and bool(_REFERENCE.search(text))


def _record_body(record):
    return {key: record.get(key) for key in _RECORD_FIELDS}


def source_record_valid(record):
    if not isinstance(record, dict) or record.get("source_context_status") != "VERIFIED" or (
            record.get("original_question_complete") is not True or record.get("source_active") is not True):
        return False
    if any(not isinstance(record.get(key), str) or not record[key] for key in
            ("occurrence_id", "revision_id", "source_interview_id", "build_id", "original_question")):
        return False
    if type(record.get("question_order")) is not int or record["question_order"] < 0:
        return False
    if any(not isinstance(record.get(key), str) or not _HEX.fullmatch(record[key])
            for key in ("source_sha256", "decoded_text_sha256")):
        return False
    spans = record.get("source_spans")
    if not isinstance(spans, list) or not spans:
        return False
    previous_end = -1
    quotes = []
    for span in spans:
        if not isinstance(span, dict):
            return False
        start, end, quote = span.get("start_char"), span.get("end_char"), span.get("quote")
        if type(start) is not int or type(end) is not int or not (0 <= start < end) or start < previous_end:
            return False
        if not isinstance(quote, str) or len(quote) != end - start or not quote.strip():
            return False
        if span.get("revision_id", record["revision_id"]) != record["revision_id"]:
            return False
        previous_end = end
        quotes.append(quote)
    # Raw asks may be a continuous subspan of an extracted quote, never a
    # normalized canonical rephrasing or text borrowed from a different span.
    if not any(record["original_question"] in quote for quote in quotes):
        return False
    return record.get("source_identity_hash") == content_hash(_record_body(record))


def seal_source_record(record):
    record = deepcopy(record)
    record["source_identity_hash"] = content_hash(_record_body(record))
    return record


def _same_source(left, right):
    return all(left.get(key) == right.get(key) for key in
        ("revision_id", "source_interview_id", "build_id", "source_sha256", "decoded_text_sha256"))


def _before(left, right):
    return left["question_order"] < right["question_order"] and (
        max(span["end_char"] for span in left["source_spans"]) <=
        min(span["start_char"] for span in right["source_spans"]))


def _sealed_frame(body):
    return {**body, "frame_hash": content_hash(body)}


def build_reference_frame(current, preceding, relations=(), *, source_context_index=0,
        text_budget=0, source_text=None):
    """Offer at most four whole nearest previous asks, never skip an obstacle.

    ``preceding`` is the full same-interview active sequence, in nearest-first
    order. SOURCE edges remain separate facts; absent edges do not disqualify
    physically verified context. No inferred edge is written to the graph.
    """
    body = {"version": VERSION, "status": "NOT_APPLICABLE", "reason": "NO_EXPLICIT_BACKREFERENCE",
        "frame_kind": "PRECEDING_REFERENCE",
        "source_context_index": source_context_index, "current_source_context_index": source_context_index,
        "current_occurrence_id": current.get("occurrence_id"),
        "current_source_identity_hash": current.get("source_identity_hash"),
        "semantic_resolution": "UNASSESSED", "antecedents": [],
        "available_preceding_count": len(preceding), "bounded": True,
        "text_budget": text_budget, "frame_text_bytes": 0, "unoffered_preceding_count": len(preceding)}
    if not explicit_reference_anchor(current.get("original_question")):
        return _sealed_frame(body)
    body.update(status="UNRESOLVED", reason="CURRENT_SOURCE_UNAVAILABLE")
    if not source_record_valid(current):
        return _sealed_frame(body)
    if not preceding:
        body["reason"] = "NO_PRECEDING_SOURCE"
        return _sealed_frame(body)
    # All SOURCE incoming relationships reachable within the offered frame are
    # checked, including multiple parents; an invalid graph is never silently
    # replaced with an apparently verified adjacency edge.
    records = {item.get("occurrence_id"): item for item in [current, *preceding]}
    relevant = {current["occurrence_id"], *(item.get("occurrence_id") for item in preceding[:MAX_ANTECEDENTS])}
    incoming = {}
    for edge in relations:
        if not isinstance(edge, dict):
            body["reason"] = "INVALID_SOURCE_RELATION"
            return _sealed_frame(body)
        if edge.get("relation_type") != "OBSERVED_FOLLOWUP" or edge.get("provenance") != "SOURCE":
            continue
        if edge.get("target_occurrence_id") not in relevant:
            continue
        # Transport timestamps are not relationship identity and can be Python
        # datetimes in SQL versus strings in a frozen JSON export.
        normalized = {key: deepcopy(value) for key, value in edge.items() if key != "created_at"}
        incoming.setdefault(edge.get("target_occurrence_id"), []).append(normalized)
    if any(len(edges) > 1 for edges in incoming.values()):
        body["reason"] = "MULTIPLE_SOURCE_PARENTS"
        return _sealed_frame(body)
    for edges in incoming.values():
        edge = edges[0]
        left, right = records.get(edge.get("source_occurrence_id")), records.get(edge.get("target_occurrence_id"))
        if edge.get("source_occurrence_id") not in relevant:
            body["reason"] = "REFERENCE_HOP_BUDGET"
            return _sealed_frame(body)
        spans = edge.get("evidence_spans")
        if (not isinstance(edge.get("id"), str) or not edge["id"]
                or edge.get("source_canonical_id") is not None or edge.get("target_canonical_id") is not None
                or not left or not right or not source_record_valid(left) or not source_record_valid(right)
                or not _same_source(current, left) or not _same_source(left, right) or not _before(left, right)
                or edge.get("source_interview_id") != current["source_interview_id"]
                or not isinstance(source_text, str) or not isinstance(spans, list) or not spans):
            body["reason"] = "INVALID_SOURCE_RELATION"
            return _sealed_frame(body)
        for span in spans:
            if not isinstance(span, dict):
                body["reason"] = "INVALID_RELATION_SPAN"
                return _sealed_frame(body)
            start, end, quote = span.get("start_char"), span.get("end_char"), span.get("quote")
            if (type(start) is not int or type(end) is not int or not (0 <= start < end <= len(source_text))
                    or span.get("revision_id", current["revision_id"]) != current["revision_id"]
                    or source_text[start:end] != quote):
                body["reason"] = "INVALID_RELATION_SPAN"
                return _sealed_frame(body)
    seen, previous = {current["occurrence_id"]}, current
    for index, record in enumerate(preceding[:MAX_ANTECEDENTS]):
        if (not source_record_valid(record) or not _same_source(current, record)
                or not _before(record, previous) or record["occurrence_id"] in seen):
            body.update(status="UNRESOLVED", reason="INVALID_PRECEDING_SOURCE", antecedents=[], frame_text_bytes=0)
            return _sealed_frame(body)
        size = len(record["original_question"].encode("utf-8"))
        if body["frame_text_bytes"] + size > text_budget:
            body["reason"] = "REFERENCE_BYTE_BUDGET"
            break
        target_ids = seen
        observed = [deepcopy(edges[0]) for target, edges in incoming.items()
            if target in target_ids and edges[0].get("source_occurrence_id") == record["occurrence_id"]]
        bounded_record = {key: deepcopy(record[key]) for key in (*_RECORD_FIELDS, "source_identity_hash") if key in record}
        body["antecedents"].append({**bounded_record, "slot": f"s{source_context_index}r{index}",
            "frame_kind": "PRECEDING_REFERENCE",
            "relation_provenance": "SOURCE" if observed else "CANDIDATE_REFERENCE", "observed_edges": observed})
        body["frame_text_bytes"] += size
        seen.add(record["occurrence_id"])
        previous = record
    body["unoffered_preceding_count"] = len(preceding) - len(body["antecedents"])
    if body["antecedents"]:
        body.update(status="VERIFIED", reason="SOURCE_IDENTITY_ONLY")
    return _sealed_frame(body)


_NUMBERED = re.compile(r"^(?P<indent>[ \t]*)(?P<number>\d+|[一二三四五六七八九十]+)[.、）)]\s+")
_NUMBER_LIKE = re.compile(r"^[ \t]*(?:\d+|[一二三四五六七八九十]+)[.、）)]")
_BULLET = re.compile(r"^[ \t]*[-*+]\s+")


def _independent_ask(text):
    """Conservative grammar boundary, independent of subject/domain names.

    This is a stop rule for unstructured peer lines, not a complete semantic
    classifier. The caller separately preserves formal deeper sub-item levels.
    """
    text = _BULLET.sub("", text).strip()
    if re.search(r"[?？]|如何|怎么|怎样|为何|为什么|是否|能否|什么|哪些|多少|几种|能不能|可不可以", text):
        return True
    if re.match(r"(?:please\s+)?(?:what|how|why|when|where|who|which|can|could|would|should|is|are|do|does)\b", text, re.I):
        return True
    # Nominal requirement declarations are not imperative verb requests.
    if re.match(r"(?:设计|实现|说明|开发)(?:目标|要求|约束|范围|结果|方式|内容|目的|原则)(?:是|为|包括|应|需|[:：])", text):
        return False
    if re.match(r"(?:design|implementation|development)\s+(?:goals?|requirements?|constraints?|scope|outcomes?)\s+(?:are|is|include|must)\b", text, re.I):
        return False
    return bool(re.match(r"(?:请(?:你)?|试着|尝试)?(?:设计|实现|构建|开发|介绍|解释|比较|分析|讨论|给出|说明|写出|编写|讲讲|谈谈|推导|证明)", text)
        or re.match(r"(?:please\s+)?(?:design|build|implement|develop|describe|explain|compare|analyze|discuss|provide|write|prove)\b", text, re.I))


def build_source_item_frame(current, source_text, *, source_context_index=0, text_budget=0):
    """Offer owned-position qualifiers, without asserting semantic ownership.

    A bare subsequent question, blank paragraph or malformed peer stops the
    frame. Unindented OCR continuation statements are allowed only inside an
    explicit numbered item. We never scan past an obstacle for a useful noun.
    """
    if not source_record_valid(current) or not isinstance(source_text, str) or (
            hashlib.sha256(source_text.encode()).hexdigest() != current.get("decoded_text_sha256")):
        return None
    spans = current["source_spans"]
    start, end = min(span["start_char"] for span in spans), max(span["end_char"] for span in spans)
    if source_text[start:end] != current["original_question"] or "\n" in current["original_question"]:
        return None
    line_start = source_text.rfind("\n", 0, start) + 1
    line_end = source_text.find("\n", end)
    line_end = len(source_text) if line_end < 0 else line_end
    line = source_text[line_start:line_end]
    marker = _NUMBERED.match(line)
    if not marker or line_start + marker.end() != start or source_text[end:line_end].strip():
        return None
    if " " in marker["indent"] and "\t" in marker["indent"]:
        return None
    indent = len(marker["indent"].expandtabs(4))
    qualifiers, offset, boundary_kind, boundary = [], line_end + 1, "EOF", len(source_text)
    scanned = 0
    while offset < len(source_text):
        next_end = source_text.find("\n", offset)
        next_end = len(source_text) if next_end < 0 else next_end
        value = source_text[offset:next_end]
        next_marker = _NUMBERED.match(value)
        whitespace = re.match(r"[ \t]*", value)[0]
        level = len(whitespace.expandtabs(4))
        if next_marker and len(next_marker["indent"].expandtabs(4)) <= indent:
            boundary_kind, boundary = "NEXT_PEER_ITEM", offset
            break
        if not value.strip() or value.lstrip().startswith("#"):
            boundary_kind, boundary = "PARAGRAPH_OR_SECTION_BOUNDARY", offset
            break
        if (next_marker or _NUMBER_LIKE.match(value) or scanned >= MAX_ANTECEDENTS
                or level < indent or " " in whitespace and "\t" in whitespace):
            boundary_kind, boundary = "AMBIGUOUS_OR_BOUNDED_STRUCTURE", offset
            break
        # A bare bullet at the parent's indentation is not a child level.
        # Strictly deeper formal source lines remain candidate qualifiers;
        # their semantic ownership still needs the independent z declaration.
        if level <= indent and _independent_ask(value):
            boundary_kind, boundary = "INDEPENDENT_FOLLOWING_QUESTION", offset
            break
        if not value.strip() or len(value.encode()) > text_budget - sum(len(q["qualifier_text"].encode()) for q in qualifiers):
            boundary_kind, boundary = "ITEM_BYTE_BUDGET", offset
            break
        record = {key: deepcopy(current[key]) for key in (*_RECORD_FIELDS, "source_identity_hash") if key in current}
        record.update(slot=f"s{source_context_index}i{len(qualifiers)}", frame_kind="SOURCE_ITEM_QUALIFIER",
            current_source_identity_hash=current["source_identity_hash"],
            qualifier_text=value, qualifier_span={"revision_id": current["revision_id"],
                "start_char": offset, "end_char": next_end, "quote": value},
            relation_provenance="SOURCE_ITEM_POSITION_ONLY")
        # No original_question alias for a future line: it is a distinct source
        # kind and can never become a current task/construction option.
        record.pop("original_question", None)
        qualifiers.append(record)
        scanned += 1
        offset = next_end + 1
    if not qualifiers:
        return None
    structure = {"current_occurrence_id": current["occurrence_id"], "source_sha256": current["source_sha256"],
        "decoded_text_sha256": current["decoded_text_sha256"], "item_start": line_start,
        "current_span_start": start, "current_span_end": end, "item_number": marker["number"],
        "item_indent": indent, "boundary_start": boundary, "boundary_kind": boundary_kind,
        "qualifier_spans": [item["qualifier_span"] for item in qualifiers]}
    return _sealed_frame({"version": ITEM_VERSION, "frame_kind": "SOURCE_ITEM_QUALIFIER",
        "status": "VERIFIED", "reason": "SOURCE_ITEM_POSITION_ONLY", "semantic_resolution": "UNASSESSED",
        "current_source_context_index": source_context_index, "source_context_index": source_context_index,
        "current_occurrence_id": current["occurrence_id"], "current_source_identity_hash": current["source_identity_hash"],
        "source_item_structure": structure, "source_item_structure_hash": content_hash(structure),
        "qualifiers": qualifiers, "frame_text_bytes": sum(len(q["qualifier_text"].encode()) for q in qualifiers)})


def validated_reference_view(candidate):
    """Recheck loader-bound physical frames; semantic resolution stays unassessed."""
    result = {"version": VIEW_VERSION, "status": "NOT_APPLICABLE", "reason": "NO_REFERENCE_FRAME",
        "semantic_resolution": "UNASSESSED", "frames": [], "slots": {}, "unresolved_frames": []}
    sources = candidate.get("source_context", []) if isinstance(candidate, dict) else []
    if not isinstance(sources, list) or len(sources) > 2:
        result.update(status="UNRESOLVED", reason="INVALID_CURRENT_SOURCES")
        return {**result, "view_hash": content_hash(result)}
    total = sum(len(source.get("original_question", "").encode("utf-8"))
        for source in sources if isinstance(source, dict) and isinstance(source.get("original_question", ""), str))
    invalid = None
    for index, source in enumerate(sources):
        item_frame = source.get("source_item_frame") if isinstance(source, dict) else None
        if item_frame is not None:
            structure = item_frame.get("source_item_structure", {}) if isinstance(item_frame, dict) else {}
            qualifiers = item_frame.get("qualifiers", []) if isinstance(item_frame, dict) else []
            if (not isinstance(item_frame, dict) or item_frame.get("version") != ITEM_VERSION
                    or item_frame.get("status") != "VERIFIED" or not source_record_valid(source)
                    or item_frame.get("current_source_context_index") != index
                    or type(item_frame.get("current_source_context_index")) is not int
                    or item_frame.get("current_occurrence_id") != source["occurrence_id"]
                    or item_frame.get("current_source_identity_hash") != source["source_identity_hash"]
                    or item_frame.get("frame_hash") != content_hash({key: value for key, value in item_frame.items() if key != "frame_hash"})
                    or content_hash(structure) != source.get("source_item_structure_hash")
                    or item_frame.get("source_item_structure_hash") != source.get("source_item_structure_hash")
                    or structure.get("current_occurrence_id") != source["occurrence_id"]
                    or structure.get("source_sha256") != source["source_sha256"]
                    or structure.get("decoded_text_sha256") != source["decoded_text_sha256"]
                    or not isinstance(qualifiers, list) or not 1 <= len(qualifiers) <= MAX_ANTECEDENTS
                    or structure.get("qualifier_spans") != [q.get("qualifier_span") for q in qualifiers if isinstance(q, dict)]):
                invalid = "INVALID_SOURCE_ITEM_FRAME"
                break
            previous_end = max(span["end_char"] for span in source["source_spans"])
            for pos, qualifier in enumerate(qualifiers):
                span = qualifier.get("qualifier_span", {})
                if (not _same_source(source, qualifier) or qualifier.get("slot") != f"s{index}i{pos}"
                        or qualifier.get("frame_kind") != "SOURCE_ITEM_QUALIFIER"
                        or qualifier.get("current_source_identity_hash") != source["source_identity_hash"]
                        or qualifier.get("source_context_status") != "VERIFIED"
                        or span.get("revision_id") != source["revision_id"]
                        or type(span.get("start_char")) is not int or type(span.get("end_char")) is not int
                        or span["start_char"] <= previous_end or span["end_char"] > structure.get("boundary_start", -1)
                        or span["end_char"] - span["start_char"] != len(qualifier.get("qualifier_text", ""))
                        or qualifier.get("qualifier_text") != span.get("quote")):
                    invalid = "INVALID_SOURCE_ITEM_QUALIFIER"
                    break
                previous_end = span["end_char"]
                total += len(qualifier["qualifier_text"].encode())
                result["slots"][qualifier["slot"]] = deepcopy(qualifier)
            result["frames"].append(deepcopy(item_frame))
        frame = source.get("reference_frame") if isinstance(source, dict) else None
        if frame is None:
            continue
        if not isinstance(frame, dict) or frame.get("version") != VERSION or frame.get("frame_hash") != content_hash(
                {key: value for key, value in frame.items() if key != "frame_hash"}):
            invalid = "INVALID_FRAME_HASH"
            break
        if frame.get("status") == "NOT_APPLICABLE":
            continue
        if frame.get("status") != "VERIFIED":
            result["unresolved_frames"].append(deepcopy(frame))
            continue
        antecedents = frame.get("antecedents")
        if (not source_record_valid(source) or frame.get("current_source_context_index") != index
                or type(frame.get("current_source_context_index")) is not int
                or frame.get("source_context_index") != index or frame.get("current_occurrence_id") != source["occurrence_id"]
                or frame.get("current_source_identity_hash") != source["source_identity_hash"]
                or not isinstance(antecedents, list) or not 1 <= len(antecedents) <= MAX_ANTECEDENTS):
            invalid = "CURRENT_FRAME_BINDING_MISMATCH"
            break
        offered_ids = [item.get("occurrence_id") for item in antecedents if isinstance(item, dict)]
        sequence_ids = source.get("source_reference_preceding_ids")
        if not isinstance(sequence_ids, list) or offered_ids != sequence_ids[:len(antecedents)]:
            invalid = "NONCONTIGUOUS_SOURCE_FRAME"
            break
        previous, seen = source, {source["occurrence_id"]}
        for pos, item in enumerate(antecedents):
            if (not source_record_valid(item) or not _same_source(source, item) or not _before(item, previous)
                    or item["occurrence_id"] in seen or item.get("slot") != f"s{index}r{pos}"
                    or item.get("relation_provenance") not in {"SOURCE", "CANDIDATE_REFERENCE"}):
                invalid = "INVALID_ANTECEDENT_BINDING"
                break
            total += len(item["original_question"].encode("utf-8"))
            result["slots"][item["slot"]] = deepcopy(item)
            seen.add(item["occurrence_id"])
            previous = item
        result["frames"].append(deepcopy(frame))
    if total > MAX_TEXT_BYTES:
        invalid = "REFERENCE_TOTAL_BYTE_BUDGET"
    if invalid:
        result.update(status="UNRESOLVED", reason=invalid, frames=[], slots={})
    elif result["frames"]:
        result.update(status="VERIFIED", reason="SOURCE_IDENTITY_ONLY")
    elif result["unresolved_frames"]:
        result.update(status="UNRESOLVED", reason=result["unresolved_frames"][0].get("reason", "UNRESOLVED_REFERENCE_FRAME"))
    return {**result, "view_hash": content_hash(result)}


def validate_reference_claim(candidate, claim):
    """Bind an independently emitted z claim, never certify its semantic truth."""
    view = validated_reference_view(candidate)
    result = {**view, "valid": False, "grounded": False, "model_unique_claim": False,
        "current_source_context_index": None, "current_occurrence_id": None,
        "anchor_text": "", "selected_slots": [], "slots": {}, "semantic_resolution": "UNRESOLVED"}
    if not isinstance(claim, dict) or set(claim) != {"a", "r", "u"}:
        result["reason"] = "MISSING_OR_INVALID_REFERENCE_CLAIM"
        return result
    if view["status"] != "VERIFIED":
        return result
    anchor_ref = claim.get("a")
    match = re.fullmatch(r"s(\d+)o(\d+)(?::s\1o(\d+))?", anchor_ref) if isinstance(anchor_ref, str) and len(anchor_ref) <= 64 else None
    slots = claim.get("r")
    if not match or not isinstance(slots, list) or not 1 <= len(slots) <= MAX_ANTECEDENTS or any(type(slot) is not str for slot in slots):
        result["reason"] = "INVALID_REFERENCE_ANCHOR_OR_SLOTS"
        return result
    index = int(match[1])
    mode = "i" if slots[0].startswith(f"s{index}i") else "r"
    kind = "SOURCE_ITEM_QUALIFIER" if mode == "i" else "PRECEDING_REFERENCE"
    frames = [frame for frame in view["frames"] if frame["current_source_context_index"] == index and frame.get("frame_kind") == kind]
    if len(frames) != 1 or slots != [f"s{index}{mode}{pos}" for pos in range(len(slots))] or any(slot not in view["slots"] for slot in slots):
        result["reason"] = "REFERENCE_NOT_CONTIGUOUS_PREFIX"
        return result
    # Import lazily to share the exact candidate-local excerpt numbering while
    # avoiding a module cycle with the citation resolver.
    from interview_intelligence.search.citations import _excerpts
    source = candidate["source_context"][index]
    excerpts = _excerpts(source["original_question"])
    first, last = int(match[2]), int(match[3] or match[2])
    if first > last or last >= len(excerpts) or last - first > 10:
        result["reason"] = "UNKNOWN_REFERENCE_ANCHOR"
        return result
    anchor = "".join(excerpts[first:last + 1])
    if (mode == "r" and not explicit_reference_anchor(anchor)) or anchor not in source["original_question"]:
        result["reason"] = "NO_EXPLICIT_CURRENT_BACKREFERENCE"
        return result
    result.update(anchor_text=anchor, current_source_context_index=index,
        current_occurrence_id=source["occurrence_id"], selected_slots=slots,
        frame_kind=kind,
        model_unique_claim=claim.get("u") == "UNIQUE")
    if claim.get("u") != "UNIQUE":
        result["reason"] = "REFERENCE_RESOLUTION_NOT_UNIQUE"
        return result
    result.update(valid=True, grounded=True, reason="MODEL_ASSERTED_UNIQUE_SOURCE_BOUND_REFERENCE",
        semantic_resolution="MODEL_ASSERTED_UNIQUE", slots={slot: view["slots"][slot] for slot in slots})
    result["view_hash"] = content_hash({key: value for key, value in result.items() if key != "view_hash"})
    return result
