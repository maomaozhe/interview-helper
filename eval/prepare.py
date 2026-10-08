"""Build editable annotation queues. No generated item becomes human gold."""
from __future__ import annotations

import argparse
import random
from collections import defaultdict
from pathlib import Path

from eval.common import SECTIONS, digest, inside, read_json, read_jsonl, write_json, write_jsonl
from eval.oracle import frequency_list
from eval.validate_gold import require


def draft(key, group, tags):
    return {"id": key, "group_id": group, "split": "dev", "tags": tags, "human_verified": False,
            "review": {"reviewer": "", "reviewed_at": "", "guide_version": "annotation_v2"}}


def query_seeds():
    definitions = [
        ("algorithm-top40", "前40个频率最高的算法题", {"action": "LIST", "filters.coding_focus": "ALGORITHM", "top_n": 40, "sort": "frequency"}),
        ("engineering", "手撕代码有哪些题目", {"action": "LIST", "filters.coding_focus": "ENGINEERING", "filters.response_form": "CODE", "top_n": None}),
        ("algorithm-code", "手撕算法有哪些题目", {"action": "LIST", "filters.coding_focus": "ALGORITHM", "filters.response_form": "CODE"}),
        ("sql", "手写SQL有哪些题目", {"action": "LIST", "filters.response_form": "SQL", "filters.coding_focus": "ENGINEERING"}),
        ("engineering20", "前20个高频工程代码实现题", {"action": "LIST", "filters.coding_focus": "ENGINEERING", "filters.response_form": "CODE", "top_n": 20}),
        ("chinese-number", "前四十个频率最高的算法题", {"action": "LIST", "top_n": 40, "filters.coding_focus": "ALGORITHM"}),
        ("all-algorithms", "列出全部算法题，按频率排序", {"action": "LIST", "top_n": None, "filters.coding_focus": "ALGORITHM", "sort": "frequency"}),
        ("company-stats", "按公司统计题目出现次数，每页5组", {"action": "STATS", "group_by": "company", "page_size": 5}),
        ("semantic-memory", "找线上内存不断上涨且不影响业务的排查面试题", {"action": "SEARCH"}),
        ("semantic-oom", "oom的常见问法有哪些", {"action": "SEARCH"}),
        ("negation", "只看工程实现题，不要算法题", {"action": "LIST", "filters.coding_focus": "ENGINEERING"}),
        ("importance", "最重要的20道算法题", {"action": "LIST", "filters.coding_focus": "ALGORITHM", "sort": "importance", "top_n": 20}),
        ("gap", "最薄弱的10道算法题", {"action": "LIST", "filters.coding_focus": "ALGORITHM", "sort": "gap", "top_n": 10}),
        ("before-top", "未掌握的前40个高频算法题", {"action": "LIST", "top_n": 40, "review_order": "BEFORE_TOP_N"}),
        ("after-top", "前40个高频算法题中未掌握的", {"action": "LIST", "top_n": 40, "review_order": "AFTER_TOP_N"}),
    ]
    tools = {"LIST": "list_questions", "STATS": "get_question_stats", "SEARCH": "search_questions"}
    rows = []
    for key, message, plan in definitions:
        rows.append({**draft(key, "query-family-" + key, ["query", plan["action"]]), "message": message,
                     "expected_plan": plan, "required_tools": [tools[plan["action"]]],
                     "forbidden_tools": ["record_review"], "allow_write": False,
                     "assertions": [{"id": "result_present", "path": "result.rows", "op": "exists"},
                                    {"id": "unique_rows", "path": "result.rows.*.canonical_question_id", "op": "unique"}]})
    rows[-8]["assertions"] = [{"id": "result_present", "path": "result.rows", "op": "exists"}]
    rows.append({**draft("inherit-and-reset", "query-family-inherit-reset", ["multi_turn", "state"]), "turns": [
        {"message": "手撕代码有哪些题目", "expected_plan": {"filters.coding_focus": "ENGINEERING", "filters.response_form": "CODE"},
         "required_tools": ["list_questions"], "forbidden_tools": ["record_review"],
         "assertions": [{"id": "result", "path": "result.rows", "op": "exists"}]},
        {"message": "只看二面", "expected_plan": {"filters.coding_focus": "ENGINEERING", "filters.response_form": "CODE", "filters.round": "SECOND"},
         "required_tools": ["list_questions"], "forbidden_tools": ["record_review"],
         "assertions": [{"id": "result", "path": "result.rows", "op": "exists"}]},
        {"message": "重新开始，算法题有哪些", "expected_plan": {"filters.coding_focus": "ALGORITHM", "filters.round": None, "top_n": None},
         "required_tools": ["list_questions"], "forbidden_tools": ["record_review"],
         "assertions": [{"id": "result", "path": "result.rows", "op": "exists"}]}]})
    rows.append({**draft("isolated-write-replay", "query-family-write-replay", ["multi_turn", "write", "idempotency"]), "turns": [
        {"message": "前3个高频算法题", "expected_plan": {"action": "LIST", "top_n": 3}, "required_tools": ["list_questions"],
         "assertions": [{"id": "three", "path": "result.rows", "op": "length", "value": 3}]},
        {"message": "把这三题标记为已掌握", "allow_write": True, "required_tools": ["record_review"],
         "expected_plan": {"action": "RECORD_REVIEW", "scope": "current_page", "final": True},
         "assertions": [{"id": "three_events", "path": "result.write_event_delta", "op": "eq", "value": 3, "dimension": "risk"}]},
        {"message": "重放上次请求", "replay_previous": True, "allow_write": True, "required_tools": ["record_review"],
         "assertions": [{"id": "no_duplicate_events", "path": "result.write_event_delta", "op": "eq", "value": 0, "dimension": "risk"},
                        {"id": "no_new_models", "path": "model_calls", "op": "length", "value": 0, "dimension": "efficiency"}]}]})
    return rows


def prepare(output: Path, *, corpus_root: Path | None = None, snapshot_root: Path | None = None, seed=20261005, count=30):
    output.mkdir(parents=True, exist_ok=False)
    randomizer = random.Random(seed)
    manifest = {"version": output.name, "status": "draft", "kind": "corpus", "seed": seed,
                "annotation_guide": "docs/annotation-guide.md", "models": {}, "id_namespace": "gold",
                "canonical_mapping": {"items": [], "review": {"reviewer": "", "reviewed_at": "", "guide_version": "annotation_v2"}}}
    queues = {s: [] for s in SECTIONS}
    proposals = {s: [] for s in SECTIONS}
    facts = None
    if snapshot_root:
        facts = read_json(snapshot_root / "facts.json")
        stamp = read_json(snapshot_root / "manifest.json")
        require(digest(facts) == stamp["facts_sha256"], "exported facts hash mismatch")
        manifest["snapshot"] = facts["snapshot"]
        manifest["physical_indices"] = facts["physical_indices"]
        sources = list(facts["documents"])
        randomizer.shuffle(sources)
        sources = sources[:count]
        for source in sources:
            path = inside(snapshot_root, source["source_path"])
            target = inside(output, source["source_path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
            queues["extraction"].append({**draft("extract-" + source["source_hash"][:12], source["source_hash"], ["source_annotation"]),
                "source_hash": source["source_hash"], "source_path": source["source_path"], "original_path": source["path"],
                "sample_kind": "pending", "questions": [], "sessions": [], "followups": []})
        questions = list(facts["questions"])
        randomizer.shuffle(questions)
        for q in questions[:200]:
            source = next(d for d in facts["documents"] if d["revision_id"] == q["source_spans"][0]["revision_id"])
            target = inside(output, source["source_path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.write_bytes(inside(snapshot_root, source["source_path"]).read_bytes())
            queues["task_labels"].append({**draft("label-" + q["id"], source["source_hash"], ["task_annotation"]),
                "occurrence_id": q["id"], "source_hash": source["source_hash"], "source_path": source["source_path"], "raw_question": q["raw_question"],
                "context_before": q.get("context_before"), "context_after": q.get("context_after"),
                "source_spans": q["source_spans"], "response_form": None, "coding_focus": None})
            proposals["task_labels"].append({"id": "label-" + q["id"], "result": q.get("task_annotation") or {}, "failed": False})
        grouped = defaultdict(list)
        for q in questions:
            grouped[q["canonical_question_id"]].append(q)
        pairs = []
        for group in grouped.values():
            if len(group) > 1:
                pairs.append((group[0], group[1]))
        # Adjacent texts provide discovery candidates, not SAME/RELATED labels.
        ordered = sorted(questions, key=lambda q: (q["topic_id"], q["normalized_question"]))
        pairs.extend((left, right) for left, right in zip(ordered, ordered[1:]) if left["canonical_question_id"] != right["canonical_question_id"])
        seen = set()
        for left, right in pairs:
            identity = tuple(sorted((left["id"], right["id"])))
            if identity in seen:
                continue
            seen.add(identity)
            queues["dedup"].append({**draft("pair-" + digest(identity)[:12], "pair-" + digest(identity), ["dedup_candidate"]),
                "left": {"id": left["id"], "text": left["normalized_question"]},
                "right": {"id": right["id"], "text": right["normalized_question"]}, "label": None})
            if len(queues["dedup"]) == 150:
                break
        list_requests = [{"top_n": 40, "page_size": 20}, {"coding_focus": "ALGORITHM", "top_n": 40, "page_size": 20},
                         {"coding_focus": "ENGINEERING", "response_form": "CODE", "page_size": 7},
                         {"response_form": "SQL", "page_size": 5}, {"round": "SECOND", "page_size": 17},
                         {"topic_l1": "Redis", "page_size": 9}]
        companies = sorted({s.get("company_normalized") for s in facts["interviews"] if s.get("company_normalized")})
        list_requests += [{"company": company, "page_size": 13} for company in companies[:6]]
        for index, request in enumerate(list_requests):
            request["annotation_status"] = facts["snapshot"]["task_annotation_policy"]
            truth = frequency_list(facts, request)
            queues["sql"].append({**draft(f"sql-{index:02}", f"sql-family-{index:02}", ["sql", "frequency", "full_pagination"]),
                "request": request, "all_pages": True, "oracle": "independent_exported_facts_frequency_v1",
                "assertions": [{"id": "ordered_ids", "path": "result.rows.*.canonical_question_id", "op": "eq", "value": truth["ids"]},
                               {"id": "counts", "path": "result.rows.*.occurrence_count", "op": "eq", "value": truth["counts"]},
                               {"id": "unique", "path": "result.rows.*.canonical_question_id", "op": "unique"},
                               {"id": "total", "path": "result.meta.pagination.total", "op": "eq", "value": truth["total"]},
                               {"id": "zero_models", "path": "model_calls", "op": "length", "value": 0, "dimension": "efficiency"}]})
    elif corpus_root:
        from interview_intelligence.config import discover_corpus_documents
        files = discover_corpus_documents(corpus_root)
        randomizer.shuffle(files)
        for path in files[:count]:
            raw = path.read_bytes(); sha = digest(raw)
            target = output / "sources" / (sha + ".md")
            target.parent.mkdir(exist_ok=True); target.write_bytes(raw)
            queues["extraction"].append({**draft("extract-" + sha[:12], sha, ["source_annotation"]), "source_hash": sha,
                "source_path": "sources/" + target.name, "original_path": path.relative_to(corpus_root).as_posix(),
                "sample_kind": "pending", "questions": [], "sessions": [], "followups": []})
    queues["routing"] = query_seeds()
    for index, query in enumerate(("oom的常见问法", "线上内存上涨如何排查", "慢SQL如何定位", "请求超时如何定位", "并发请求重复回源的合并处理", "量子纠错码相关的面试题")):
        queues["retrieval"].append({**draft(f"retrieval-{index:02}", f"retrieval-family-{index:02}", ["retrieval_annotation"]),
                                   "query": query, "filters": {}, "sample_kind": "pending", "relevance": {}})
    for section in SECTIONS:
        manifest[section] = section + ".jsonl"
        write_jsonl(output / manifest[section], queues[section])
        if proposals[section]:
            write_jsonl(output / "proposals" / (section + ".jsonl"), proposals[section])
    write_json(output / "manifest.json", manifest)
    (output / "README.md").write_text("# 评测标注工作包\n\n全部样本是dev草稿，human_verified为false。\n\n先查看sources原文，再填写独立问题、场次和标签；proposals单独保存机器结果，不能直接复制为金标。routing种子只覆盖部分计划断言，须补事实、来源和终态检查。sql期望由独立频率oracle导出，但沿用未核验生产分类和归并，只能用于工程开发评测。\n\n按来源和模板分组后划分dev/test；真人审核填写review并确认human_verified。test达到最低规模且snapshot/canonical_mapping完成后才能设frozen。不要直接将此包整体改成test。\n", encoding="utf-8")
    return {s: len(queues[s]) for s in SECTIONS}


def import_feedback(directory: Path, output: Path):
    require(not output.exists(), "feedback output already exists")
    rows, seen = [], set()
    for file in sorted(directory.glob("*.json")):
        feedback = read_json(file)
        key = digest({k: feedback.get(k) for k in ("category", "note", "query", "canonical_question_id")})
        if key in seen:
            continue
        seen.add(key)
        rows.append({**draft("feedback-" + key[:12], "feedback-" + key, [feedback.get("category", "OTHER")]),
                     "query": feedback.get("query", ""), "note": feedback.get("note", ""),
                     "context": feedback.get("context", {}), "source_file": file.name})
    write_jsonl(output, rows)
    return len(rows)


def propose_alignments(predictions: Path, output: Path):
    require(not output.exists(), "alignment output already exists")
    rows = [{"id": p["id"], "prediction_sha256": digest(p.get("result", {})), "human_verified": False,
             "review": {"reviewer": "", "reviewed_at": "", "guide_version": "annotation_v2"}, "pairs": [], "sessions": []}
            for p in read_jsonl(predictions) if not p.get("failed")]
    write_jsonl(output, rows)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("dataset")
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--snapshot", type=Path)
    create.add_argument("--corpus-root", type=Path)
    create.add_argument("--seed", type=int, default=20261005)
    create.add_argument("--count", type=int, default=30)
    feedback = sub.add_parser("feedback")
    feedback.add_argument("--directory", type=Path, default=Path("data/feedback"))
    feedback.add_argument("--output", type=Path, required=True)
    alignment = sub.add_parser("alignments")
    alignment.add_argument("--predictions", type=Path, required=True)
    alignment.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "dataset":
        print(prepare(args.output, corpus_root=args.corpus_root, snapshot_root=args.snapshot, seed=args.seed, count=args.count))
    elif args.command == "feedback":
        print(import_feedback(args.directory, args.output))
    else:
        propose_alignments(args.predictions, args.output)
        print(args.output)


if __name__ == "__main__":
    main()
