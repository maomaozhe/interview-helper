"""Source-only diagnosis of the sealed v9 ENGINEERING regression; no relabeling."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from interview_intelligence.agent.task_context import compile_task_context


ROOT = Path("data/reports/source-optimization-20261015-v2")
REFERENCE = Path("data/gold/source-reviewed-extraction-20261014-v3")


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def rows(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# Indexes are the immutable diagnostic packet order; IDs below prevent accidental
# reuse against a changed prediction or another question with a similar phrase.
# These are audit judgments, never replacement gold labels or model instructions.
DECISIONS = {
 1: ("label-3d82a7fc-1202-4aa9-9206-6cd1df883d95", "CONTRACT_CONFLICT", "NEW_DELIVERY_SCOPE",
     "原文第15行要求设计支持工具可插拔扩展的Agent架构，并非只解释某个现成框架；旧审核把架构统一视为NONE，新source-v3明确按新增扩展能力判ENGINEERING。二者为同源同题的边界冲突。"),
 2: ("label-a3ccfb0e-dbe6-4275-8aed-176f1ef891e6", "CONTRACT_CONFLICT", "NEW_DELIVERY_SCOPE",
     "第26行要求用Redis实现滑动窗口，未记载是在介绍已做过的实现；旧NONE与source-v3 ENGINEERING同题冲突。不能仅凭此处的‘详细讲讲’排除构造，也不能补出原文未写的窗口用途。"),
 3: ("label-21ddc39d-890b-4cc0-bf56-c5e6e438ec8e", "CONTRACT_CONFLICT", "NEW_DELIVERY_SCOPE",
     "第21行明确‘补全’平台旁路设计，列可观测性、故障Auto-RC、人评接入三项能力；原有项目是基础，任务要求补全新能力。旧review将所有架构讨论判NONE，与v9允许功能性应用/架构交付存在范围差异。"),
 4: ("label-cb5ad0d8-0080-4194-a894-89f4b6f709cd", "PROMPT_ERROR", "EXPLANATION_VS_CONSTRUCTION",
     "第8-14行连贯讨论实习开发范式、AI coding与上下文工程。第12行问重做业务时如何组织整体上下文和约束流程，未给新技术功能的运行职责；更接近开发方法讨论。v9把‘部署/重做’读成具体构造交付，超出已记录任务。"),
 5: ("label-7c519e61-e3ca-4d5b-8bf9-283e6e99f50b", "CONTRACT_CONFLICT", "NEW_DELIVERY_SCOPE",
     "第51-54行为独立场景题，明确购票系统、10万人/1000座位、长路程优先分配及高可用目标；具备业务交付对象与可检查行为。旧NONE采用‘系统设计不算组件’范围，v9的新系统构造口径会判ENGINEERING；不应借改旧gold提高成绩。"),
 6: ("label-2638434d-c336-4cc2-a4b7-0b3dc33e781d", "CONTRACT_CONFLICT", "NEW_DELIVERY_SCOPE",
     "第21行‘这个场景’确指第19行新设计的数据集成系统；编译输入也保留了父题。要求把布隆过滤器用于其过滤写入行为，与第20行单纯讲原理不同。旧NONE与允许继承明确新交付要求的口径冲突，不能丢掉父题后只按工具名判断。"),
 7: ("label-8afa3bdd-2511-4051-b287-a553ab0da57d", "PROMPT_ERROR", "EXPLANATION_VS_CONSTRUCTION",
     "第23-26行连续问回表、Redis一致性、Redis分布式锁、协议知识，当前题为‘讲下Redis如何实现分布式锁’。没有指派新的锁对象、行为或命令参数；相较‘用Redis实现基础锁并给核心命令参数’证据更弱。v9将常见机制说明提升为构造。"),
 8: ("label-08b8319e-fe91-47ec-88ee-27fdb97f3a47", "PROMPT_ERROR", "LRU_TASK_IDENTITY",
     "第24行仅记载‘手撕：手写LRUCache’，当前二面之前是Redis机制讨论；没有LeetCode号或算法题身份。按冻结指南，明确写缓存组件为CODE/ENGINEERING，不能凭LRU常见题库身份改为算法。"),
 9: ("label-a78e23a7-bd52-4c77-9d01-d674a96dc8ba", "PROMPT_ERROR", "LRU_TASK_IDENTITY",
     "第21行明确写LRU缓存get/put与O(1)，未称本题为算法题；第18行另一道三元组手撕不是当前任务的算法身份。旧gold及source-v3都为CODE/ENGINEERING，v9把复杂度约束和熟悉题型当作算法证据。"),
 10: ("label-f5473f92-d8ea-4f82-b999-7a440bd3f8e4", "PROMPT_ERROR", "LRU_TASK_IDENTITY",
      "第9行只说一道GC八股和一道手撕LRU，第22行重复‘手撕：LRU缓存’，没有算法环节标识。任务焦点应按本题缓存构造裁定，不能把‘手撕’自动等同ALGORITHM。"),
 11: ("label-c20dbdfd-471c-413b-8d85-02e21f7ec6b3", "HUMAN_ADJUDICATION_MISSED", "HUMAN_AUTHORITY",
      "第21行同时记载算法题身份与C++模板类实现，且第26行说明考察代码熟练度。更关键的是field_reviews.coding_focus保存用户对该项的MIXED直接裁定，优先级高于Agent判断。v9输出ENGINEERING丢失算法侧；工程filter仍命中，但strict类别错误必须保留。"),
 12: ("holdout-label-09b41f26-4caa-4d97-905e-9da5b8c56bd6", "CONTRACT_CONFLICT", "NEW_DELIVERY_SCOPE",
      "第15行为‘如果让你设计一个智能导购助手’的独立场景，明确不是已有项目复盘；第16行‘没有手撕’只限定作答形式。原文没有展开导购动作/流程，旧整体架构NONE与v9把应用职责视为构造目标的最低证据门槛不同，需先统一最低行为证据要求。"),
 13: ("holdout-label-2536ea82-8ffa-40a9-98ff-412e7fe6e094", "CONTEXT_LOSS", "SCOPE_CONTEXT",
      "全文第21行起是最熟悉项目及其延迟注入、路由、审计、双进程适配；第33行继续解释已有跨OS适配。实际4行context丢了项目根问题，却额外带入第19行另一道手撕标题。新source-v3同题仍NONE；输入编译应保留项目根作用域并终止过时手撕环节。尚无反事实复跑，不能证明这是唯一成因。"),
 14: ("holdout-label-189ebf54-87d3-4cf0-b322-1cb68da3a696", "PROMPT_ERROR", "EXPLANATION_VS_CONSTRUCTION",
      "全文从第7行起回顾已有智能体项目；当前第23行承接Docling/VLM实际效果和RAG调优，询问检索杂乱后的流程改进。提供的邻近上下文已有实际数据/调优线索，未指派一个新产物；v9将流程优化讨论判成工程构造。"),
 15: ("holdout-label-b0b4d96e-0395-4806-b4ef-8f850b8d9dcd", "PROMPT_ERROR", "EXPLANATION_VS_CONSTRUCTION",
      "第17行明确‘项目’，第18-20行连续追问该项目bitmap多用户多标签、取模长度、支付超时设置；‘项目’就在实际输入context内。当前题没有切换为新增组件交付，v9忽略已有项目作用域，不能归咎于完全没给上下文。"),
 16: ("holdout-label-33932295-bfa3-4a5f-bc06-d6b228e740eb", "CONTRACT_CONFLICT", "NEW_DELIVERY_SCOPE",
      "第13行明确‘让你设计一个评测系统’，并点明要评测Agent的调用次数和执行流程；旧review把架构/评估讨论统一列为NONE。v9允许带运行职责的新系统构造，但这里也可能仅问评测方法，系统交付与指标方法的最低证据门槛需要显式版本化。"),
 17: ("holdout-label-121400e9-5eb6-4e8a-bb36-a0efc59a8232", "PROMPT_ERROR", "EXPLANATION_VS_CONSTRUCTION",
      "第9行为一般接口鉴权做法与token隔离问题，措辞是‘一般怎么处理’，没有给出要交付的新接口/代理、具体命令或参数。其后紧接协议机制讨论；v9把一般安全策略解释等同构造了一个新鉴权组件。"),
 18: ("holdout-label-8fa2b917-c76f-4881-8242-14586fad5aa8", "CONTRACT_CONFLICT", "NEW_DELIVERY_SCOPE",
      "第15行‘这个过程’明确指同一行刚要求编写的两栈队列，追问实现多线程安全；父题在实际context_before内。旧NONE把它当一般并发策略，新口径可视为对刚构造队列新增并发行为。该追问应独立裁定，不能为纠正旧分数反向污染父题。"),
 19: ("holdout-label-a70398b4-5864-4db4-a5ac-8d402104ccb6", "CONTRACT_CONFLICT", "NEW_DELIVERY_SCOPE",
      "第9行既问Codex能力，又要求在不改Codex的约束下接入本地多工具；不是单问既有协议定义。旧架构NONE与v9允许应用集成目标的ENGINEERING边界有差异；原文没要求可执行命令，不能自动补命令/API交付。"),
 20: ("holdout-label-2c6aafce-008e-4528-bb5f-31c898d5f426", "PROMPT_ERROR", "EXPLANATION_VS_CONSTRUCTION",
      "全文第11行说明下面是候选人已给方案后的追问，第13-17行连问分表、冷热分离、异步写和Redis结构，当前是在优化已有方案以免扫库。原始需求本身未完整记载；不能用Redis数据结构与性能目标补出独立新组件。根说明未进入4行context是次要信息损失。"),
 21: ("holdout-label-d74b0f15-4f35-4fdf-936e-dc659baf3c8a", "CONTRACT_CONFLICT", "NEW_DELIVERY_SCOPE",
      "第9行明确假设要做网页，并追问大访问量优化，但未展开网页具体功能；旧整体流程/架构讨论NONE与v9应用交付范围不同。它不是已有项目回顾，也没有代码要求，不能只按网页名补全产品行为。"),
 22: ("holdout-label-8bfec65f-915f-4edd-b819-7c8b5241228e", "CONTRACT_CONFLICT", "NEW_DELIVERY_SCOPE",
      "第8行明示让候选人新设计可对话差旅助手，并给周三赴上海、安排行程的行为示例。旧NONE与新source-v3 ENGINEERING同源同题直接冲突；具体业务交付在v9政策下成立，不能因对象是Agent就排除。"),
 23: ("engineering-challenge-e78cf67f-e5c7-4509-8e9b-9eb863c99e95", "PROMPT_ERROR", "ALGORITHM_RESULT",
      "第14行单次代码任务对文件记录按歌曲聚合并求Top5；交付是给定输入上的统计结果，没有维护业务状态、通用服务或查询组件要求。v9把读文件和业务字段提升为工程构造，压过了本题明确的数据求解目标。"),
 24: ("engineering-challenge-0260d534-a970-4f93-90b6-7f2a8830f7ad", "PROMPT_ERROR", "MIXED_REQUIREMENTS",
      "第46-51行同一手写任务明确两栈队列求解、push/pop/front/back及C++拷贝/移动构造；不是单纯把算法写成代码，确有额外语言工程要求。v9只保留工程侧而丢失两栈求解侧；工程filter正确，strict MIXED错误仍保留。"),
 25: ("engineering-challenge-3094c3b4-0942-4223-a265-6f7e9205672c", "PROMPT_ERROR", "TASK_BOUNDARY",
      "当前source span仅为第15行‘代码题：两个栈实现一个队列’；线程安全属于同一行‘后续追问’的另一独立任务，出现在context_after。把下一追问的工程目标移到当前两栈求解会改变任务身份，v9未守住原子任务边界。"),
 26: ("engineering-challenge-2046a6b9-6ed5-4919-b8ce-699e1c6003f1", "CONTRACT_CONFLICT", "NEW_DELIVERY_SCOPE",
      "第19行独立系统设计题明确10万/秒输入、ID/status、条件新增更新、无效消息过滤及性能目标。它把新数据集成系统交给候选人，旧NONE的泛化review与v9功能性系统交付范围冲突；前一算法题和下一布隆原理均不能代替本题身份。"),
 27: ("engineering-challenge-3c878b1f-c74b-4f0f-8d19-631cc5a58b1e", "PROMPT_ERROR", "CONCURRENT_BEHAVIOR",
      "第27行要求实现多线程按规定顺序交替打印，是明确并发行为构造；第25行才是JMM安全通信原理，不能把前题说明任务延续到本题。旧gold和source-v3一致为VERBAL/ENGINEERING，未要求代码所以保留VERBAL。"),
 28: ("engineering-challenge-5d7b541b-cc8c-4493-aa76-d28da3645a39", "PROMPT_ERROR", "CONCURRENT_BEHAVIOR",
      "第33行明确‘手撕一道，怎么保证接口幂等性’，给代码作答环节和幂等行为目标；v9把两个维度都弃为UNKNOWN，超过必要的保守程度。该gold的review.basis错误复用了‘不要求构造’泛化句，与其CODE/ENGINEERING标签不一致；标记审核依据文案冲突，但不改变gold。"),
 29: ("engineering-challenge-16a24aa1-28ea-49ce-8c3c-c7cb4aeb6d48", "CONTRACT_CONFLICT", "NEW_DELIVERY_SCOPE",
      "第18行新功能对象为实时商品名称模糊搜索，已给输入行为和搜索目标；第19-20行再问输入联想与网络协议。旧review以‘应用架构未要求API’排除ENGINEERING，而v9明确不要求逐项API/代码，属于范围冲突。"),
 30: ("engineering-challenge-9065cf63-e702-4af9-9a02-38052333e848", "PROMPT_ERROR", "LRU_TASK_IDENTITY",
      "第18行先问Redis中LRU淘汰机制，再因候选人忘记而切换为‘让你去实现这么个算法’。没有给输入求答案或算法题环节，只给LRU机制实现对象；‘算法’这个名词本身不足以证明独立算法求解。v9丢失构造机制与讨论算法词汇的区别。"),
 31: ("engineering-challenge-3817b187-acbc-4c03-8c85-5dbdf3c675e9", "CONTRACT_CONFLICT", "NEW_DELIVERY_SCOPE",
      "第19行明确假设实现监控埋点系统并承受大量日志请求，是新系统及运行职责；旧NONE泛化review把高并发方案讨论全部排除，与v9允许系统构造范围不一致。线程池相邻题可作知识背景，但不能自动新增线程池实现要求。"),
 32: ("engineering-challenge-1522d6c0-056e-4dec-acfd-7ea450738b0d", "PROMPT_ERROR", "EXPLANATION_VS_CONSTRUCTION",
      "第15-18行连问TCP可靠性、WebSocket断线重连、超时及请求幂等性；当前只问常见断线恢复策略，未给新连接组件、命令参数或具体交付对象。v9把‘如何设计’和故障场景本身视为构造证据。"),
 33: ("engineering-challenge-166cdf43-a97b-4680-944f-491b58bf8823", "PROMPT_ERROR", "EXPLANATION_VS_CONSTRUCTION",
      "第19行明确结合项目问八股，第21行由数据一致性延伸到多机房Redis的整体限流策略；该项目/八股标题已在实际输入内。原文没有交付新限流组件或命令参数的要求，v9将假设部署变化等同新构造。"),
 34: ("engineering-challenge-72db9cca-53fa-41dc-b179-a9b08ad4d191", "CONTRACT_CONFLICT", "NEW_DELIVERY_SCOPE",
      "第31-34行为连续新Agent场景，第33行给日志/告警输入以及自动发现并恢复的问题修复行为，并说‘设计…agent出来’。已有项目是背景而新增Agent是交付；旧NONE的泛化架构排除口径与v9有实质差异。"),
 35: ("engineering-challenge-cc9d1cd1-14f2-4c5d-9ac0-61e5e64a1b5a", "CONTRACT_CONFLICT", "UNKNOWN_TARGET",
      "第19行只说组合数组/链表优点，未给操作、功能或独立求解目标；第21行LRU是另一手撕题。旧challenge把该题判ALGORITHM，新source-v3同源同题为UNKNOWN；v9判ENGINEERING也超出了现有证据。需保留不确定性，不靠熟悉数据结构补行为。"),
 36: ("engineering-challenge-291c8c48-bfc4-4877-94b5-cf4ea8814070", "PROMPT_ERROR", "PARAMETER_DELIVERY",
      "第23行明确实现Redis锁并给核心命令/关键参数，第25行继承它要求设计参数防客户端崩溃死锁；父题完整出现在实际context_before。该题给的是具体参数行为交付，v9把它当一般故障补救讨论，漏掉已有构造作用域。"),
 37: ("engineering-challenge-27889123-c145-4ee7-b4a9-c84ccc3443f0", "PROMPT_ERROR", "EXPLANATION_VS_CONSTRUCTION",
      "第17-20行连续讨论延迟优化、弹性伸缩、监控和跨服务Trace策略；第20行未指派一个新追踪组件或具体交付行为，只问链路追踪的通用做法。仅凭Trace与‘怎么设计’不足以改成构造。"),
 38: ("engineering-challenge-d5bcc809-e494-4286-bf98-35a29e888feb", "PROMPT_ERROR", "EXPLANATION_VS_CONSTRUCTION",
      "第11行要求多轮对话记忆分级与缓存策略，周围为架构闭环、选型、可靠性等知识讨论；没有新增存储功能或具体操作要求。v9把记忆/缓存技术对象直接等同工程任务，违反其策略讨论应为NONE的规则。"),
 39: ("engineering-challenge-52880f29-74c7-44f2-98b4-8cb2b82f84d2", "PROMPT_ERROR", "EXPLANATION_VS_CONSTRUCTION",
      "第16行只问多Agent通信机制设计，标‘场景化问题’但没有记载具体通信对象行为、协议交付或新增功能。前面的RAG排障及后面的MCP机制不能补出独立构造要求；‘场景化’不是工程标签证据。"),
 40: ("engineering-challenge-bef49ccf-89a0-4417-b5cd-f96c1cdfbcc4", "PROMPT_ERROR", "EXPLANATION_VS_CONSTRUCTION",
      "第68-80行是重构、范式、路由、评分与上线修复的技术讨论；第74行‘评分体系’未具体指定运行流程或构造产物。第82行才另起明确设计题，第84行的训练评测编排约束不能倒灌到本题。v9把抽象评分方法当新系统构造。"),
 41: ("engineering-challenge-5450cb64-f534-4dad-a537-052c21fd1ba9", "PROMPT_ERROR", "EXPLANATION_VS_CONSTRUCTION",
      "第16行仅问大规模Agent线程/进程资源调度策略，未指定要实现的调度器操作与目标；第17行另题才给GPU有限及吞吐时延约束。v9不能把邻题具体要求或‘资源调度’领域词补给当前题。"),
 42: ("engineering-challenge-8d5f15de-c3e3-4188-b7be-552d246f4630", "PROMPT_ERROR", "EXPLANATION_VS_CONSTRUCTION",
      "第11行用SSE断联引出恢复、存储及Redis过大怎么办，是故障和补救策略串问；没有新模块、操作接口或具体命令参数交付。v9规则已说故障/流量变化不能自动变构造，但本题仍越界。"),
 43: ("label-g-new-6a559eecded4-007", "PROMPT_ERROR", "SQL_FOCUS",
      "第24行明确SQL查询第二高薪水，模型已正确识别作答形式SQL，却输出ALGORITHM焦点。冻结指南与v9都规定写SQL归ENGINEERING，求查询结果的目标不改变这一约定；是直接可定位的标签一致性错误。"),
 44: ("label-g-new-22f55936b83d-005", "PROMPT_ERROR", "UNKNOWN_TARGET",
      "与旧challenge数组/链表结构题为完全同源同任务。source-v3刻意保留UNKNOWN，原文仍无操作/行为目标；v9仅凭结构组合强判ENGINEERING。此项是UNKNOWN金标上的过度裁定，不可当作已知负类的工程filter FP。"),
}

FIXES = {
 "NEW_DELIVERY_SCOPE": "先版本化统一‘带功能职责的新系统/应用交付’与‘整体架构讨论’的边界，列清最低行为证据；保留旧分数，另设统一政策的前瞻独立集。不要按本次错例硬调提示或回改gold。",
 "EXPLANATION_VS_CONSTRUCTION": "要求分别核对当前任务是否指派新产物、是否给该产物行为；已有项目解释、机制/优化/排障/策略讨论不能因设计/实现等词自动变工程。输出诊断可记录这两类证据，仍以原文为准。",
 "LRU_TASK_IDENTITY": "依本题真实算法题身份与交付目标区分LRU；复杂度、手撕及算法名不能替代身份，另一道算法题不能转移标签。保留用户对具体字段的有效裁定。",
 "HUMAN_AUTHORITY": "保留并优先执行该字段的直接用户MIXED裁定；在审核/发布流程区分模型建议与已裁定标签，通用政策若有例外需显式记录，不由Agent覆盖。",
 "SCOPE_CONTEXT": "用真实段落/章节及父任务边界保留项目根问题、适用题干和当前环节；遇新项目/原理章节结束旧手撕作用域，避免只按20行回溯重插过时标题。需独立输入配对验证，尚未证明因果恢复。",
 "ALGORITHM_RESULT": "区分一次性对输入求统计/TopK答案与需持续维护业务功能；文件IO和业务字段不改变求解目标，不能把常规代码输出本身再算工程交付。",
 "MIXED_REQUIREMENTS": "同一任务分别核对独立求解要求和额外语言/组件交付，两者都明确时保留MIXED；不把算法代码实现本身重复计作工程。",
 "TASK_BOUNDARY": "显式标记context_after中的下一追问/下一题边界；允许当前省略指代继承父题，但禁止将后续新增功能并回当前原子任务。",
 "CONCURRENT_BEHAVIOR": "命名清楚的交替打印、幂等接口等可检测行为可确定构造，缺完整接口不必把两个维度都弃为UNKNOWN；作答形式依据本题有效手撕/口头要求独立判断。",
 "UNKNOWN_TARGET": "只有抽象优点/结构组合、无功能或操作目标，且求解/构造身份无法判定时保留UNKNOWN，不从邻题或常见实现补约束。",
 "PARAMETER_DELIVERY": "保留父题已要求的命令/参数构造作用域；继承该任务的具体参数行为设计可为工程，区别于没有交付目标的一般故障补救策略。",
 "SQL_FOCUS": "保持已冻结SQL作答→工程任务契约，作为通用语义一致性检查/修复反馈；不能因为查询是TopN求值而改成算法焦点。",
}


def same_task(left, right):
    if left["source_hash"] != right["source_hash"]:
        return False
    overlap = any(a["start_char"] < b["end_char"] and b["start_char"] < a["end_char"]
                  for a in left["source_spans"] for b in right["source_spans"])
    clean = lambda s: re.sub(r"^\s*\d+[.、)）]\s*", "", s).strip()
    return overlap and clean(left["raw_question"]) == clean(right["raw_question"])


def main():
    index = read(ROOT / "task-score-index.json")
    packet = read(ROOT / "task-v9-regression-source-audit.packet.json")
    assert len(packet) == len(DECISIONS) == 44
    reference = rows(REFERENCE / "task_labels.jsonl")
    dataset_meta, frozen_predictions, frozen_gold = {}, {}, {}
    for name, run_path in index.items():
        run = Path(run_path)
        protocol = read(ROOT / name / "task_labels.protocol.json")
        dataset = Path(protocol["dataset"])
        manifest, metrics = read(run / "manifest.json"), read(run / "metrics.json")
        assert digest(run / "predictions.jsonl") == manifest["predictions_sha256"]
        assert digest("src/interview_intelligence/agent/task_context.py") == protocol["runtime_files"]["src/interview_intelligence/agent/task_context.py"]
        assert digest("prompts/task_annotation_v9.md") == protocol["prompt_sha256"]["candidate"]
        frozen_predictions[name] = {row["id"]: row["result"] for row in rows(run / "predictions.jsonl")}
        frozen_gold[name] = {row["id"]: row for row in rows(dataset / "task_labels.jsonl")}
        dataset_meta[name] = {"dataset": str(dataset), "run": str(run), "dataset_sha256": manifest["dataset_sha256"],
            "gold_task_labels_sha256": digest(dataset / "task_labels.jsonl"),
            "predictions_sha256": manifest["predictions_sha256"],
            "strict_engineering": metrics["coding_focus"]["per_class"]["ENGINEERING"],
            "engineering_filter": metrics["task_filter_metrics"]["engineering"]}
    findings = []
    for record in packet:
        number = record["audit_index"]
        expected_id, diagnostic, fix, basis = DECISIONS[number]
        gold, prediction = record["gold"], record["prediction"]
        assert gold["id"] == expected_id
        assert gold == frozen_gold[record["dataset"]][gold["id"]]
        assert prediction == frozen_predictions[record["dataset"]][gold["id"]]
        source = Path(record["source_file"])
        text = source.read_text(encoding="utf-8")
        assert digest(source) == gold["source_hash"] == record["source_sha256"]
        assert all(text[s["start_char"]:s["end_char"]] == s["quote"] for s in gold["source_spans"])
        exact_matches = [other for other in reference if same_task(gold, other)]
        for other in exact_matches:
            reference_source = REFERENCE / other["source_path"]
            assert digest(reference_source) == other["source_hash"]
            reference_text = reference_source.read_text(encoding="utf-8")
            assert all(reference_text[s["start_char"]:s["end_char"]] == s["quote"] for s in other["source_spans"])
        counterpart = [{"dataset": str(REFERENCE), "id": other["id"],
            "label": {k: other[k] for k in ("response_form", "coding_focus")},
            "source_hash": other["source_hash"], "source_spans": other["source_spans"],
            "raw_question": other["raw_question"], "review": other["review"],
            "identity_contract": "equal_source_hash+overlapping_spans+equal_task_text_ignoring_numeric_list_prefix",
            "overlap_ranges": [[max(a["start_char"], b["start_char"]), min(a["end_char"], b["end_char"])]
                for a in gold["source_spans"] for b in other["source_spans"]
                if a["start_char"] < b["end_char"] and b["start_char"] < a["end_char"]],
            "reference_source_hash_and_spans_valid": True,
            "label_conflict": any(gold[k] != other[k] for k in ("response_form", "coding_focus"))}
            for other in exact_matches if other["id"] != gold["id"]]
        finding = {"audit_index": number, "dataset": record["dataset"], "id": gold["id"],
            "old_dataset": dataset_meta[record["dataset"]]["dataset"],
            "scope": "supplemental_source_538" if record["dataset"] == "source-538" else "requested_three_regression_sets",
            "metric_status": "UNKNOWN_GOLD_OVERREACH" if gold["coding_focus"] == "UNKNOWN" else "KNOWN_GOLD_STRICT_CLASS_ERROR",
            "strict_error": record["strict_error"], "raw_question": gold["raw_question"],
            "gold_label": {k: gold[k] for k in ("response_form", "coding_focus")},
            "prediction": prediction, "gold_review": gold["review"],
            "field_reviews": gold.get("field_reviews", {}),
            "source_file": str(source), "source_hash": gold["source_hash"],
            "source_spans": gold["source_spans"], "source_hash_valid": True, "source_span_quotes_valid": True,
            "full_source_read": True, "source_line_count": record["source_lines"],
            "compiled_model_context": compile_task_context(text, gold["source_spans"]),
            "context_provenance": "reconstructed from sealed task_context_v1; provider input recipe verified; no hidden model reasoning inferred",
            "diagnostic_class": diagnostic, "fix_family": fix, "basis": basis,
            "general_repair": FIXES[fix], "reference_same_task": counterpart,
            "gold_unchanged": True, "relabel_proposed": False,
            "engineering_filter_error": False if gold["coding_focus"] in {"MIXED", "UNKNOWN"} else True}
        if number == 28:
            finding["review_basis_conflict"] = "review文字说不要求构造，但冻结标签为CODE/ENGINEERING；source支持标签，本审核仅记录文案冲突。"
        findings.append(finding)
    human_decisions = read(Path(dataset_meta["regression-211"]["dataset"]) / "human_review_decisions.json")
    receipt = {"created_at": datetime.now(timezone.utc).isoformat(), "reviewer_kind": "agent",
        "human_verified": False, "agent_verified": True,
        "scope": "source-only diagnosis; existing gold and sealed predictions/scores unchanged; not a new gold release",
        "datasets": dataset_meta, "annotation_guide_sha256": digest("docs/annotation-guide.md"),
        "prompt_v9_sha256": digest("prompts/task_annotation_v9.md"),
        "context_compiler_sha256": digest("src/interview_intelligence/agent/task_context.py"),
        "script_sha256": digest(__file__), "source_evidence_contract": "raw_source_spans_v1",
        "counts": {"requested_cases": 42, "supplemental_source_538": 2, "unique_sources": len({f["source_hash"] for f in findings}),
            "requested_unique_sources": len({f["source_hash"] for f in findings[:42]}),
            "requested_diagnostics": dict(Counter(f["diagnostic_class"] for f in findings[:42])),
            "all_diagnostics": dict(Counter(f["diagnostic_class"] for f in findings)),
            "strict_fp_requested": sum(f["strict_error"] == "FP" for f in findings[:42]),
            "strict_fn_requested": sum(f["strict_error"] == "FN" for f in findings[:42]),
            "mixed_to_engineering_requested": sum(f["gold_label"]["coding_focus"] == "MIXED" for f in findings[:42]),
            "explicit_source_v3_label_conflicts": sum(any(c["label_conflict"] for c in f["reference_same_task"]) for f in findings[:42])},
        "human_authority": {"direct_decisions": human_decisions,
            "cpp_rule": "review-cpp-template explicitly adjudicates coding_focus=MIXED and remains authoritative",
            "lru_rule": "review-lru-task interpretation explicitly adjudicates response_form=CODE and question_type=ALGORITHM; task focus was not separately adjudicated. Do not reinterpret this as a global coding_focus override."},
        "limitations": ["All diagnostics are review judgments, not causal model attribution or replacement scores.",
            "The 42 strict-class errors include two MIXED→ENGINEERING predictions which correctly enter the engineering business filter.",
            "The supplemental UNKNOWN→ENGINEERING item is not a known-negative filter FP.",
            "Context-loss hypotheses require a new frozen paired input experiment; no paid model calls performed.",
            "This audit reviews ENGINEERING strict-class FP/FN only, not all errors in form/algorithm/code filters."],
        "external_model_calls": 0, "production_writes": 0, "findings": findings}
    (ROOT / "task-v9-regression-source-audit.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# task_annotation_v9 回归原文审核", "",
        "旧三套回归共42个严格ENGINEERING类错例（35 FP、7 FN），已逐题阅读36份去重来源全文；另附source-538新结果的2项，合计37份来源。保留全部gold、预测和评分。", "",
        "主要结论：旧回归的下降同时包含真实提示词误判、任务输入作用域缺失，以及旧/新任务政策冲突。不能用source-538改善覆盖旧集回归，也不能把所有旧回归错误视为同一种提示词失败。严格工程类精确率用于发布门槛；含MIXED的业务filter仅辅助解释。", "",
        "| 数据集 | strict ENGINEERING P / R | strict TP / FP / FN | 含MIXED工程filter P / R |", "| --- | ---: | ---: | ---: |"]
    pct = lambda v: f"{100*v:.2f}%" if v is not None else "N/A"
    for name, meta in dataset_meta.items():
        strict, business = meta["strict_engineering"], meta["engineering_filter"]
        lines.append(f"| {name} | {pct(strict['precision'])} / {pct(strict['recall'])} | {strict['tp']} / {strict['fp']} / {strict['fn']} | {pct(business['precision'])} / {pct(business['recall'])} |")
    lines += ["", "## 分类判断与限制", "",
        f"请求的42项：{json.dumps(receipt['counts']['requested_diagnostics'], ensure_ascii=False)}。其中16项CONTRACT_CONFLICT是源审核提出的边界分歧，不表示这些旧gold已被改正；其余按各例依据处理。", "",
        "同源冲突用真实source_hash、重叠字符span及去列表编号后相同任务文本确认；没有仅按语义相似/机器归一化串联。四个明确的旧/新同源标签冲突见下表。建议先固定新构造的最低行为证据和整体架构边界，版本化指南；旧分数继续报告，后续新集使用统一政策。", "",
        "| 旧dataset / id / 标签 | source-v3 id / 标签 | 两边review依据 |", "| --- | --- | --- |"]
    esc = lambda value: str(value).replace("|", "\\|").replace("\n", "<br>")
    for finding in findings[:42]:
        for ref in finding["reference_same_task"]:
            if ref["label_conflict"]:
                old_label = "/".join(finding["gold_label"].values())
                new_label = "/".join(ref["label"].values())
                lines.append(f"| {Path(finding['old_dataset']).name}<br>{finding['id']}<br>{old_label} | {ref['id']}<br>{new_label} | 旧：{esc(finding['gold_review']['basis'])}<br>新：{esc(ref['review']['basis'])} |")
    lines += ["", "用户裁定优先：C++模板类的coding_focus=MIXED有`field_reviews`及`human_review_decisions.json`直接回复记录，Agent不得覆盖。LRU审核卡的`ALGORITHM`裁定映射的是Question Type，另明确作答CODE，记录同时注明任务焦点未另行裁定；不能误扩为所有LRU的coding_focus=ALGORITHM。", "",
        "观察到的输入问题：`task_context_v1`仅保留前4/后3行，且在前20行寻找旧手撕标记；跨OS项目题丢了当前项目根问题，反而插入早先已结束的手撕题。它是已检查的输入事实；模型修复因果仍需独立配对。两栈队列父题则是相反方向：context_after确含后续线程安全要求，提示词需要守住当前原子任务边界。", "",
        "该审核没有新建或修改gold，没有重新计算替换分数、没有模型调用。错误归因均为source-only诊断；未声称知道模型隐藏推理。补充source-538中的UNKNOWN项只表示过度裁定，不当作已知负类FP。", "", "## 逐例依据", ""]
    for finding in findings:
        line = finding["source_spans"][0]["start_line"]
        path = Path(finding["source_file"]).resolve().as_posix()
        label = "/".join(finding["gold_label"].values())
        pred = finding["prediction"]
        lines += [f"### {finding['audit_index']}. {finding['dataset']} — {finding['strict_error']} — {finding['diagnostic_class']}", "",
            f"ID：`{finding['id']}`；冻结标签 `{label}` → v9 `{pred['response_form']}/{pred['coding_focus']}`。", "",
            f"原问：{esc(finding['raw_question'])}", "",
            f"来源：[原文第{line}行](<{path}:{line}>)；SHA256 `{finding['source_hash']}`；字符span " + ", ".join(f"[{s['start_char']},{s['end_char']})" for s in finding['source_spans']) + "，hash/quote均校验通过。", "",
            "冻结review依据：" + esc(finding["gold_review"]["basis"]), "",
            "本次源审核：" + finding["basis"], "", "建议通用修法：" + finding["general_repair"], ""]
    lines += ["## 可复核产物", "",
        "`task-v9-regression-source-audit.json`保存每例原始span、源hash、冻结review/字段裁定、v9预测、按封存规则重建的模型上下文、跨gold同题映射与判断依据。`task-v9-regression-source-audit.packet.json`保留审核时读到的44条输入及对应完整原文；配方为`evals/agent_gold/task_v9_regression_source_audit_20261015.py`。", ""]
    (ROOT / "task-v9-regression-source-audit.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(receipt["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
