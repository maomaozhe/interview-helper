"""Host-owned, source-bound semantic scope shared by search and verification.

Browsing labels and model rewrites are not authorization. Compile and validation
use the same local action/object bindings, including later corrections.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re

VERSION = "query_relevance_scope_v1"
CONTROL_MESSAGES = frozenset({"按题意查找候选", "全量分页", "按分类浏览", "按现有标签统计"})
_PAGE_CONTROL = re.compile(r"^(?:下一页|下页|继续(?:找|查找|查询)?|更多|再找(?:一些|一批)?|"
    r"(?:这类|这些|这批|同类)(?:题目?|问题)?(?:再|继续)(?:查找|查询|检索|搜索|找|查|搜)(?:一些|一批|更多)?|"
    r"重查|重新(?:检索|查询)|按(?:重要性|频次|频率|薄弱)(?:排序)?)[。！!？?]?$", re.I)
_BUSINESS = re.compile(r"(?:具体)?业务(?:应用|系统|功能|流程)(?:的)?(?:设计|架构|题)?|business\s+(?:applications?|systems?|workflows?)(?:\s+design)?", re.I)
_TARGET_CLEAR_PREFIX = re.compile(r"(?:不(?:只|仅)(?:看|要|给|限于)?|不限(?:定)?(?:于)?|不限定于|不再(?:只要|只看)|不是(?:只看|只要)?|而非|并非|非|不看|不要(?:只给|只看|只要|限定于?)?|不想要|不要求|不需要|无需|不用|不做|排除|取消|去掉)\s*$", re.I)
_TARGET_CLEAR_SUFFIX = re.compile(r"^\s*(?:以外|之外|不(?:再)?(?:限定|限制)|的限定.{0,4}(?:取消|去掉))", re.I)
_EN_TARGET_CLEAR = re.compile(r"(?:not\s+(?:only|just)|not\s+limited\s+to|exclude|without|no)\s*$", re.I)
_NEGATIVE_BUSINESS_OBJECT = re.compile(r"(?:不要|不看|排除|不包括|不包含|不含|exclude|without)\s*(?:(?!题|但|而|要|只|保留|包括|包含|include)[^，,。；;\n]){0,24}$", re.I)
_INDEPENDENT_POSITIVE = re.compile(r"(?:只要|仅要|限定|只看)\s*(?:独立|完整|整体|整套|端到端).{0,8}(?:系统|设计|架构)|\bindependent\s+(?:systems?|services?)\b", re.I)
_INDEPENDENT_CLEAR = re.compile(
    r"(?:不(?:再)?(?:要求|需要|限定|限制|必|用)|无须|无需|不要(?:再)?(?:限定|限制|只看|只要)|不只看|不仅要|取消|去掉)\s*(?:只看|只要|是)?\s*(?:独立|完整|整体|整套|端到端)(?:系统|设计|架构)?"
    r"|独立(?:系统)?\s*(?:不(?:是)?(?:必要|必须)|不是硬性要求)"
    r"|(?:(?:do\s+not|don['’]t)\s+(?:require|limit\s+to)|not\s+necessarily|not\s+only|not\s+limited\s+to|no\s+requirement\s+for|not|without|no)\s+(?:an?\s+)?independent\s+(?:systems?|services?)", re.I)
_NEGATED_INDEPENDENT_PREFIX = re.compile(r"(?:不|不要|不必|无需|不用|不要求|不限定|并非|非|not|without|no)\s*$", re.I)
_DOMAIN_PATTERNS = {
    "AI": r"(?<![A-Za-z])AI(?![A-Za-z])|人工智能|大模型",
    "AGENT": r"(?<![A-Za-z])Agents?(?![A-Za-z])|智能体",
    "MCP": r"(?<![A-Za-z])MCP(?![A-Za-z])",
}
_DOMAIN = "(?:" + "|".join(f"(?:{pattern})" for pattern in _DOMAIN_PATTERNS.values()) + ")"
_DOMAIN_RE = re.compile(_DOMAIN, re.I)
_OBJECT_SUFFIX = r"(?:\s*(?:(?:相关|具体)?业务(?:应用|系统|功能)|应用|系统|相关题|相关内容|题目|题|business\s+(?:applications?|systems?)|applications?|systems?))?"
_OBJECTS = _DOMAIN + _OBJECT_SUFFIX + r"(?:\s*(?:和|与|及|、|，|,|/|and\b|or\b|&)\s*" + _DOMAIN + _OBJECT_SUFFIX + r")*"
_ALLOW_ACTION = (
    r"(?:不(?:再|要)?(?:擅自|随意|随便|任意|自动)?排除|不要(?:再|继续|擅自|随意|随便|任意|自动)?排除|别(?:再|擅自|随意|随便)?排除"
    r"|不能排除|不应该排除|不应排除|不想排除|不需要排除|不用排除|不必排除|无需排除|无须排除|禁止排除"
    r"|(?:不要|别)(?:擅自|随意|任意)?[^，,。；;\n]{1,20}(?:或|和|以及)排除"
    r"|不要不(?:包括|包含|含)|别不(?:包括|包含|含)|保留|可以包含|允许|包含|包括|也要"
    r"|(?<![A-Za-z])(?:do\s+not|don['’]t|not|never)\s+(?:exclude|excluding)\b|(?<![A-Za-z])(?:include|including|allow)\b)"
)
_EXCLUDE_ACTION = (
    r"(?:排除|不含|不包含|不包括|不要|不看|别给|剔除|不涉及|禁止"
    r"|(?<![A-Za-z])(?:do\s+not|don['’]t|not)\s+include\b|(?<![A-Za-z])(?:exclude|excluding|without|no)\b)"
)
_PREFIX_ACTION = re.compile(r"(?P<allow>" + _ALLOW_ACTION + r")\s*(?:所有|任何|一切|the|all|any)?\s*(?P<allow_objects>" + _OBJECTS + r")"
    + r"|(?P<exclude>" + _EXCLUDE_ACTION + r")\s*(?:所有|任何|一切|the|all|any)?\s*(?P<exclude_objects>" + _OBJECTS + r")", re.I)
_SUFFIX_ACTION = re.compile(r"(?P<objects>" + _OBJECTS + r")\s*(?:也|都|仍然|仍|可以|也可以)?\s*(?:(?P<allow>不排除|不要排除|别排除|保留|要|包含|包括|允许|可接受|接受|可以|are\s+allowed|is\s+allowed)|(?P<exclude>不要|不看|排除|剔除|不接受|are\s+excluded|is\s+excluded))", re.I)
_QUESTION_ACTION = re.compile(r"为什么|为何|是否|是不是|会不会|你(?:怎么|为何)|\bwhy\b|\bwhether\b", re.I)
_BEHAVIOR_TAIL = re.compile(r"^\s*(?:[来去](?:决定|判断|生成|编造|改写|回答|筛选|控制|选择|帮我|替我|给我)|生成|编造|瞎编|胡编|杜撰|捏造|乱编|臆造|改写|回答|猜测|把|替我|帮我|给我|generated?\b|fabricat\w*\b|hallucinat\w*\b|make\s+up\b)", re.I)
_FIELDS = frozenset({"version", "target_unit", "source_messages", "target_evidence", "excluded_domains",
    "exclusion_evidence", "independent_only", "independence_evidence", "retrieval_mode", "scope_hash"})


def scope_digest(value):
    body = {key: item for key, item in value.items() if key != "scope_hash"}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()


def semantic_messages(messages):
    # A repeated correction is meaningful: exclude -> include -> exclude.
    return [text.strip() for text in messages if isinstance(text, str) and text.strip()
        and text.strip() not in CONTROL_MESSAGES and not _PAGE_CONTROL.fullmatch(text.strip())]


def _domain_name(text):
    return next(name for name, pattern in _DOMAIN_PATTERNS.items() if re.fullmatch(pattern, text, re.I))


def _clauses(message):
    # A comma directly followed by another domain is a possible object list;
    # it is not a license to bind an exclusion to arbitrary words after it.
    boundary = re.compile(r"[。；;！？!?\n]|[，,](?!\s*" + _DOMAIN + r")", re.I)
    start = 0
    for match in boundary.finditer(message):
        yield start, message[start:match.start()]
        start = match.end()
    yield start, message[start:]


def _interpret(messages):
    target, targets, independent, independence, excluded = "ANY_ENGINEERING", [], False, [], {}
    for index, message in enumerate(messages):
        for _, clause in _clauses(message):
            for match in _BUSINESS.finditer(clause):
                before, after = clause[:match.start()], clause[match.end():]
                clear = (_TARGET_CLEAR_PREFIX.search(before) or _EN_TARGET_CLEAR.search(before)
                         or _TARGET_CLEAR_SUFFIX.search(after))
                # Negating a qualified subdomain ("no AI business apps") is
                # neither a positive business target nor a cancellation of an
                # earlier business target. Domain actions are interpreted below.
                if not clear and _NEGATIVE_BUSINESS_OBJECT.search(before):
                    continue
                if clear and _BEHAVIOR_TAIL.search(after):
                    continue
                target = "ANY_ENGINEERING" if clear else "BUSINESS_APPLICATION"
                targets = [] if clear else [{"message_index": index, "quote": match.group()}]
            clear_independence = list(_INDEPENDENT_CLEAR.finditer(clause))
            events = [(match.start(), True, match.group()) for match in _INDEPENDENT_POSITIVE.finditer(clause)
                      if not _NEGATED_INDEPENDENT_PREFIX.search(clause[:match.start()])
                      and not any(clear.start() <= match.start() < clear.end() for clear in clear_independence)]
            events.extend((match.start(), False, match.group()) for match in clear_independence)
            for _, enabled, quote in sorted(events):
                independent = enabled
                independence = [{"message_index": index, "quote": quote}] if enabled else []
            actions = []
            for match in _PREFIX_ACTION.finditer(clause):
                if _QUESTION_ACTION.search(clause[:match.start()]):
                    continue
                enabled = match.group("allow") is not None
                objects = match.group("allow_objects") if enabled else match.group("exclude_objects")
                if not enabled and _BEHAVIOR_TAIL.search(clause[match.end():]):
                    # "Don't let AI fabricate answers" constrains provenance
                    # or assistant behavior, not the domain of stored questions.
                    continue
                actions.append((match.start(), enabled, objects, match.group()))
            for match in _SUFFIX_ACTION.finditer(clause):
                # Prefix actions own their span; do not reinterpret their suffix.
                if any(start <= match.start() < start + len(quote) for start, _, _, quote in actions):
                    continue
                actions.append((match.start(), match.group("allow") is not None,
                                match.group("objects"), match.group()))
            for _, allow, objects, quote in sorted(actions):
                for match in _DOMAIN_RE.finditer(objects):
                    domain = _domain_name(match.group())
                    if allow:
                        excluded.pop(domain, None)
                    else:
                        excluded[domain] = {"domain": domain, "message_index": index, "quote": quote}
    return {"target_unit": target, "target_evidence": targets, "independent_only": independent,
        "independence_evidence": independence, "excluded_domains": sorted(excluded),
        "exclusion_evidence": [excluded[key] for key in sorted(excluded)]}


def _evidence_valid(item, messages, *, domain=False):
    return (isinstance(item, dict) and set(item) == ({"domain", "message_index", "quote"} if domain else {"message_index", "quote"})
        and type(item.get("message_index")) is int and 0 <= item["message_index"] < len(messages)
        and isinstance(item.get("quote"), str) and 0 < len(item["quote"]) <= 2000
        and item["quote"] in messages[item["message_index"]])


def _evidence_matches(actual, expected):
    return len(actual) == len(expected) and all(any(
        item["message_index"] == reference["message_index"] and item.get("domain") == reference.get("domain")
        and reference["quote"] in item["quote"] for item in actual) for reference in expected)


def validate_relevance_scope(value):
    """Recompute authorization from original messages before accepting a hash."""
    if not isinstance(value, dict) or set(value) != _FIELDS or value.get("version") != VERSION:
        raise ValueError("RELEVANCE_SCOPE_INVALID")
    scope = deepcopy(value)
    messages = scope["source_messages"]
    if (not isinstance(messages, list) or not 1 <= len(messages) <= 24
            or any(not isinstance(message, str) or not 1 <= len(message) <= 2000
                   or message != message.strip() or message in CONTROL_MESSAGES or _PAGE_CONTROL.fullmatch(message)
                   for message in messages)
            or not isinstance(scope["target_unit"], str) or scope["target_unit"] not in {"BUSINESS_APPLICATION", "ANY_ENGINEERING"}
            or type(scope["independent_only"]) is not bool
            or not isinstance(scope["retrieval_mode"], str) or scope["retrieval_mode"] not in {"CANDIDATES", "UNDECIDED"}):
        raise ValueError("RELEVANCE_SCOPE_INVALID")
    targets, independence, exclusions, evidence = (scope[key] for key in
        ("target_evidence", "independence_evidence", "excluded_domains", "exclusion_evidence"))
    if (not all(isinstance(value, list) for value in (targets, independence, exclusions, evidence))
            or any(not isinstance(domain, str) or domain not in _DOMAIN_PATTERNS for domain in exclusions)
            or exclusions != sorted(set(exclusions))
            or any(not _evidence_valid(item, messages) for item in [*targets, *independence])
            or any(not _evidence_valid(item, messages, domain=True) for item in evidence)):
        raise ValueError("RELEVANCE_SCOPE_INVALID")
    expected = _interpret(messages)
    if scope["target_unit"] != expected["target_unit"] or not _evidence_matches(targets, expected["target_evidence"]):
        raise ValueError("RELEVANCE_SCOPE_TARGET_UNGROUNDED")
    if scope["independent_only"] != expected["independent_only"] or not _evidence_matches(independence, expected["independence_evidence"]):
        raise ValueError("RELEVANCE_SCOPE_INDEPENDENCE_UNGROUNDED")
    if exclusions != expected["excluded_domains"] or not _evidence_matches(evidence, expected["exclusion_evidence"]):
        raise ValueError("RELEVANCE_SCOPE_EXCLUSION_UNAUTHORIZED")
    if not isinstance(scope["scope_hash"], str) or re.fullmatch(r"[0-9a-f]{64}", scope["scope_hash"]) is None or scope["scope_hash"] != scope_digest(scope):
        raise ValueError("RELEVANCE_SCOPE_HASH_MISMATCH")
    return scope


def compile_relevance_scope(messages, *, candidate_mode=False):
    messages = semantic_messages(messages)
    if not messages:
        return None
    result = _interpret(messages)
    if result["target_unit"] == "ANY_ENGINEERING" and not result["excluded_domains"] and not result["independent_only"] and not candidate_mode:
        return None
    result.update(version=VERSION, source_messages=messages, retrieval_mode="CANDIDATES" if candidate_mode else "UNDECIDED")
    result["scope_hash"] = scope_digest(result)
    return validate_relevance_scope(result)
