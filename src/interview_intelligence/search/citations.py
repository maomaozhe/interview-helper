"""Select candidate-local canonical excerpts without regenerating their text."""
import re

from interview_intelligence.search.candidate_prompt import candidate_payload


PRIMARY_EVIDENCE_INSTRUCTIONS = (
    "本轮证据输出契约优先：每题question是按q0、q1等编号排列的连续原文片段映射，按顺序阅读就是当前题干。"
    "object_evidence和focus_evidence填写该题的片段编号（如q0），不要重新抄写或改写引文。"
    "宿主按当前candidate_id解析编号为原文，其他候选和source_context的编号不能借用。"
    "对象编号须支持当前限定对象，任务编号须支持当前限定动作/现象；不因存在编号就判相关。"
    "证据不足用空字符串。task_evidence仍是最多160字符的连续原文，请只选证明作答形式的短句；"
    "长题不能整题复制进task_evidence。query_task_evidence及其他query证据仍按原query摘取。"
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


def primary_candidate_payload(candidates):
    payload = candidate_payload(candidates)
    for item in payload:
        question = item["question"]
        parts = []
        for match in re.finditer(r"[^，。？！；：,\n?!;:]+[，。？！；：,\n?!;:]?|[，。？！；：,\n?!;:]", question):
            text = match.group()
            parts.extend(text[start:start+120] for start in range(0, len(text), 120))
        item["canonical_evidence_options"] = {f"q{index}": text for index, text in enumerate(parts) if text.strip()}
    return payload


def primary_wire_payload(payload):
    # Send each question only once. Duplicating full text plus excerpts
    # consumes the conservative reservation needed for a retry.
    wire = [{**{key: value for key, value in item.items()
                if key not in {"question", "canonical_evidence_options"}},
             "question": item["canonical_evidence_options"]} for item in payload]
    for item in wire:
        if "source_context" in item:
            # UUID provenance remains on host candidates and returned sources;
            # the model needs the verified text and status, not opaque IDs.
            item["source_context"] = [{key: value for key, value in source.items()
                                       if key not in {"occurrence_id", "revision_id"}}
                                      for source in item["source_context"]]
    return wire


def evidence_choice_schema(schema, payload):
    choices = sorted({key for item in payload for key in item["canonical_evidence_options"]})
    for field in ("object_evidence", "focus_evidence"):
        schema["$defs"]["CandidateRank"]["properties"][field]["enum"] = ["", *choices]


def resolve_candidate_evidence(item, options):
    resolved = {}
    for field in ("object_evidence", "focus_evidence"):
        value = getattr(item, field)
        # Literal canonical excerpts from older clients remain subject to the
        # existing exact-text checks. Unknown selection IDs cannot become text.
        resolved[field] = options.get(value, "") if re.fullmatch(r"q\d+", value) else value
    return item.model_copy(update=resolved)
