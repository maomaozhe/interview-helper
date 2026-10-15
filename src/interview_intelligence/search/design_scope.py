"""Ground the claimed independent design unit in the current question."""
import re
from typing import Literal

from pydantic import Field, StrictBool

from interview_intelligence.contracts import StrictModel
from interview_intelligence.search.candidate_prompt import task_evidence_grounded


class DesignVerification(StrictModel):
    candidate_id: str
    keep: StrictBool
    reason: str = Field(min_length=1, max_length=140)
    design_unit: Literal["NEW_OR_REBUILT_SYSTEM", "COUPLED_MECHANISM", "INTERNAL_COMPONENT", "LOCAL_SUBTASK", "EXISTING_PROJECT", "UNKNOWN"]
    construction_evidence: str = Field(max_length=160)
    constraint_a: str = Field(max_length=120)
    constraint_b: str = Field(max_length=120)


class DesignScopeResult(StrictModel):
    verifications: list[DesignVerification] = Field(min_length=1, max_length=50)


DESIGN_PROOF_INSTRUCTIONS = (
    "本轮独立设计复核schema优先：每项还必须输出design_unit、construction_evidence、constraint_a、constraint_b。"
    "NEW_OR_REBUILT_SYSTEM仅用于当前题明确提出设计/新建/构建/开发/重构/改造成某个交付对象；"
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
)


CONSTRUCTION_ACTION = re.compile(
    r"设计|新建|构建|搭建|开发|重构|改造成|重新实现|实现一个|实现一套|实现.*系统|实现.*服务|"
    r"\b(?:design|build|develop|rebuild|rearchitect|architect)\b", re.IGNORECASE)

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


def _attached_component(text):
    for match in ATTACHED_DELIVERY.finditer(text):
        parent, target = match.groups()
        if any(word in parent for word in ("支持", "面向", "具有", "满足", "覆盖", "包含")):
            continue
        if target.startswith(("整体", "完整", "总体")) or target.rstrip("。？！?！").endswith(("系统", "服务", "平台")):
            continue
        return True
    return False


def design_unit_grounded(candidate, verification):
    canonical = candidate.get("canonical_text", "")[:500]
    originals = [source.get("original_question", "") for source in candidate.get("source_context", [])
                 if source.get("source_context_status") == "VERIFIED" and source.get("original_question")]
    # A standardized construction directive cannot erase an explicit coding
    # task in every verified original. Neighbor sections are not task evidence.
    if CODE_TASK.search(canonical) or (originals and all(CODE_TASK.search(text) for text in originals)):
        return False
    goal = verification.construction_evidence
    if not task_evidence_grounded(candidate, "DESIGN_TASK", goal):
        return False
    if verification.design_unit == "NEW_OR_REBUILT_SYSTEM":
        return bool(CONSTRUCTION_ACTION.search(goal)) and not _attached_component(goal)
    if verification.design_unit != "COUPLED_MECHANISM":
        return False
    first, second = verification.constraint_a, verification.constraint_b
    if not first.strip() or not second.strip() or first == second:
        return False
    # Component names and "how to do X" are possible implementation tasks,
    # not given states, resource bounds or observable behavior requirements.
    if not GIVEN_CONSTRAINT.search(first) or not GIVEN_CONSTRAINT.search(second):
        return False
    texts = [canonical, *originals]
    return any(first in text and second in text and (
        text.index(first)+len(first) <= text.index(second) or text.index(second)+len(second) <= text.index(first))
        for text in texts)
