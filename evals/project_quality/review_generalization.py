"""Materialize source-reviewed labels, frozen before any prediction on these sources."""
import argparse
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from eval.common import digest, inside, read_json, read_jsonl, write_json, write_jsonl
from eval.validate_gold import load_dataset, require, validate_gold
from interview_intelligence.agent.task_context import compile_task_context
from interview_intelligence.ingestion.snapshot import decode_source


QUEUE_SHA256 = "f20e6836441aabffcfd5a421237547cea6604290a18a5a5622239adf4aef6a20"
# Every selected original question and its source context was read before authoring these decisions.
CODE_ALGORITHM = {7, 9, 23, 37, 53, 70, 71, 94, 113, 116, 130, 142, 152, 165, 166, 185}
CODE_ENGINEERING = {0, 100}
VERBAL_ALGORITHM = {12}
VERBAL_ENGINEERING = {72}
UNKNOWN_FORM_ALGORITHM = {188, 193}
NOTES = {
    0: "上来先做题的并发交替打印任务；实现线程协作，不是算法原理讨论。",
    9: "原文补充编译器与push_back报错，明确为算法编程求解。",
    12: "同一原文句明确只讲回溯思路、不在手撕上为难，故口述算法。",
    72: "明确构造一个支持高并发读取和新条目插入的具体Cache组件；没有手写要求。",
    100: "同一行前缀明确手撕20min，构造ArrayList类的插入和删除功能。",
    142: "同一原文行有算法环节限定，要求判断具体树性质。",
    166: "同一行明确算法环节的岛屿问题，按算法编程题，不继承旁边SQL形式。",
    185: "同一原文行明确算法并写了25分钟，属于CODE/ALGORITHM。",
    188: "仅记载出了一道原创算法题，没有题干或是否编程的依据；形式保留UNKNOWN。",
    193: "原文只有最长公共子串名称，算法焦点可判定，是否手写无法确认。",
}


def build(snapshot, queue, output, prior):
    require(not output.exists(), "GOLD_VERSION_ALREADY_EXISTS")
    require(digest(queue.read_bytes()) == QUEUE_SHA256, "REVIEWED_QUEUE_CHANGED")
    candidates = read_jsonl(queue)
    require(len(candidates) == 200 and [r["review_index"] for r in candidates] == list(range(200)),
            "REVIEWED_QUEUE_ORDER_CHANGED")
    previous = load_dataset(prior)
    seen = {r["source_hash"] for section in ("extraction", "task_labels") for r in previous[section]}
    require(not seen & {r["source_hash"] for r in candidates}, "PRIOR_SOURCE_LEAKAGE")
    facts = read_json(snapshot / "facts.json")
    require(digest(facts) == read_json(snapshot / "manifest.json")["facts_sha256"], "SNAPSHOT_CHANGED")
    output.mkdir(parents=True)
    stamp = datetime.now(timezone.utc).isoformat()
    rows = []
    for candidate in candidates:
        i = candidate["review_index"]
        if i in CODE_ALGORITHM: form, focus = "CODE", "ALGORITHM"
        elif i in CODE_ENGINEERING: form, focus = "CODE", "ENGINEERING"
        elif i in VERBAL_ALGORITHM: form, focus = "VERBAL", "ALGORITHM"
        elif i in VERBAL_ENGINEERING: form, focus = "VERBAL", "ENGINEERING"
        elif i in UNKNOWN_FORM_ALGORITHM: form, focus = "UNKNOWN", "ALGORITHM"
        else: form, focus = "VERBAL", "NONE"
        raw = inside(snapshot, candidate["source_path"]).read_bytes()
        require(digest(raw) == candidate["source_hash"], "SOURCE_CHANGED")
        target = inside(output, candidate["source_path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            require(digest(target.read_bytes()) == candidate["source_hash"], "COPIED_SOURCE_CHANGED")
        else:
            target.write_bytes(raw)
        context = compile_task_context(decode_source(raw), candidate["source_spans"])
        basis = NOTES.get(i, "原文为明确算法编程求解。" if focus == "ALGORITHM" else
                            "原文讨论原理、已有实现、策略、架构或个人经历；没有具体构造/编程求解要求。")
        rows.append({**candidate, **context, "id": "holdout-label-" + candidate["occurrence_id"],
            "group_id": candidate["source_hash"], "split": "test", "tags": ["source_disjoint_holdout", "task_labels"],
            "human_verified": False, "agent_verified": True, "annotation_origin": "independent_agent_source_review",
            "response_form": form, "coding_focus": focus, "review": {
                "reviewer": "Codex", "reviewer_kind": "agent", "reviewed_at": stamp,
                "guide_version": "annotation_v2", "basis": basis}})
    manifest = {"version": output.name, "kind": "corpus", "status": "frozen", "snapshot": facts["snapshot"],
        "task_labels": "task_labels.jsonl", "review_authorization": previous["review_authorization"],
        "annotation_guide": "docs/annotation-guide.md", "facts_sha256": digest(facts),
        "reviewed_queue_sha256": QUEUE_SHA256, "review_recipe_sha256": digest(Path(__file__).read_bytes()),
        "frozen_at": stamp, "prior_dataset": prior.name, "prior_dataset_sha256": digest(previous),
        "review_lifecycle": "Original-source labels frozen before prediction; no expected labels copied from production.",
        "selection": {"seed": 20261006, "source_disjoint": True, "exact_text_duplicates_excluded": True,
            "prior_canonical_ids_excluded": True, "sources": len({r["source_hash"] for r in rows}),
            "limitations": "Source-disjoint sample from the same corpus; not domain or semantic-family disjoint. Engineering positives are sparse."}}
    write_jsonl(output / "task_labels.jsonl", rows)
    write_json(output / "manifest.json", manifest)
    write_json(output / "freeze.json", {"dataset_sha256": digest(load_dataset(output)),
        "file_hashes": {p.relative_to(output).as_posix(): digest(p.read_bytes())
                       for p in sorted(output.rglob("*")) if p.is_file() and p.name != "freeze.json"}})
    validate_gold(output, "task_labels", review_policy="delegated_agent")
    print({"dataset": str(output), "samples": len(rows), "sources": manifest["selection"]["sources"],
           "classes": dict(Counter(f"{r['response_form']}/{r['coding_focus']}" for r in rows))})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for name in ("snapshot", "queue", "output", "prior"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    build(args.snapshot, args.queue, args.output, args.prior)
