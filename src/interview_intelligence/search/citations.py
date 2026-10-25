"""Select candidate-local canonical excerpts without regenerating their text."""
import re

from interview_intelligence.search.candidate_prompt import candidate_payload, requires_verified_original

_NEW_QUESTION = re.compile(r"[\n，。？！；,?!;]\s*(?:\d+[.、）)]|[一二三四五六七八九十]+[、）)]|(?:问题|题目)[一二三四五六七八九十\d]+)")


PRIMARY_EVIDENCE_INSTRUCTIONS = (
    "本轮证据输出契约优先：每题question是按q0、q1等编号排列的连续原文片段映射，按顺序阅读就是当前题干。"
    "object_evidence和focus_evidence填写该题的片段编号（如q0），不要重新抄写或改写引文。"
    "宿主按当前candidate_id解析编号为原文，其他候选和source_context的编号不能借用。"
    "对象编号须支持当前限定对象，任务编号须支持当前限定动作/现象；不因存在编号就判相关。"
    "task_evidence也填写本题的片段编号：qN来自question，s0oN来自第0条VERIFIED原问，"
    "s0bN/s0aN来自该条前文/后文，其他条依次s1等。不要重新抄写、拼接或加省略号。"
    "DESIGN_TASK/ALGORITHM_TASK只能选question或VERIFIED original_question，不能借前后文证明建设或算法任务；"
    "其他任务类型可选适用于当前题的VERIFIED上下文标记。证据不足用空字符串。"
    "存在VERIFIED原问时，正向任务优先选该原问编号；标准题改写片段只有逐字存在同一原问才可作任务证据。"
    "query_task_evidence及其他query证据仍按原query摘取。"
)

DESIGN_UNIT_BOUNDARY_INSTRUCTIONS = (
    "独立设计查询额外检查：新建或架构改造的交付目标必须已在当前题或其VERIFIED原问明确提出。"
    "只有规模数字加‘如何优化/怎么拆服务/怎么保证性能’的残缺标题，未给新建/重构的独立对象或"
    "相互约束的机制目标，不能由你补成完整架构设计，keep=false。"
    "原问或适用上下文明确是在追问自己的既有项目实现/优化，且没有新增建设需求时，"
    "仍是PROJECT_REPORT，不能把标准化后的业务名和技术列表补成新建设计，keep=false。"
    "完整设计题的性能、存储、恢复等多项需求属于同一任务，不因是子机制而拆掉主设计；"
    "新建基础服务、缓存、队列、容灾或状态恢复机制也可独立设计，不要求必须是完整业务系统。"
    "只有当前query要求独立设计时适用，明确找局部架构或优化题时仍按用户要求判断。"
)


def _excerpts(text):
    parts = []
    for match in re.finditer(r"[^，。？！；：,\n?!;:]+[，。？！；：,\n?!;:]?|[，。？！；：,\n?!;:]", text):
        value = match.group()
        parts.extend(value[start:start+120] for start in range(0, len(value), 120))
    return [value for value in parts if value.strip()]


def primary_candidate_payload(candidates, *, original_only=False):
    payload = candidate_payload(candidates)
    for item in payload:
        item["canonical_evidence_options"] = {f"q{index}": text for index, text in enumerate(_excerpts(item["question"]))}
        task_options = {key: {"text": value, "source": "question"}
                        for key, value in item["canonical_evidence_options"].items()}
        for source_index, source in enumerate(item.get("source_context", [])):
            if source.get("source_context_status") != "VERIFIED":
                continue
            for field, prefix in (("original_question", "o"), ("context_before", "b"), ("context_after", "a")):
                for index, text in enumerate(_excerpts(source.get(field, ""))):
                    task_options[f"s{source_index}{prefix}{index}"] = {
                        "text": text, "source": field, "source_context_index": source_index}
        item["task_evidence_options"] = task_options
        # Canonical wording remains available for object/focus matching, but
        # cannot invent an affirmative task missing from authoritative sources.
        originals = verified_originals(item)
        for value in task_options.values():
            value["task_authoritative"] = value["source"] == "original_question" or (
                value["source"] == "question" and
                ((not originals and not requires_verified_original(item)) or any(value["text"] in text for text in originals)))
        if original_only and requires_verified_original(item):
            # Canonical excerpts remain object/focus evidence. The scoped wire
            # must not tempt a model to use qN (or a neighboring question) as
            # task evidence, even when its text happens to overlap the raw ask.
            item["task_evidence_options"] = {key: value for key, value in task_options.items()
                if value["source"] == "original_question"}
    return payload


def verified_originals(item):
    return [source["original_question"] for source in item.get("source_context", [])
            if source.get("source_context_status") == "VERIFIED" and source.get("original_question")]


def authoritative_task_options(item):
    """Only current-question evidence; context can explain an object, not a task."""
    originals = verified_originals(item)
    return {key: value for key, value in item["task_evidence_options"].items()
            if value["source"] == "original_question" or
            (value["source"] == "question" and
             ((not originals and not requires_verified_original(item)) or any(value["text"] in text for text in originals)))}


def reference_record_text(record):
    return record.get("qualifier_text", "") if record.get("frame_kind") == "SOURCE_ITEM_QUALIFIER" else record.get("original_question", "")


def reference_evidence_options(view):
    """Distinct antecedent origins; these are never current task options."""
    return {f"{slot}o{index}": {"text": text, "source": "source_item_qualifier" if "i" in slot else "reference_question",
                "source_context_index": int(re.fullmatch(r"s(\d+)[ri]\d+", slot)[1]), "reference_slot": slot}
        for slot, record in view.get("slots", {}).items() if re.fullmatch(r"s\d+[ri]\d+", slot)
        for index, text in enumerate(_excerpts(reference_record_text(record)))}


def resolve_reference_option(reference, payload, claim):
    """Resolve one exact antecedent span only after a valid scoped claim."""
    if claim.get("valid") is not True or claim.get("model_unique_claim") is not True:
        return None
    local = payload.get("reference_evidence_options", {})
    match = re.fullmatch(r"(s\d+[ri]\d+)o(\d+)(?::\1o(\d+))?", reference)
    if not match or match[1] not in claim.get("selected_slots", []):
        return None
    first, last = int(match[2]), int(match[3]) if match[3] is not None else int(match[2])
    if last < first or last - first >= len(local):
        return None
    keys = [f"{match[1]}o{index}" for index in range(first, last + 1)]
    if any(key not in local for key in keys):
        return None
    text = "".join(local[key]["text"] for key in keys)
    original = reference_record_text(claim["slots"][match[1]])
    if not text or len(text) > 160 or text not in original:
        return None
    return {**local[keys[0]], "text": text}


def continuous_task_ranges(item, local, sufficient, *, max_chars=160):
    """Minimal sufficient exact ranges from one question; never join neighbors."""
    groups = {}
    for key, value in local.items():
        groups.setdefault((value["source"], value.get("source_context_index")), []).append(key)
    ranges = {}
    originals = verified_originals(item)
    for (source, index), keys in groups.items():
        original = item["question"] if source == "question" else item["source_context"][index]["original_question"]
        for start, key in enumerate(keys):
            text = local[key]["text"]
            if sufficient(text):
                continue
            for end in range(start + 1, len(keys)):
                text += local[keys[end]]["text"]
                if len(text) > max_chars or text not in original:
                    break
                if source == "question" and originals and not any(text in raw for raw in originals):
                    break
                if _NEW_QUESTION.search(text):
                    break
                if sufficient(text):
                    # A sufficient atomic quote already exists; avoid redundant
                    # ranges unless its own target/action is incomplete.
                    if not any(sufficient(local[k]["text"]) for k in keys[start:end + 1]):
                        ranges[f"{key}:{keys[end]}"] = keys[start:end + 1]
                    break
    return ranges


def resolve_task_option(reference, payload, ranges=None):
    local = authoritative_task_options(payload)
    if reference in local:
        return local[reference]
    keys = (ranges or {}).get(reference)
    if keys is None and payload.get("_source_bound_ranges"):
        # Source authority and contiguity are mechanical facts. A heuristic
        # offer list must not turn an exact original span into a bad citation.
        match = re.fullmatch(r"(s\d+o)(\d+):\1(\d+)", reference)
        if match:
            first, last = int(match[2]), int(match[3])
            # Unknown endpoints must not allocate an unbounded range before
            # candidate-local existence is checked.
            if first < last and last - first + 1 <= len(local):
                keys = [f"{match[1]}{index}" for index in range(first, last + 1)]
    if not keys or len(keys) < 2 or reference != f"{keys[0]}:{keys[-1]}" or any(key not in local for key in keys):
        return None
    parsed = [re.fullmatch(r"(q|s\d+o)(\d+)", key) for key in keys]
    if any(match is None for match in parsed):
        return None
    if len({match[1] for match in parsed}) != 1 or [int(match[2]) for match in parsed] != list(
            range(int(parsed[0][2]), int(parsed[0][2]) + len(keys))):
        return None
    values = [local[key] for key in keys]
    origins = {(value["source"], value.get("source_context_index")) for value in values}
    if len(origins) != 1:
        return None
    source, index = next(iter(origins))
    original = payload["question"] if source == "question" else payload["source_context"][index]["original_question"]
    text = "".join(value["text"] for value in values)
    if len(text) > 160 or text not in original or _NEW_QUESTION.search(text):
        return None
    return {**values[0], "text": text}


def source_quote_span(text, quote, *, start=None):
    """Normalize a quote and its offsets together; never change source bytes."""
    if not quote or not quote.strip():
        return None
    offset = text.find(quote) if start is None else start
    if offset < 0 or text[offset:offset + len(quote)] != quote:
        return None
    left = len(quote) - len(quote.lstrip())
    normalized = quote.strip()
    return {"text": normalized, "start": offset + left, "end": offset + left + len(normalized)}


def source_context_display(sources, *, original_only=False):
    """Only longstanding display text/status; new host metadata is not a prompt."""
    allowed = {"original_question", "original_question_complete", "source_context_status"}
    if not original_only:
        allowed.update({"context_before", "context_after"})
    return [{key: value for key, value in source.items() if key in allowed} for source in sources]


def primary_wire_payload(payload, *, original_only=False):
    # Send each question only once. Duplicating full text plus excerpts
    # consumes the conservative reservation needed for a retry.
    wire = [{**{key: value for key, value in item.items()
                if key not in {"question", "canonical_evidence_options", "task_evidence_options", "reference_evidence_options"}
                and not key.startswith("_")},
             "question": item["canonical_evidence_options"]} for item in payload]
    for item, candidate in zip(wire, payload):
        if "source_context" in item:
            # UUID provenance remains on host candidates and returned sources;
            # the model needs the verified text and status, not opaque IDs.
            item["source_context"] = source_context_display(item["source_context"], original_only=original_only)
            if "_reference_view" in candidate:
                for source in item["source_context"]:
                    source.pop("reference_frame", None)
                    source.pop("source_item_frame", None)
                # Only the scope's offered frame is visible. IDs, hashes and
                # spans stay on host; full antecedent text remains separately
                # labeled and cannot masquerade as the current raw question.
                for frame in candidate["_reference_view"].get("frames", []):
                    source_index = frame["current_source_context_index"]
                    if frame.get("frame_kind") == "SOURCE_ITEM_QUALIFIER":
                        item["source_context"][source_index]["source_item_frame"] = {
                            "frame_kind": "SOURCE_ITEM_QUALIFIER", "status": frame["status"], "semantic_resolution": "UNASSESSED",
                            "qualifiers": [{"slot": record["slot"], "qualifier_text": {
                                key: value["text"] for key, value in candidate.get("reference_evidence_options", {}).items()
                                if value["reference_slot"] == record["slot"]}} for record in frame.get("qualifiers", [])]}
                        continue
                    item["source_context"][source_index]["reference_frame"] = {
                        "frame_kind": "PRECEDING_REFERENCE", "status": frame["status"], "semantic_resolution": "UNASSESSED",
                        "antecedents": [{"slot": record["slot"], "original_question_complete": True,
                            "relation_provenance": record["relation_provenance"], "original_question": {
                                key: value["text"] for key, value in candidate.get("reference_evidence_options", {}).items()
                                if value["reference_slot"] == record["slot"]}}
                            for record in frame.get("antecedents", [])]}
            # Render source text once, with the same candidate-local IDs the
            # host will resolve. The authoritative payload keeps plain strings.
            for source_index, source in enumerate(item["source_context"]):
                if source.get("source_context_status") == "VERIFIED":
                    for field in ("original_question", "context_before", "context_after"):
                        if field in source:
                            source[field] = {key: value["text"] for key, value in candidate["task_evidence_options"].items()
                                             if value["source"] == field and value.get("source_context_index") == source_index}
    return wire


def evidence_choice_schema(schema, payload):
    choices = sorted({key for item in payload for key in item["canonical_evidence_options"]})
    for field in ("object_evidence", "focus_evidence"):
        schema["$defs"]["CandidateRank"]["properties"][field]["enum"] = ["", *choices]
    task_choices = sorted({key for item in payload for key, value in item["task_evidence_options"].items()
                           if value.get("task_authoritative") or (value["source"] in {"context_before", "context_after"}
                               and (not requires_verified_original(item) or verified_originals(item)))})
    schema["$defs"]["CandidateRank"]["properties"]["task_evidence"]["enum"] = ["", *task_choices]


def resolve_candidate_evidence(item, options, task_options=None, *, source_payload=None):
    resolved = {}
    for field in ("object_evidence", "focus_evidence"):
        value = getattr(item, field)
        # Literal canonical excerpts from older clients remain subject to the
        # existing exact-text checks. Unknown selection IDs cannot become text.
        resolved[field] = options.get(value, "") if re.fullmatch(r"q\d+", value) else value
    value = item.task_evidence
    if source_payload is not None and source_payload.get("_source_bound_ranges") and re.fullmatch(r"[qs]\d+.*", value):
        selected = resolve_task_option(value, source_payload, source_payload.get("construction_ranges"))
        resolved["task_evidence"] = selected["text"] if selected else ""
    elif re.fullmatch(r"q\d+|s\d+[oba]\d+", value):
        selected = (task_options or {}).get(value, {})
        allowed = (selected.get("source") in {"question", "original_question"} and
                   selected.get("task_authoritative", True)) or (
            item.candidate_task not in {"DESIGN_TASK", "ALGORITHM_TASK"} and
            selected.get("source") in {"context_before", "context_after"})
        resolved["task_evidence"] = selected.get("text", "") if allowed else ""
    return item.model_copy(update=resolved)
