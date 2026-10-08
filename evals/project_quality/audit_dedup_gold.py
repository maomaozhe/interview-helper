"""Correct source-label defects in a new immutable dataset version.

These are post-run, agent-reviewed data corrections, not an independent holdout.
No model predictions are read by this script. Prior Gold and reports stay intact.
"""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import shutil

from eval.common import digest, read_json, read_jsonl, write_json, write_jsonl
from eval.validate_gold import load_dataset, validate_gold


CORRECTIONS = {
    28: ("RELATED", "Redis 和 MySQL 的数据一致性怎么保证？你了解哪些方案？", "如何保证数据库和缓存的一致性？",
         "The concrete Redis/MySQL combination is narrower than arbitrary database/cache systems; unspecified products are not inferred."),
    64: ("SAME", "请谈谈你对 AI 的理解。", "了解ai吗",
         "An interview familiarity question invites the same general explanation of AI; neither text specifies a distinct technical subtask or constraint."),
    82: ("RELATED", "HashMap 的底层实现讲一下，1.7 和 1.8 有什么区别？", "HashMap的底层数据结构是什么？JDK不同版本之间有什么变化？",
         "The first fixes the 1.7/1.8 comparison; the second leaves versions unrestricted. Explicit version constraints must not be silently supplied."),
    100: ("RELATED", "这个智能文档审核系统的审核是偏规则审核还是内容审核？", "整个审核更多只是规范性的，不是实际内容的吗？",
          "The second omits the system identity; the concrete document-review system cannot be inferred from the other candidate."),
    103: ("RELATED", "索引为什么使用 B+ 树而不是其他数据结构？", "InnoDB 索引为什么要使用 B+ 树？",
          "A general index-design question and InnoDB's concrete storage-engine design have different subject scope."),
    107: ("RELATED", "TCP为什么需要三次握手？", "为什么HTTP连接需要三次握手？两次握手不行吗？",
          "TCP and HTTP are distinct named protocol subjects. The apparent misconception must not be silently rewritten during deduplication."),
    111: ("SAME", "Java 垃圾回收算法或者机制了解吗？", "Java如何进行垃圾回收？",
          "Both ask for Java garbage-collection mechanisms. The prior reason incorrectly referred to collection data structures and did not match either source text."),
    130: ("RELATED", "Token 很贵，有哪些省钱方案？", "大模型调用成本很高，你们是怎么做成本控制的？",
          "Available token-saving proposals and a team's actual model-cost-control practice have different requested evidence and answer scope."),
    138: ("RELATED", "如果产品经理临时增加需求，你会怎么办？", "如果你目前的项目进度已经做了一半的时候，产品经理提出了一个新的需求，请问怎么做？",
          "The second explicitly conditions the decision on a half-completed project; the first does not specify progress."),
    148: ("RELATED", "做算法题：SQL。", "手撕一个 SQL 语句。",
          "Neither text identifies the SQL problem, tables or desired query. A shared SQL task category is insufficient to establish the same individual problem."),
}


def correct(source: Path, output: Path):
    validate_gold(source, "dedup", review_policy="delegated_agent")
    output.mkdir(parents=True, exist_ok=False)
    for file in source.iterdir():
        if file.is_file() and file.name != "freeze.json":
            shutil.copyfile(file, output / file.name)
    manifest = read_json(output / "manifest.json")
    rows = read_jsonl(output / "dedup.jsonl")
    audit = []
    for row in rows:
        if not row["id"].startswith("dedup-reviewed-"):
            continue
        index = int(row["id"].removeprefix("dedup-reviewed-"))
        if index not in CORRECTIONS:
            continue
        label, left, right, reason = CORRECTIONS[index]
        if (row["left"]["text"], row["right"]["text"]) != (left, right):
            raise ValueError("REVIEWED_SOURCE_PAIR_CHANGED")
        audit.append({"id": row["id"], "old_label": row["label"], "new_label": label,
            "left": row["left"], "right": row["right"], "reason": reason})
        row["label"] = label
        row["review"].update(basis=reason, guide_version="project_quality_agent_v2",
            reviewed_at=datetime.now(timezone.utc).isoformat())
    manifest.update(version=output.name, annotation_guide="evals/project_quality/review_protocol_v2.json",
        supersedes=source.name, correction_policy="post_run_source_audit; regression evidence, not independent holdout")
    write_jsonl(output / "dedup.jsonl", rows)
    write_json(output / "manifest.json", manifest)
    write_json(output / "dedup_review_corrections.json", {"prior_dataset": source.name,
        "reviewer_kind": "agent", "human_verified": False, "corrections": audit,
        "reason": "Full 150-source-pair audit found inconsistent subject, version, referent and generic-task boundaries."})
    write_json(output / "freeze.json", {"dataset_sha256": digest(load_dataset(output)),
        "file_hashes": {file.name: digest(file.read_bytes()) for file in sorted(output.iterdir())
                       if file.is_file() and file.name != "freeze.json"}})
    validate_gold(output, "dedup", review_policy="delegated_agent")
    print({"dataset": str(output), "corrected_pairs": len(audit)})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    correct(args.source, args.output)
