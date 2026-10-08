"""Reproduce the reviewed gold, without using evaluated predictions as labels."""
from __future__ import annotations

import argparse
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from eval.common import digest, inside, read_json, read_jsonl, write_json, write_jsonl
from eval.validate_gold import load_dataset, require
from evals.agent_gold.reviewed_extraction import DOCUMENTS, FOLLOWUPS, NEGATIVES, OVERRIDES, TOPICS, TYPES

WORKBENCH_TASK_HASH = "836cfc999d450a6b4132ee1a97c28eee92223113d0297070576fd8112d0ab557"
WORKBENCH_EXTRACTION_HASH = "26553f8b3fa6b90cf53e76c7b4e5c49826f5d501048e56cd2b928d99f329d04c"
INSTRUCTION = "你自己跑，来打金标，不确定的再给我审核"
GUIDE = "annotation_v2_delegated_source_review"

# Explicit decisions following the complete 200-row source/context review.
CODE_ALGORITHM = {4, 15, 45, 49, 66, 86, 94, 101, 145, 183, 189, 190, 199}
CODE_ENGINEERING = {39, 124}
VERBAL_ALGORITHM = {32, 69, 97}
VERBAL_ENGINEERING = {137, 174}
PENDING = {151: "原文只称‘C++模板类满足规定需求’，未列实际需求；不能仅凭‘算法题’标题裁定算法或工程。"}
# Additional original occurrences deliberately cover SQL and engineering boundaries.
EXTRAS = {
    "08b8319e-fe91-47ec-88ee-27fdb97f3a47": ("CODE", "ENGINEERING", "明确手写LRUCache组件，未指定算法题或LeetCode。"),
    "137d7010-9e93-4e4f-b687-41c63a51b2c4": ("CODE", "ENGINEERING", "明确实现并发数为3的外部接口任务处理器。"),
    "283a5634-8b04-4ff5-8679-49912489e708": ("CODE", "ENGINEERING", "线程安全LRU是明确工程组件实现，不能因数据结构词汇改成算法。"),
    "368acfc9-31aa-4b5c-8472-c82359ede1fd": ("SQL", "ENGINEERING", "明确手撕SQL语句；独立于相邻的文件统计代码题。"),
    "4f2ef995-afdb-4355-9e2f-47e3a8c09df6": ("SQL", "ENGINEERING", "明确场景手写SQL，而不是只分析查询性能。"),
    "95c883d1-ff53-47b6-ab94-e70ab2c558b0": ("CODE", "ENGINEERING", "明确手写阻塞队列组件。"),
    "a78e23a7-bd52-4c77-9d01-d674a96dc8ba": ("CODE", "ENGINEERING", "明确实现LRU缓存的get/put；O(1)约束本身不改变工程组件身份。"),
    "a9776f75-ac56-4228-9806-6906fabbeda3": ("CODE", "ALGORITHM", "原文明确把LRU放在手撕算法环节，与通用缓存组件区分。"),
    "d6c1fa87-2291-4484-8e3b-2d6ca1497dc3": ("CODE", "ENGINEERING", "Coding环节实现按商品ID限流和缓存的组件；不是仅口头讲限流算法。"),
    "f5473f92-d8ea-4f82-b999-7a440bd3f8e4": ("CODE", "ENGINEERING", "明确手撕LRU缓存，默认按工程组件，原文无LeetCode或算法环节依据。"),
    "fb7d7dda-4c74-48ed-a08b-e695f2052d1a": ("CODE", "ENGINEERING", "明确手撕单例模式工程实现。"),
}


def stamp(basis):
    return {"reviewer": "Codex", "reviewer_kind": "agent", "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "guide_version": GUIDE, "basis": basis}


def review_row(key, group, tags, basis):
    return {"id": key, "group_id": group, "split": "test", "tags": tags, "human_verified": False,
            "agent_verified": True, "annotation_origin": "independent_agent_source_review", "review": stamp(basis)}


def span_at(text, first, last, quote, revision):
    lines = text.splitlines(keepends=True)
    start = sum(len(line) for line in lines[:first - 1])
    block = "".join(lines[first - 1:last]).rstrip("\n")
    if quote is None:
        quote = block
    require(quote in block, f"authored quote is absent at line {first}")
    start += block.index(quote)
    return {"revision_id": revision, "start_char": start, "end_char": start + len(quote),
            "start_line": text.count("\n", 0, start) + 1,
            "end_line": text.count("\n", 0, start + len(quote) - 1) + 1,
            "quote": quote, "origin": "OCR" if "## 图片内容（OCR）" in text[:start] or "图片原文 OCR" in text[:start] else "TEXT"}


def materialize(snapshot: Path, workbench: Path, output: Path):
    require(not output.exists(), "gold version already exists; create a new version")
    facts = read_json(snapshot / "facts.json")
    require(digest(facts) == read_json(snapshot / "manifest.json")["facts_sha256"], "immutable snapshot changed")
    require(digest((workbench / "task_labels.jsonl").read_bytes()) == WORKBENCH_TASK_HASH,
            "reviewed task candidate order/content changed")
    require(digest((workbench / "extraction.jsonl").read_bytes()) == WORKBENCH_EXTRACTION_HASH,
            "reviewed extraction candidate order/content changed")
    queue = read_jsonl(workbench / "extraction.jsonl")
    pending, extraction, labels = [], [], []
    output.mkdir(parents=True)

    def source_copy(doc):
        target = inside(output, doc["source_path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(inside(snapshot, doc["source_path"]).read_bytes())
        require(digest(target.read_bytes()) == doc["source_hash"], "source fingerprint changed")
        from interview_intelligence.ingestion.snapshot import decode_source
        return decode_source(target.read_bytes())

    for index, company, position, round_, authored in DOCUMENTS:
        if isinstance(index, int):
            candidate = queue[index]
            doc = next(d for d in facts["documents"] if d["source_hash"] == candidate["source_hash"])
        else:
            doc = next(d for d in facts["documents"] if d["source_hash"].startswith(index))
        text = source_copy(doc)
        row = {**review_row("extract-" + doc["source_hash"][:12], doc["source_hash"], ["source_review", "extraction"],
                           "逐行阅读原文，独立划定提问、场次和属性；原文不完整的日期保留null。"),
               "source_path": doc["source_path"], "source_hash": doc["source_hash"], "original_path": doc["path"],
               "sample_kind": "negative" if index in NEGATIVES else "positive", "sessions": [], "questions": [], "followups": []}
        if index in NEGATIVES:
            row.update(document_kind="COMPILATION", exclusion_basis=NEGATIVES[index])
            extraction.append(row)
            continue
        row["document_kind"] = "INTERVIEW"
        session_specs = [("s1", round_, 1, len(text.splitlines()))]
        if index == 10:
            session_specs = [("s1", "FIRST", 14, 43), ("s2", "SECOND", 44, 65), ("s3", "THIRD", 66, 84)]
        if index == 12:
            session_specs = [("s1", "FIRST", 6, 19), ("s2", "SECOND", 20, 36)]
        publish = {2: "2025-01-21", 12: "2025-12-24", 22: "2025-05-17"}.get(index)
        for key, session_round, first, last in session_specs:
            metadata = {"company": company, "position": position, "round": session_round,
                        "interview_date": None, "publish_date": publish}
            session_row = {"id": key, "metadata": metadata,
                                    "source_evidence": [span_at(text, first, first, None, doc["revision_id"])],
                                    "review_basis": "公司/岗位/轮次仅采用标题、正文或明确场次标题；缺年份不能补访谈日期。"}
            if index == "028868":
                session_row["metadata_to_review"] = ["company"]
                pending.append({"id": "review-boss-company", "section": "extraction", "sample_id": row["id"],
                    "session_id": key, "source_path": row["source_path"], "quote": text.splitlines()[0],
                    "reason": "boss可能指招聘平台，也可能指BOSS直聘公司；正文没有确认雇主。",
                    "proposed": {"company": None}, "options": ["BOSS直聘公司", "仅招聘平台，公司未知", "保持无法裁定"]})
            row["sessions"].append(session_row)
        questions = []
        for piece in authored.split(";"):
            if piece.strip():
                line, code = piece.split()
                # A multi-line explicit replacement below supersedes this line.
                if not any(d == index and a == int(line) for d, a, b, _, _ in OVERRIDES):
                    questions.append((int(line), int(line), None, code))
        questions += [(a, b, quote, code) for d, a, b, quote, code in OVERRIDES if d == index]
        questions.sort(key=lambda q: (q[0], q[2] or ""))
        by_line = {}
        for number, (first, last, quote, code) in enumerate(questions, 1):
            if index == 25 and first == 27:
                quote = "20.一个长字符串里找一个短字符申的最小覆盖子串"
            span = span_at(text, first, last, quote, doc["revision_id"])
            topic, kind = code.split(":")
            sid = next(sid for sid, _, lo, hi in session_specs if lo <= first <= hi)
            q = {"id": f"g-{doc['source_hash'][:12]}-{number:03}", "session_id": sid,
                 "raw_question": span["quote"], "source_spans": [span], "topic_l1": TOPICS[topic][0],
                 "topic_l2": TOPICS[topic][1], "question_type": TYPES[kind], "evidence_kind": "INTERVIEW_QUESTION",
                 "review_basis": "原文提问及其章节上下文；编号相邻不作为追问证据。"}
            if index == 20 and first == 26:
                q.update(attributes_to_review=["question_type"], review_basis="裸词LRU缓存无明确作答方式，题型暂用OTHER；不参与题型准确率。")
                pending.append({"id": "review-lru-task", "section": "extraction", "sample_id": row["id"],
                    "question_id": q["id"], "source_path": row["source_path"], "quote": span["quote"],
                    "reason": "原文只有LRU缓存；无法裁定是手写、原理或口头设计。", "proposed": "OTHER / 暂不计题型准确率",
                    "options": ["手写代码", "口头原理", "口头设计", "保持无法裁定"]})
            row["questions"].append(q)
            by_line.setdefault(first, []).append(q["id"])
        for d, parent, child, marker in FOLLOWUPS:
            if d == index:
                require(len(by_line[parent]) == len(by_line[child]) == 1, "followup endpoint ambiguous")
                row["followups"].append({"source_id": by_line[parent][0], "target_id": by_line[child][0],
                    "evidence_spans": [span_at(text, child, child, marker, doc["revision_id"])]})
        extraction.append(row)

    candidates = read_jsonl(workbench / "task_labels.jsonl")
    for i, candidate in enumerate(candidates):
        if i in PENDING:
            source_copy(next(d for d in facts["documents"] if d["source_hash"] == candidate["source_hash"]))
            pending.append({"id": "review-cpp-template", "section": "task_labels", "sample_id": candidate["id"],
                "source_path": candidate["source_path"], "quote": candidate["raw_question"], "reason": PENDING[i],
                "proposed": {"response_form": "CODE", "coding_focus": "UNKNOWN"},
                "options": ["工程功能实现", "算法或数据结构求解", "同时包含两者", "保持无法裁定"],
                "candidate": candidate})
            continue
        form, focus = ("CODE", "ALGORITHM") if i in CODE_ALGORITHM else ("CODE", "ENGINEERING") if i in CODE_ENGINEERING else (
                      "VERBAL", "ALGORITHM") if i in VERBAL_ALGORITHM else ("VERBAL", "ENGINEERING") if i in VERBAL_ENGINEERING else ("VERBAL", "NONE")
        basis = ("原文要求解释/讨论现有机制、经历或设计，没有编程求解/组件实现任务。" if focus == "NONE" else
                 "原文及上下文明确为算法求解；讲思路与要求代码分别标注。" if focus == "ALGORITHM" else
                 "原文明确为工程组件/校验器/功能实现；是否手写按原文分别判定。")
        source_copy(next(d for d in facts["documents"] if d["source_hash"] == candidate["source_hash"]))
        labels.append({**candidate, **review_row(candidate["id"], candidate["source_hash"], ["random_source_sample", "task_labels"], basis),
                       "response_form": form, "coding_focus": focus, "reviewed_queue_index": i})
    existing = {r["occurrence_id"] for r in labels}
    for q in facts["questions"]:
        if q["id"] not in EXTRAS or q["id"] in existing:
            continue
        doc = next(d for d in facts["documents"] if d["revision_id"] == q["source_spans"][0]["revision_id"])
        source_copy(doc)
        form, focus, basis = EXTRAS[q["id"]]
        labels.append({**review_row("label-" + q["id"], doc["source_hash"], ["boundary_supplement", "task_labels"], basis),
            "occurrence_id": q["id"], "source_hash": doc["source_hash"], "source_path": doc["source_path"],
            "raw_question": q["raw_question"], "source_spans": q["source_spans"], "response_form": form, "coding_focus": focus})
    require(len(labels) >= 200, "not enough reviewed task labels")
    require(sum(r["sample_kind"] == "positive" for r in extraction) >= 30, "not enough reviewed positive documents")
    manifest = {"version": output.name, "status": "frozen", "kind": "corpus", "snapshot": facts["snapshot"],
        "physical_indices": facts["physical_indices"], "annotation_guide": "docs/annotation-guide.md", "models": {},
        "review_authorization": {"policy": "delegated_agent", "thread_id": os.environ.get("CODEX_THREAD_ID", "current-user-task"),
                                 "user_instruction": INSTRUCTION},
        "facts_sha256": digest(facts), "workbench_task_candidates_sha256": digest((workbench / "task_labels.jsonl").read_bytes()),
        "blind_review": "Initial v1 source labeling preceded prediction comparison; v2 source rereview adjudicated granularity and explicit role metadata after comparison.",
        "review_lifecycle": "Source-authored diagnostic/regression corpus; later adjudication is not an untouched holdout evaluation.",
        "evaluation_scope": "Published r293 outputs audit; no model replay, no production label publication.",
        "supersedes": "evaluation-agent-reviewed-20261005-v1",
        "adjudication": ["Separate independently answerable tasks, retain inseparable justification/example clauses.",
            "Use explicit role hashtags and literal role names as metadata evidence; do not infer from technology alone.",
            "Redis distributed-lock command/parameter construction is VERBAL/ENGINEERING; existing mechanism explanations stay NONE."],
        "extraction": "extraction.jsonl", "task_labels": "task_labels.jsonl"}
    write_jsonl(output / "extraction.jsonl", extraction)
    write_jsonl(output / "task_labels.jsonl", labels)
    write_jsonl(output / "needs_user_review.jsonl", pending)
    write_json(output / "manifest.json", manifest)
    write_json(output / "freeze.json", {"dataset_sha256": digest(load_dataset(output)),
        "file_hashes": {p.relative_to(output).as_posix(): digest(p.read_bytes()) for p in sorted(output.rglob("*")) if p.is_file() and p.name != "freeze.json"}})
    print({"dataset": str(output), "positive_documents": 30, "negative_documents": 2,
           "questions": sum(len(r["questions"]) for r in extraction), "task_labels": len(labels),
           "task_distribution": dict(Counter(f"{r['response_form']}/{r['coding_focus']}" for r in labels)),
           "pending_review": len(pending), "human_verified": 0})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--workbench", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    materialize(args.snapshot, args.workbench, args.output)


if __name__ == "__main__":
    main()
