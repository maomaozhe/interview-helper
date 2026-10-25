"""Source-bound applied-scenario tasks, independent of published topic/type labels."""
from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, StrictBool

from interview_intelligence.contracts import StrictModel
from interview_intelligence.search.citations import (
    authoritative_task_options, continuous_task_ranges, primary_candidate_payload, resolve_task_option,
)


SCENARIO_CONTRACT = "scenario_source_task_v1"


def scenario_scope_required(query: str) -> bool:
    """Select a contract from the user's task, never from a candidate's labels."""
    if re.search(r"场景(?:概念|原理)题|(?:介绍|解释|有哪些).{0,8}应用场景|"
                 r"(?:不要|排除|除了|不限定|不只|不仅).{0,5}场景|"
                 r"场景.{0,8}(?:和|与|及|或).{0,6}(?:原理|概念|知识)|"
                 r"(?:原理|概念|知识).{0,8}(?:和|与|及|或).{0,6}场景", query):
        return False
    return bool(re.search(r"(?:场景|实战|实际应用).{0,10}(?:题|问题|案例)|"
                          r"(?:故障排查|故障诊断).{0,8}(?:题|问题|案例)|"
                          r"\b(?:scenario|troubleshooting|applied)\s+(?:questions?|problems?|cases?)\b", query, re.I))


class ScenarioVerification(StrictModel):
    candidate_id: str
    keep: StrictBool
    reason: str = Field(min_length=1, max_length=140)
    scenario_task: Literal["DIAGNOSIS", "SOLUTION", "DESIGN", "TRADEOFF", "CONCEPT", "UNKNOWN"]
    situation_evidence: str = Field(max_length=160)
    application_evidence: str = Field(max_length=160)


class ScenarioScopeResult(StrictModel):
    verifications: list[ScenarioVerification] = Field(min_length=1, max_length=50)


SCENARIO_PROOF_INSTRUCTIONS = (
    "当前原query要场景/实战任务，不能以技术名、故障名、旧SCENARIO标签或KNOWLEDGE标签代替任务证据。"
    "场景需当前题已给应用条件、异常、运行约束或可检查目标，并要求诊断、处置、构造、验证或方案取舍。"
    "不要求很长的故事或具体公司：消息积压如何处理、如何保证消息不丢失、缓存击穿如何解决，均有明确运行任务。"
    "只问概念含义/区别、现有机制原理/内部如何实现，即使提到高并发、延迟消息、缓存故障，也不是实际场景任务。"
    "混合题若同一原问确实要求解决/诊断/设计，不能因含原理二字或上游KNOWLEDGE而排除。"
    "本轮schema优先：每项必须包含candidate_id、keep、reason、scenario_task、situation_evidence、application_evidence。"
    "scenario_task为DIAGNOSIS/SOLUTION/DESIGN/TRADEOFF/CONCEPT/UNKNOWN；纯解释用CONCEPT，证据不足UNKNOWN。"
    "两段证据填写本题scenario_options中的片段ID，不抄写、不拼接；前者证明给定情境/异常/约束，后者证明实际所需任务。"
    "两项可以选同一完整短句，但必须来自同一原问；不能跨occurrence组合或借邻题的解决任务。"
    "原问存在时优先选s编号；scenario_options已排除不被原问支持的标准题改写。"
    "动作与对象跨标点时可选scenario_ranges中的连续范围编号，不自行拼接；只有‘场景：’或组件名不能证明任务。"
    "VERIFIED原问比标准题改写优先；原问只是‘这三个是什么意思’不能借上一题的‘如何解决’。"
    "纯概念拒绝时application_evidence引用本题解释要求，situation_evidence可空；UNKNOWN留空并keep=false。"
    "不得把可能的用途、答案中的实现方案或自己想象的故障补成题面已有事实。"
)


def scenario_candidate_payload(candidates):
    payload = primary_candidate_payload(candidates)
    for item in payload:
        local = authoritative_task_options(item)
        ranges = continuous_task_ranges(item, local, _complete_applied_unit)
        item["scenario_ranges"] = ranges
        item["scenario_options"] = [key for key, value in local.items()
                                    if _given_situation(value["text"]) or _applied_request(value["text"]) or
                                    _CONCEPT.search(value["text"])] + list(ranges)
    return payload


def scenario_evidence_schema(schema, payload):
    fields = schema["$defs"]["ScenarioVerification"]["properties"]
    for field in ("situation_evidence", "application_evidence"):
        choices = set()
        for item in payload:
            for key in item["scenario_options"]:
                value = resolve_task_option(key, item, item["scenario_ranges"])
                if value and (field == "situation_evidence" and _given_situation(value["text"]) or
                              field == "application_evidence" and
                              (_applied_request(value["text"]) or _CONCEPT.search(value["text"]))):
                    choices.add(key)
        fields[field]["enum"] = ["", *sorted(choices)]


def _task_texts(candidate):
    originals = [s.get("original_question", "") for s in candidate.get("source_context", [])
                 if s.get("source_context_status") == "VERIFIED" and s.get("original_question", "").strip()]
    return originals or [candidate.get("canonical_text", "")[:500]]


_CONCEPT = re.compile(r"原理|概念|含义|是什么意思|是什么|区别|底层|内部|机制|设计思想|设计理念|\b(?:principle|definition|meaning|difference)\b", re.I)
_APPLIED = re.compile(r"解决|排查|诊断|定位|避免|防止|应对|优化|验证|选型|取舍|怎么办|"
                      r"(?:如何|怎么|怎样)(?:去)?(?:保证|保障|确保|选择|选|做|拆分?|分片|查找|找到|找回|恢复|兼顾|权衡|协调|调度|去重|打包|复用)|"
                      r"(?:哪些|什么|何种)(?:策略|措施|方案).{0,12}(?:保证|保障|确保|处理)|"
                      r"\b(?:diagnose|troubleshoot|mitigate|design|prevent|resolve)\b", re.I)
_CONSTRUCTION = re.compile(r"(?:如何|怎么|怎样|请|让你|要你|要求|需要)(?:去|来)?设计(?!原理|思想|理念|模式)|"
                           r"(?:设计|构建|搭建|开发|实现)(?:一个|一套|一种|某个)|"
                           r"^\s*设计[^，。？！；,?!;\n]{1,40}(?:系统|方案|组件|服务|机制|策略|流程|功能)")
_CONDITION = re.compile(r"如果|假设|遇到|出现|发生|失败|故障|异常|超时|丢失|丢消息|积压|重复|宕机|"
                        r"穿透|击穿|雪崩|死锁|不一致|不均衡|倾斜|过载|瓶颈|热点|不丢|并发|有限|限制|要求|必须|不能|不得|需要|"
                        r"\b(?:given|when|failure|timeout|duplicate|must|limited)\b", re.I)
_EXPLANATION_FRAME = re.compile(r"(?:介绍|解释|讲述|讲讲|说说|描述|阐述|说明).{0,120}"
                                r"(?:现有|已有|机制|工作方式|实现过程|运行方式)")


def _applied_request(text):
    # "底层如何处理/实现" explains an existing mechanism. A separate actual
    # handling request remains valid even when a different clause asks a concept.
    clauses = re.split(r"[，。？！；,?!;\n]|(?:并且|并|然后|再|之后|后)(?=(?:设计|实现|构建|搭建|开发|遇到|如果|假设|排查|诊断))", text)
    hypothesis = any(re.search(r"如果|假设|一旦", clause) and not _CONCEPT.search(clause)
                     for clause in clauses)
    # A proposed failure chain is also a given scenario; merely asking whether
    # a phenomenon exists, without a condition or causal chain, is insufficient.
    hypothesis = hypothesis or bool(re.search(r"(?:会不会|是否会).{0,8}(?:情况|问题)", text) and
                                    re.search(r"导致|致使|从而", text) and _CONDITION.search(text))
    for clause in clauses:
        # Existing-mechanism explanations do not become application tasks by
        # containing verbs such as 'guarantee' or 'design'. A separate explicit
        # incident/construction clause can still establish a mixed task.
        if re.search(r"(?:底层|内部|原理|设计思想|设计理念)", clause) or _EXPLANATION_FRAME.search(clause):
            continue
        if _APPLIED.search(clause) or _CONSTRUCTION.search(clause):
            return True
        if hypothesis and re.search(r"会不会|是否会|是不是|(?:是否|还|仍)有必要|是否(?:还|仍)?需要", clause):
            return True
        if re.search(r"(?:如何|怎么|怎样)(?:处理|实现)", clause):
            if _CONDITION.search(clause) or not _CONCEPT.search(text):
                return True
        if re.search(r"(?:实现|做)(?:一个|一套)", clause):
            return True
    return False


def _given_situation(text):
    return bool(_CONDITION.search(text) or _applied_request(text) or
                re.search(r"(?:在|当)[^，。？！；,?!;\n]{2,60}(?:时|期间)|[^，。？！；,?!;\n]{2,40}(?:之后|以后)", text))


def _complete_applied_unit(text):
    # A range must add a target/condition to a bare handling question; an
    # already self-contained task needs no duplicate range in the request.
    bare = re.fullmatch(r"\s*(?:你|您)?(?:会)?(?:如何|怎么|怎样)(?:去)?(?:处理|解决|做|实现|保证|拆分?|恢复|查找|找到|兼顾)[？?。！!\s]*", text)
    return bool(_applied_request(text) and not bare)


def concept_only_evidence(candidate):
    """Conservative negative proof from actual question form, not KNOWLEDGE.

    A topic label or a scenario noun cannot produce this verdict. Mixed applied
    requests and ambiguous fragments are deliberately left for source review.
    """
    originals = [s for s in candidate.get("source_context", [])
                 if s.get("source_context_status") == "VERIFIED" and s.get("original_question", "").strip()]
    # Production source context may be a byte-capped prefix. Neither VERIFIED
    # identity nor a canonical rewrite proves that the missing tail contains no
    # application task. Old producers lacking the completeness flag are unknown.
    if not originals or any(s.get("original_question_complete") is not True for s in originals):
        return None
    texts = [s["original_question"] for s in originals]
    if texts and all(text.strip() and (_CONCEPT.search(text) or _EXPLANATION_FRAME.search(text))
                     and not _applied_request(text) for text in texts):
        return {"status": "REJECT", "host_reason": "CONCEPT_EXPLANATION_WITHOUT_APPLIED_TASK",
                "source_quotes": texts, "source_basis": "verified_complete_original"}
    return None


def scenario_proof_assessment(candidate, verification, payload):
    """Prove both claims in one authoritative question; missing claims stay unknown."""
    def result(status, reason, invalid=(), **extra):
        return {"grounded": status == "PASS", "status": status, "host_reason": reason,
                "invalid_evidence_fields": list(invalid), **extra}

    negative = concept_only_evidence(candidate)
    if negative:
        return result("REJECT", negative["host_reason"], source_quotes=negative["source_quotes"])
    if verification is None:
        return result("UNKNOWN", "SCENARIO_PROOF_MISSING", ["scenario_task", "situation_evidence", "application_evidence"])
    if verification.scenario_task == "UNKNOWN":
        return result("UNKNOWN", "SCENARIO_TASK_UNRESOLVED", ["scenario_task"])
    options = payload["task_evidence_options"]
    claims = {field: (resolve_task_option(getattr(verification, field), payload, payload.get("scenario_ranges"))
                      if ":" in getattr(verification, field) else options.get(getattr(verification, field)))
              for field in ("situation_evidence", "application_evidence")}
    if verification.scenario_task == "CONCEPT":
        task = claims["application_evidence"]
        # A model's negative label is no stronger than its positive label. Pure
        # concept forms were already rejected above from the authoritative text.
        if task and task["source"] in {"question", "original_question"}:
            return result("UNKNOWN", "CONCEPT_CLASSIFICATION_NOT_ESTABLISHED", ["scenario_task"])
        return result("UNKNOWN", "CONCEPT_REJECTION_EVIDENCE_MISSING", ["application_evidence"])
    invalid = [field for field, claim in claims.items() if not claim or claim["source"] not in {"question", "original_question"}]
    if invalid:
        return result("UNKNOWN", "SCENARIO_EVIDENCE_NOT_GROUNDED", invalid)
    origins = {(v["source"], v.get("source_context_index")) for v in claims.values()}
    if len(origins) != 1:
        return result("UNKNOWN", "SCENARIO_CLAIMS_FROM_DIFFERENT_QUESTIONS", list(claims))
    origin, index = next(iter(origins))
    if origin == "question":
        # A canonical rewrite may combine source questions. With verified raw
        # forms available, both selected excerpts must be supported by one form.
        texts = _task_texts(candidate)
    else:
        texts = [candidate["source_context"][index]["original_question"]]
    texts = [text for text in texts if all(v["text"] in text for v in claims.values())]
    if not texts:
        return result("UNKNOWN", "SCENARIO_CANONICAL_NOT_SUPPORTED_BY_ONE_ORIGINAL", list(claims))
    if not any(_applied_request(text) for text in texts):
        return result("UNKNOWN", "APPLIED_TASK_NOT_ESTABLISHED", ["application_evidence"])
    situation = claims["situation_evidence"]["text"]
    task = claims["application_evidence"]["text"]
    # A construction/choice task can itself name its concrete delivery object;
    # explicit incidents/requirements otherwise establish the applied setting.
    if not _given_situation(situation):
        return result("UNKNOWN", "GIVEN_SITUATION_NOT_ESTABLISHED", ["situation_evidence"])
    if not _applied_request(task):
        return result("UNKNOWN", "SELECTED_QUOTE_HAS_NO_APPLIED_TASK", ["application_evidence"])
    return result("PASS", "GROUNDED_APPLIED_SCENARIO_TASK", situation_evidence=situation,
                  application_evidence=task, source_origin={"source": origin, "source_context_index": index})
