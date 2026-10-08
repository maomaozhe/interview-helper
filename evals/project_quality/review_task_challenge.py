"""Materialize explicit Agent source decisions, sealed before challenge inference."""
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from eval.common import digest, inside, read_json, read_jsonl, write_json, write_jsonl
from eval.validate_gold import load_dataset, require, validate_gold


QUEUE_SHA = "16fd3f2646746d7602f435889bc4e752556c8da3e7f12528e878010ea5ba32c9"
CODE_ALGORITHM = {1, 2, 3, 6, 7, 8, 9, 10, 12, 14, 15, 17, 19, 20, 21, 22, 29, 32, 33, 35, 40, 42, 43, 118}
CODE_ENGINEERING = {27, 30, 38, 47, 151}
VERBAL_ENGINEERING = {18, 28, 57, 58, 80, 83, 98, 101, 105, 149}
VERBAL_ALGORITHM = {69, 193}
UNKNOWN = {31, 115}
NOTES = {
    10: "明确写代码，按文件记录聚合并求Top5；主要是输入数据上的统计求解，非通用服务构造。",
    11: "手写两个栈队列含算法求解，同时要求C++拷贝/移动构造这一语言工程任务；同一原始任务为MIXED。",
    18: "原文明示不写代码，但要求构造get/put为O(1)、支持覆盖/淘汰/有效期的Map组件。",
    22: "两个栈实现队列是明确代码求解；原文证据没有包含后续独立线程安全追问，不强行合并为MIXED。",
    28: "具体构造多个线程按规定顺序交替打印；没有明确要求现场写代码，形式为口述。",
    31: "仅二进制编码问题这一名称，邻接JVM与系统场景未提供题干；不足以裁定求解或概念解释。",
    34: "线上Bug快速修复是排障讨论；AI Coding章节名不将这个未给出代码任务的问句变成代码编写。",
    38: "明确手撕JSON路径查询器，构造指定输入输出的解析组件，属于工程代码。",
    46: "实时商品模糊搜索属于应用架构/检索方案讨论，未要求具体API或代码构造。",
    57: "在前一Map构造任务上新增持久化、读写并发及掉电恢复要求，口述组件构造方案。",
    58: "Redis原理未回答后，明确转为让候选人构造LRU机制；非力扣求解，没有写代码要求。",
    69: "要求构造综合数组/链表特点的数据结构，是抽象数据结构求解，无服务/API构造要求。",
    79: "AI面试模块组成讨论；不因此前手撕标题就将架构问答变为编程。",
    80: "继承前一分布式锁构造的核心命令/参数任务，要求构造过期释放行为以避免死锁。",
    83: "构造任务队列并满足多类请求公平性与资源占用约束，没有代码要求。",
    98: "继承明确的并发安全按需启动功能构造，要求给出懒加载实现方案。",
    101: "明确假设自己构造延时队列，后续要求准时消费与调度行为，属于口述组件构造。",
    105: "要求构造处理大量任务的线程池，未要求手写代码；不同于解释现有线程池原理。",
    115: "只记述出了增量优化场景，没有完整要求和作答形式，两个维度均保持UNKNOWN。",
    118: "算法环节给出两组有序timestamp记录，要求线性无副作用合并，属于算法编程求解。",
    127: "在整体系统缓存讨论中询问要考虑的事项，没有新组件接口/行为构造要求。",
    149: "解释线程池之外，独立提出让候选人设计任务队列，按具体组件构造而非现有机制解释。",
    151: "原文明示AIcoding实现周报发送系统，含自动子任务及异常行为，是明确工程编码任务。",
    193: "同一行明确算法环节，先序/中序换成反转链表加口述LRU；按VERBAL/ALGORITHM，非通用缓存组件。",
    194: "MCP知识问答环节，开发方式说明没有具体新server实现要求，不把技术用法误判构造。",
}


def build():
    root = Path("data/reports/quality-refinement-20261006")
    queue = root / "engineering-challenge.queue.jsonl"
    require(digest(queue.read_bytes()) == QUEUE_SHA, "REVIEWED_QUEUE_CHANGED")
    candidates = read_jsonl(queue)
    require(len(candidates) == 200 and [r["review_index"] for r in candidates] == list(range(200)), "QUEUE_ORDER_CHANGED")
    snapshot = Path("data/reports/resume-quality-snapshot-20261005-v1")
    previous = load_dataset(Path("data/gold/evaluation-agent-reviewed-20261005-v5"))
    output = Path("data/gold/task-engineering-challenge-agent-20261006-v1")
    require(not output.exists(), "GOLD_VERSION_ALREADY_EXISTS")
    output.mkdir()
    at = datetime.now(timezone.utc).isoformat()
    rows = []
    for c in candidates:
        i = c["review_index"]
        if i in CODE_ALGORITHM: f, focus = "CODE", "ALGORITHM"
        elif i in CODE_ENGINEERING: f, focus = "CODE", "ENGINEERING"
        elif i in VERBAL_ENGINEERING: f, focus = "VERBAL", "ENGINEERING"
        elif i in VERBAL_ALGORITHM: f, focus = "VERBAL", "ALGORITHM"
        elif i == 11: f, focus = "CODE", "MIXED"
        elif i in UNKNOWN: f, focus = "UNKNOWN", "UNKNOWN"
        else: f, focus = "VERBAL", "NONE"
        source = inside(snapshot, c["source_path"]).read_bytes()
        require(digest(source) == c["source_hash"], "SOURCE_CHANGED")
        destination = inside(output, c["source_path"])
        destination.parent.mkdir(exist_ok=True)
        destination.write_bytes(source)
        basis = NOTES.get(i, "原文明示编程/手撕算法求解。" if focus == "ALGORITHM" else
                          "原文解释现有机制、项目经验、业务架构、优化或策略，不要求构造新组件/求解。")
        rows.append({**c, "id": "engineering-challenge-" + c["occurrence_id"], "group_id": c["source_hash"],
                     "split": "test", "tags": ["engineering_challenge", c["selection"]],
                     "human_verified": False, "agent_verified": True, "response_form": f, "coding_focus": focus,
                     "annotation_origin": "independent_agent_source_review", "review": {
                         "reviewer": "Codex", "reviewer_kind": "agent", "reviewed_at": at,
                         "guide_version": "annotation_v2", "basis": basis}})
    manifest = {"version": output.name, "kind": "corpus", "status": "frozen", "snapshot": previous["snapshot"],
                "task_labels": "task_labels.jsonl", "annotation_guide": "docs/annotation-guide.md",
                "review_authorization": previous["review_authorization"], "frozen_at": at,
                "reviewed_queue_sha256": QUEUE_SHA, "review_recipe_sha256": digest(Path(__file__).read_bytes()),
                "review_lifecycle": "All 200 original quotes and necessary context reviewed; candidate challenge predictions absent when labels sealed.",
                "selection": {"prior_task_occurrence_canonical_exact_text_and_source_spans_disjoint": True,
                              "source_disjoint": False, "seed": 202610061,
                              "limitations": "Cue-stratified challenge from seen corpus sources, not random natural traffic or unseen-source generalization."}}
    write_json(output / "manifest.json", manifest)
    write_jsonl(output / "task_labels.jsonl", rows)
    write_json(output / "freeze.json", {"dataset_sha256": digest(load_dataset(output)),
        "file_hashes": {p.relative_to(output).as_posix(): digest(p.read_bytes()) for p in sorted(output.rglob("*"))
                        if p.is_file() and p.name != "freeze.json"}})
    validate_gold(output, "task_labels", review_policy="delegated_agent")
    print({"dataset": str(output), "samples": len(rows), "counts": dict(Counter(f"{r['response_form']}/{r['coding_focus']}" for r in rows))})


if __name__ == "__main__":
    build()
