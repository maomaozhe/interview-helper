"""Ground the claimed independent design unit in the current question."""
import re
from typing import Literal

from pydantic import Field, PrivateAttr, StrictBool

from interview_intelligence.contracts import StrictModel
from interview_intelligence.search.candidate_prompt import requires_verified_original
from interview_intelligence.search.citations import (
    authoritative_task_options, continuous_task_ranges, primary_candidate_payload, resolve_task_option, source_quote_span,
)


class DesignVerification(StrictModel):
    _evidence_origins: dict = PrivateAttr(default_factory=dict)
    _invalid_design_references: set = PrivateAttr(default_factory=set)
    candidate_id: str
    keep: StrictBool
    reason: str = Field(min_length=1, max_length=140)
    design_unit: Literal["NEW_OR_REBUILT_SYSTEM", "COUPLED_MECHANISM", "COMPONENT_OR_WORKFLOW", "INTERNAL_COMPONENT", "LOCAL_SUBTASK", "EXISTING_PROJECT", "UNKNOWN"]
    construction_evidence: str = Field(max_length=160)
    constraint_a: str = Field(max_length=120)
    constraint_b: str = Field(max_length=120)


class DesignScopeResult(StrictModel):
    verifications: list[DesignVerification] = Field(min_length=1, max_length=50)


DESIGN_PROOF_INSTRUCTIONS = (
    "本轮独立设计复核schema优先：每项还必须输出design_unit、construction_evidence、constraint_a、constraint_b。"
    "NEW_OR_REBUILT_SYSTEM仅用于当前题明确提出设计/新建/构建/开发/重构/改造成某个交付对象，"
    "或要做/做一个/从头做网站、网页、系统、服务、功能等明确新建交付要求；"
    "construction_evidence必须连续引用同时包含建设要求和目标的原句，不能只摘对象名或规模条件。"
    "建设动词不证明独立设计单位。NEW_OR_REBUILT_SYSTEM可交付业务系统、独立基础服务或独立工程机制；"
    "只要当前题已明确独立建设目标，该分支不另外要求业务流程、存储、一致性等同时齐全。"
    "INTERNAL_COMPONENT必须由题面明确限定从属现有运行时/父容器，不能自行猜测题目是进程内组件。"
    "例如设计某个执行器内部的数据结构、缓冲队列或一个处理环节，是INTERNAL_COMPONENT，keep=false。"
    "直接要求设计一个支持并发读写的缓存或可扩展的服务，没有父容器限制，不得因功能像组件就排除。"
    "例如设计进程内执行器的工作缓冲队列，是内部组件；设计独立延迟消息服务，是基础服务。"
    "不能将内部队列的缓冲、调度、并发处理等可能实现要求补成题面已有的独立服务或约束闭环。"
    "仅问已有系统如何扩容、拆服务或优化一个环节，没有新的独立建设目标时是LOCAL_SUBTASK。"
    "COUPLED_MECHANISM用于没有显式建设动词但给定整体机制目标及至少两个相互约束的状态、恢复、资源或服务要求；"
    "construction_evidence引用该整体目标，constraint_a/b各引用一个不同且不重叠的要求，不能把同一句拆成规模和操作充数。"
    "constraint_a/b必须是题面给定的状态、资源限制或可观察的行为要求；仅列出锁、队列、防刷等待讨论组件不能充数。"
    "例如只给大量连接并问服务拆分，只有规模条件与局部操作，不是两个相互约束的机制要求。"
    "给定有限内存、结果依赖资源是否变化，要求兼顾并发与延迟，可构成COUPLED_MECHANISM。"
    "同一明确业务对象需要更新状态且满足并发/容量要求，并要求数据组织或存储选型，属于COUPLED_MECHANISM；"
    "其建设目标证据引用该对象的整体任务。即使问法是如何保证、数据结构如何设计、数据库如何选型，也不是单一局部追问。"
    "当前VERIFIED原问明确标为场景题且含上述整体任务时，不再要求题面额外出现新建整个系统的措辞。"
    "冷热分层仍要查冷数据，或传输中断后恢复且持久存储容量受限，也属于整体约束闭环，不能仅因改造已有设施排除。"
    "既有项目实现复述是EXISTING_PROJECT；无法证明独立交付单位用UNKNOWN；这些与LOCAL_SUBTASK、INTERNAL_COMPONENT均keep=false。"
    "当前VERIFIED原问明确标注代码题、编程题或手写/手撕时，任务仍是编码；不能因标准题写了设计系统就升级为架构题。"
    "证据只能摘当前question或该题VERIFIED original_question，不能借邻题补出建设目标；没有证据填空串。"
    "NEW_OR_REBUILT_SYSTEM的constraint_a/b可以空；COUPLED_MECHANISM不能空。"
    "本轮证据选择契约优先：question与VERIFIED原问按片段编号显示，construction_evidence填写其中本题的一个编号；"
    "constraint_a/b仅从本题constraint_options选两个不同编号，不重新抄写、不拼接、不改标点。"
    "constraint_options已限定为题面给定状态、资源或行为要求，不能选仅列实现手段的子问；"
    "COUPLED_MECHANISM应分开选两条不重叠的要求，不将多个约束拼成一段。"
    "没有可用证据则留空并keep=false，不能补造目标或条件；编号只在当前candidate内有效。"
    "若目标与建设动作跨标点，可选construction_ranges提供的同题连续范围编号；不能跨原问或借邻题拼接。"
    "存在VERIFIED原问时，建设目标和约束必须由同一原问支持；标准题不能新增任务，改写不一致时选原问编号。"
)


CONSTRUCTION_ACTION = re.compile(
    r"设计|新建|构建|搭建|开发|重构|改(?:造)?成|换成|转换(?:成|为)|替换(?:成|为)|重新实现|实现一个|实现一套|实现.*系统|实现.*服务|"
    r"(?:要(?:你|您)?做(?!到|过|好)|做(?:一个|一套|一项|一种)|从头(?:开始)?做)\s*[^，。？！；：,?!;:\n\s][^，。？！；：,?!;:\n]{0,80}|"
    r"(?:需要|要)(?:你|您)?搭(?:建)?(?:什么|哪些)|"
    r"\b(?:design|build|develop|rebuild|rearchitect|architect|refactor|convert|replace)\b", re.IGNORECASE)

ATTACHED_DELIVERY = re.compile(
    r"(?:设计|开发|构建|搭建|实现|重构)\s*(?:一个|一套|某个)?\s*"
    r"([^，。？！；：,?!;:\n\s]*(?:系统|服务|框架|容器|组件|进程|线程池|执行器|处理器|对象|数据库|类))的"
    r"([^，。？！；：,?!;:\n]+)")
GIVEN_CONSTRAINT = re.compile(
    r"已知|给定|有限|受限|不足|不够|不得|不能|必须|要求|支持|需要|允许|不允许|优先|"
    r"未变化|不变化|不变|一致(?!性)|相同|不同|过期|重复|同时|最多|至少|至多|不超过|"
    r"保证|实时|高并发|高吞吐|低延[时迟]|高\s*QPS|可扩展|中断|断[联连]|丢[失数]|过大|超[出限]|仍然|可靠|幂等|"
    r"\d|\bN\b|\b(?:must|known|limited|at most|at least|unchanged)\b", re.IGNORECASE)
CODE_TASK = re.compile(r"代码题|编程题|手[写撕]|\bcoding\b", re.IGNORECASE)
DESIGN_QUERY = re.compile(
    r"设计(?!模式|原理)|架构(?:题|设计)|工程(?:场景)?题|工程(?:实现|开发)|"
    r"\b(?:system design|engineering design|design task)\b", re.IGNORECASE)
INDEPENDENT_QUERY = re.compile(
    r"(?<!不要求)(?<!不必)(?<!无需)(?<!不用)(?<!不限定)独立(?:的)?(?:系统|服务|设计|架构)|"
    r"主设计(?:题|任务)|(?:只要|仅要|限定|只看)(?:完整|整体|整套|端到端)(?:的)?(?:系统|架构)|"
    r"(?:排除|不要|不含)(?:局部|内部|组件|子任务)|\bindependent (?:system|service|design)\b", re.IGNORECASE)
DELIVERY_TASK = re.compile(
    r"(?:如何|怎么|怎样|何种|什么).{0,24}(?:实现|保证|避免|防止|恢复|隔离|协调|调度|部署|拆分?|兼顾|权衡|处理|安排|组织|选型|做(?!到|过|好)|打包|去重)|"
    r"(?:实现|保证|避免|防止|恢复|隔离|协调|调度|部署|拆分?|兼顾|权衡|处理|安排|组织|选型|做(?!到|过|好)|打包|去重).{1,60}(?:如何|怎么|怎样)|"
    r"\bhow (?:would|could|can|to)\b.{0,40}\b(?:implement|ensure|prevent|recover|isolate|coordinate|schedule)\b",
    re.IGNORECASE)
HISTORY_TASK = re.compile(r"(?:介绍|讲讲|描述|回顾|复述).{0,30}(?:做过|已有|现有|之前|项目)|(?:做过|用过).{0,50}[吗么]")
NEW_REQUEST = re.compile(r"如果|假如|重新|重做|重构|从头|(?:然后|随后|再).{0,8}(?:设计|构建|搭建|开发|新建)")
_TASK_BOUNDARY = re.compile(r"[，。？！；,?!;\n]|(?:并且|并|然后|再|之后|后)(?=(?:设计|实现|构建|搭建|开发|新建|重构|如何|怎么|怎样))")
_EXPLANATORY = re.compile(r"原理|设计模式|设计思想|设计理念|工作机制|底层机制|"
                          r"(?:介绍|解释|讲述|讲讲|说明).*(?:现有|已有|内部).*机制")


def design_scope_required(query):
    """Recognize an explicit user design request, never a candidate topic."""
    return bool(DESIGN_QUERY.search(query or ""))


def design_scope_policy(query):
    # A model's INDEPENDENT_SYSTEM_DESIGN label is not user authorization to
    # exclude components. Only an explicit narrowing in the query does that.
    if query is None:
        return "INDEPENDENT_SYSTEM"
    for match in INDEPENDENT_QUERY.finditer(query):
        if not re.search(r"(?:不要|不只要|不是只要)\s*$", query[:match.start()]):
            return "INDEPENDENT_SYSTEM"
    return "ENGINEERING_DELIVERY"


ENGINEERING_PROOF_INSTRUCTIONS = (
    "本轮为宽工程交付复核，不额外要求独立系统或完整架构；用户原query的对象与排他条件仍必须满足。"
    "每项必须输出design_unit、construction_evidence、constraint_a、constraint_b。"
    "NEW_OR_REBUILT_SYSTEM表示当前题要求新建或改造系统/应用/服务；"
    "COMPONENT_OR_WORKFLOW表示当前题要求构造、实现、改造组件、机制或工作流，"
    "也包括明确业务行为/可靠性目标下要求给出实施方案；不因属于某系统内部或只有一个可检查目标而拒绝。"
    "直接要求做一个新对象、如果要你做某项能力等口语指令同样有效，不限制对象名称必须含系统/服务。"
    "只有规模名、概念含义、现有原理介绍、是否做过或复述已有项目，不能成为新的交付任务。"
    "construction_evidence必须选当前题中同时支持目标和建设/实施任务的连续片段；裸‘怎么实现’或对象名不足。"
    "NEW_OR_REBUILT_SYSTEM与COMPONENT_OR_WORKFLOW不要求两个约束，constraint_a/b留空即可；"
    "不要为明确构造任务强选COUPLED_MECHANISM并编造第二个约束。"
    "COUPLED_MECHANISM仅在没有明确建设指令、但同一题已有整体目标与两个相互约束要求时使用；"
    "两条constraint必须同题、非空、不重叠且为原文给定的状态/资源/行为要求，不能列组件名充数。"
    "EXISTING_PROJECT、UNKNOWN以及没有明确交付目标的LOCAL_SUBTASK/INTERNAL_COMPONENT不作为正例。"
    "本轮宽工程范围允许代码形式表达的明确系统/服务/组件/接口交付；仍须本题自身有工程构造目标。"
    "纯算法或数据结构求解、只有写代码要求、原理或项目复述不因此成为工程交付；标准题不能补出构造目标。"
    "证据仅选本题question或VERIFIED original_question，不能借邻题、别的occurrence或未核验上下文补任务。"
    "construction_evidence填construction_options中的一个编号；constraint_a/b仅在COUPLED_MECHANISM时"
    "从constraint_options选择两个不同编号；不重新抄写、不拼接、不改写，缺证据则keep=false。"
    "目标与动作跨标点时，可选construction_ranges给出的连续范围编号；所有片段来自同一当前题，不能跨occurrence。"
    "存在VERIFIED原问时，建设目标和约束必须由同一原问支持；标准题不能新增任务，改写不一致时选原问编号。"
)


def design_scope_instructions(query):
    return DESIGN_PROOF_INSTRUCTIONS if design_scope_policy(query) == "INDEPENDENT_SYSTEM" else ENGINEERING_PROOF_INSTRUCTIONS


def _explicit_code_task(text):
    # A tool used to build the requested system is not the requested answer
    # format. Retain explicit coding tasks, including "AI coding题".
    text = re.sub(r"(?:使用|借助|用)\s*AI\s+coding\b(?!\s*(?:题|test|exercise|challenge))", "", text, flags=re.IGNORECASE)
    return bool(CODE_TASK.search(text))


def _construction_directive(text):
    if HISTORY_TASK.search(text) and not NEW_REQUEST.search(text):
        return False
    if re.fullmatch(r"\s*(?:你|您)?(?:会)?(?:如何|怎么|怎样)(?:去)?(?:设计|构建|搭建|开发|重构|实现)[？?。！!\s]*", text):
        return False
    clauses = re.split(r"[，。？！；,?!;\n]|(?:并且|并|然后|再|之后|后)(?=(?:设计|实现|构建|搭建|开发|新建))", text)
    return any(_affirmative_construction(clause) for clause in clauses if not re.search(
        r"(?:介绍|解释|讲述|讲讲|说明).*(?:原理|现有.*机制|内部.*机制)", clause))


def _affirmative_construction(clause):
    """Construction syntax is not affirmative when negated or explicitly past."""
    clause = re.sub(r"设计(?:模式|原理|思想|理念)", "", clause)
    for action in CONSTRUCTION_ACTION.finditer(clause):
        prefix = clause[:action.start()]
        if re.search(r"(?:不要|不要求|不需要|无需|不必|不用|别|并非|不是|不让)"
                     r"(?:你|您|再|去|来|要|进行|负责|尝试|重新|从头|\s){0,6}$", prefix):
            continue
        if re.search(r"(?:当时|之前|曾经|已经|那时|过去|去年).{0,30}$", prefix) and not re.search(
                r"(?:如果|假如|假设|若|现在).{0,30}$", prefix):
            continue
        return True
    return False


def _bare_delivery_action(text):
    return bool(re.fullmatch(
        r"\s*(?:你|您)?(?:会)?(?:如何|怎么|怎样)(?:去)?(?:设计|构建|搭建|开发|实现|保证|避免|防止|恢复|隔离|协调|调度|部署|拆分?|兼顾|权衡|处理|做|打包|去重)[？?。！!\s]*", text))


def _delivery_task(text):
    if _construction_directive(text):
        return True
    # The model must still attest a COMPONENT_OR_WORKFLOW task. This check
    # validates a current implementation request. An explanation in a different
    # clause must not erase an actual subsequent implementation task.
    return bool(not HISTORY_TASK.search(text) and not _bare_delivery_action(text) and any(
        (DELIVERY_TASK.search(clause) or re.search(r"(?:如何|怎么|怎样)优化", clause)) and not _EXPLANATORY.search(clause)
        for _, _, clause in _task_clauses(text)))


def _task_clauses(text):
    start = 0
    for match in _TASK_BOUNDARY.finditer(text):
        if match.start() > start:
            yield start, match.start(), text[start:match.start()]
        start = match.end()
    if start < len(text):
        yield start, len(text), text[start:]


def _evidence_positions(verification, field, value, source_index, text, has_originals):
    origin = verification._evidence_origins.get(field)
    if origin and (origin["source"] == "original_question" or not has_originals):
        if origin["source"] == "original_question" and origin["source_context_index"] != source_index:
            return []
        start, end = origin["start"], origin["end"]
        return [(start, end)] if text[start:end] == value else []
    return [(match.start(), match.end()) for match in re.finditer(re.escape(value), text)] if value else []


def _goal_context_task(verification, records, has_originals, *, coupled=False):
    """Validate the selected goal's own raw clause, never another clause's task."""
    goal = verification.construction_evidence
    for source_index, text, complete in records:
        for start, end in _evidence_positions(verification, "construction_evidence", goal,
                                              source_index, text, has_originals):
            # A trailing comma belongs to the selected excerpt, not the next
            # task. Ignore it when choosing the smallest supporting clause(s).
            end = start + len(goal.rstrip("，。？！；：,?!;:\n\r\t "))
            clauses = [(a, b, part) for a, b, part in _task_clauses(text) if a < end and b > start]
            if not clauses:
                continue
            if complete is False and clauses[-1][1] == len(text):
                continue  # a clipped explanation suffix could change this clause
            context = text[clauses[0][0]:clauses[-1][1]]
            if _delivery_task(context):
                return True
            # COUPLED may name an observable requirement rather than repeat an
            # imperative. Its two independent same-question constraints remain
            # mandatory; a principle description cannot supply this goal.
            if coupled and not _EXPLANATORY.search(context) and not HISTORY_TASK.search(context) and re.search(
                    r"需要|要求|必须|保证|支持|不得|不能|允许", context):
                return True
    return False


def engineering_coding_task_allowed(candidate, *, query, evidence=None):
    """Broad engineering may include coding form, only with a source-local deliverable.

    This permits entering design scope verification, never a standalone relevance
    verdict. Pure algorithm/data-structure solving still lacks this delivery proof.
    """
    if design_scope_policy(query) != "ENGINEERING_DELIVERY" or re.search(r"架构|\barchitecture\b", query or "", re.I):
        return False
    originals = [s.get("original_question", "") for s in candidate.get("source_context", [])
                 if s.get("source_context_status") == "VERIFIED" and s.get("original_question")]
    texts = originals or [candidate.get("canonical_text", "")[:500]]
    for text in texts:
        goal = text if evidence is None else evidence
        # A literal canonical substring can omit its original explanation frame.
        # Both the selected goal and this same original must request construction.
        if (not goal or goal not in text or not _construction_directive(goal)
                or not _construction_directive(text)):
            continue
        # A technical noun alone is insufficient: require a construction request
        # for an engineering unit, rather than an array/list/algorithm exercise.
        unit = r"(?:系统|服务|组件|接口|应用|平台|工作流|中间件)"
        target = re.search(r"(?:设计|构建|搭建|开发|新建|重构|实现|要做|做一个|从头做)"
                           r"[^，。？！；,?!;\n]{0,40}?" + unit + r"(?!中|内|的(?:算法|数据结构))", goal)
        target = target or re.search(unit + r"(?:该|要|应该)?(?:如何|怎么|怎样)(?:设计|构建|搭建|开发|实现)", goal)
        if target and not (
                HISTORY_TASK.search(text) and not NEW_REQUEST.search(text)):
            return True
    return False


def _attached_component(text):
    for match in ATTACHED_DELIVERY.finditer(text):
        parent, target = match.groups()
        if any(word in parent for word in ("支持", "面向", "具有", "满足", "覆盖", "包含")):
            continue
        if target.startswith(("整体", "完整", "总体")) or target.rstrip("。？！?！").endswith(("系统", "服务", "平台")):
            continue
        return True
    return False


def design_candidate_payload(candidates, *, original_only=False):
    payload = primary_candidate_payload(candidates, original_only=original_only)
    for item in payload:
        if original_only:
            item["_source_bound_ranges"] = True
        local = authoritative_task_options(item)
        item["construction_options"] = [key for key, value in local.items()
                                        if not _bare_delivery_action(value["text"])]
        item["constraint_options"] = [key for key, value in local.items() if GIVEN_CONSTRAINT.search(value["text"])]
        # A goal and its construction request can straddle punctuation. Offer
        # exact contiguous ranges from ONE question, without duplicating text
        # or merging canonical text with a different occurrence.
        ranges = continuous_task_ranges(item, local, _delivery_task)
        if original_only:
            # Referential current asks often end in a bare "how would you
            # implement?". Offer exact preceding spans even when an earlier
            # atomic clause already looks sufficient; never join occurrences.
            entries = list(local.items())
            for end, (last, value) in enumerate(entries):
                if not _bare_delivery_action(value["text"]):
                    continue
                origin = (value["source"], value.get("source_context_index"))
                for start in range(end - 1, -1, -1):
                    selected = entries[start:end + 1]
                    if any((entry["source"], entry.get("source_context_index")) != origin for _, entry in selected):
                        break
                    text = "".join(entry["text"] for _, entry in selected)
                    if len(text) > 160:
                        break
                    first = selected[0][0]
                    reference = f"{first}:{last}"
                    if resolve_task_option(reference, item, {reference: [key for key, _ in selected]}):
                        ranges[reference] = [key for key, _ in selected]
        item["construction_options"].extend(ranges)
        item["construction_ranges"] = ranges
    return payload


def design_evidence_schema(schema, payload):
    fields = schema["$defs"]["DesignVerification"]["properties"]
    fields["construction_evidence"]["enum"] = ["", *sorted({key for item in payload for key in item["construction_options"]})]
    choices = ["", *sorted({key for item in payload for key in item["constraint_options"]})]
    for field in ("constraint_a", "constraint_b"):
        fields[field]["enum"] = choices


def resolve_design_evidence(verification, payload):
    resolved = {}
    origins = {}
    invalid = set()
    for field in ("construction_evidence", "constraint_a", "constraint_b"):
        value = getattr(verification, field)
        if re.fullmatch(r"q\d+|s\d+[oba]\d+", value):
            allowed = payload["construction_options" if field == "construction_evidence" else "constraint_options"]
            resolved[field] = payload["task_evidence_options"][value]["text"] if value in allowed else ""
        elif re.fullmatch(r"(?:q\d+|s\d+[oba]\d+):(?:q\d+|s\d+[oba]\d+)", value):
            option = resolve_task_option(value, payload, payload.get("construction_ranges", {})) if field == "construction_evidence" else None
            resolved[field] = option["text"] if option else ""
        if value and field in resolved and not resolved[field]:
            invalid.add(field)
        if resolved.get(field):
            first = value.split(":", 1)[0]
            selected = payload["task_evidence_options"][first]
            source, index = selected["source"], selected.get("source_context_index")
            original = payload["question"] if source == "question" else payload["source_context"][index][source]
            cursor = 0
            for key, entry in payload["task_evidence_options"].items():
                if (entry["source"], entry.get("source_context_index")) != (source, index):
                    continue
                start = original.find(entry["text"], cursor)
                if start < 0:
                    break
                if key == first:
                    span = source_quote_span(original, resolved[field], start=start) if payload.get("_source_bound_ranges") else None
                    if span:
                        resolved[field] = span["text"]
                    origins[field] = {"source": source, "source_context_index": index,
                                      "start": span["start"] if span else start,
                                      "end": span["end"] if span else start + len(resolved[field])}
                    break
                cursor = start + len(entry["text"])
    result = verification.model_copy(update=resolved)
    result._evidence_origins = origins
    result._invalid_design_references = invalid
    return result


def design_proof_assessment(candidate, verification, *, query=None, typed=False):
    """Separate broken evidence from a grounded but out-of-policy design unit."""
    def result(grounded, reason, invalid=()):
        # Only explicit source-level contrary tasks establish a negative.
        # Missing construction wording or a deficient selected positive quote
        # cannot establish that the complete original has no delivery task.
        outcome = ("POSITIVE" if grounded else "NEGATIVE" if reason in {
            "EXPLICIT_CODING_TASK", "EXPLICIT_PROJECT_REPORT", "PARENT_BOUND_INTERNAL_COMPONENT"}
            else "INSUFFICIENT")
        fields = list(invalid)
        if typed and outcome == "INSUFFICIENT" and not fields:
            fields = ["design_unit" if reason == "DESIGN_UNIT_OUT_OF_SCOPE" else "construction_evidence"]
        return {"grounded": grounded, "host_reason": reason, "invalid_evidence_fields": fields, "scope_policy": policy,
                **({"outcome": outcome} if typed else {})}

    policy = design_scope_policy(query)
    canonical = candidate.get("canonical_text", "")[:500]
    records = [(index, source["original_question"], source.get("original_question_complete"))
               for index, source in enumerate(candidate.get("source_context", []))
               if source.get("source_context_status") == "VERIFIED" and source.get("original_question")]
    originals = [text for _, text, _ in records]
    if typed and requires_verified_original(candidate) and not originals:
        return result(False, "SOURCE_ORIGINAL_UNAVAILABLE", ["construction_evidence"])
    # Canonical normalization may add a task absent from every raw question.
    # When originals are verified, all positive claims must coexist in one of
    # those questions. Canonical-only candidates retain the legacy fallback.
    texts = originals or [canonical]
    records = records or [(None, canonical, len(candidate.get("canonical_text", "")) <= 500 if typed else True)]
    # A standardized construction directive cannot erase an explicit coding
    # task in every verified original. Neighbor sections are not task evidence.
    source_code_task = (all(_explicit_code_task(text) for text in originals) if typed and originals else
                        _explicit_code_task(canonical) or (originals and all(_explicit_code_task(text) for text in originals)))
    if source_code_task and not engineering_coding_task_allowed(
            candidate, query=query, evidence=verification.construction_evidence):
        if typed and any(complete is not True for _, _, complete in records):
            return result(False, "NON_DELIVERY_SOURCE_INCOMPLETE", ["construction_evidence"])
        return result(False, "EXPLICIT_CODING_TASK")
    if originals and all(HISTORY_TASK.search(text) and not NEW_REQUEST.search(text) for text in originals):
        if typed and any(complete is not True for _, _, complete in records):
            return result(False, "NON_DELIVERY_SOURCE_INCOMPLETE", ["construction_evidence"])
        return result(False, "EXPLICIT_PROJECT_REPORT")
    allowed_units = {"NEW_OR_REBUILT_SYSTEM", "COUPLED_MECHANISM"}
    if policy == "ENGINEERING_DELIVERY":
        allowed_units.add("COMPONENT_OR_WORKFLOW")
    if verification.design_unit not in allowed_units:
        return result(False, "DESIGN_UNIT_OUT_OF_SCOPE")
    goal = verification.construction_evidence
    matching = [record for record in records if _evidence_positions(
        verification, "construction_evidence", goal, record[0], record[1], bool(originals))]
    if not goal.strip() or not matching:
        return result(False, "CONSTRUCTION_EVIDENCE_NOT_GROUNDED", ["construction_evidence"])

    def source_task_result(supporting, *, coupled=False):
        if _goal_context_task(verification, supporting, bool(originals), coupled=coupled):
            return None
        invalid = ["construction_evidence"] if any(complete is not True for _, _, complete in supporting) else []
        return result(False, "ORIGINAL_DELIVERY_TASK_NOT_ESTABLISHED", invalid)

    if verification.design_unit == "NEW_OR_REBUILT_SYSTEM":
        desired_delivery = re.search(r"希望|需要|要求|要让", goal) and _delivery_task(goal) and (
            policy == "ENGINEERING_DELIVERY" or DELIVERY_TASK.search(goal))
        if not _construction_directive(goal) and not desired_delivery and not (
                policy == "ENGINEERING_DELIVERY" and _delivery_task(goal)):
            return result(False, "NO_CONSTRUCTION_DIRECTIVE")
        if policy == "INDEPENDENT_SYSTEM" and _attached_component(goal):
            return result(False, "PARENT_BOUND_INTERNAL_COMPONENT")
        return source_task_result(matching) or result(True, "GROUNDED_NEW_DELIVERY")
    if verification.design_unit == "COMPONENT_OR_WORKFLOW":
        if not _delivery_task(goal):
            return result(False, "NO_DELIVERY_TASK_DIRECTIVE")
        return source_task_result(matching) or result(True, "GROUNDED_COMPONENT_OR_WORKFLOW")
    first, second = verification.constraint_a, verification.constraint_b
    invalid = [field for field, value in (("constraint_a", first), ("constraint_b", second))
               if not value.strip() or not any(value in text for text in texts)]
    if invalid:
        return result(False, "CONSTRAINT_EVIDENCE_NOT_GROUNDED", invalid)
    # Component names and "how to do X" are possible implementation tasks,
    # not given states, resource bounds or observable behavior requirements.
    if not GIVEN_CONSTRAINT.search(first) or not GIVEN_CONSTRAINT.search(second):
        return result(False, "CONSTRAINT_NOT_A_GIVEN_REQUIREMENT")
    supporting = [record for record in matching if first != second and any(
        a_end <= b_start or b_end <= a_start
        for a_start, a_end in _evidence_positions(verification, "constraint_a", first, record[0], record[1], bool(originals))
        for b_start, b_end in _evidence_positions(verification, "constraint_b", second, record[0], record[1], bool(originals)))]
    if not supporting:
        return result(False, "CONSTRAINTS_OVERLAP_OR_DIFFERENT_QUESTIONS", ["constraint_a", "constraint_b"])
    return source_task_result(supporting, coupled=True) or result(True, "GROUNDED_COUPLED_REQUIREMENTS")


def design_unit_grounded(candidate, verification, *, query=None):
    return design_proof_assessment(candidate, verification, query=query)["grounded"]
