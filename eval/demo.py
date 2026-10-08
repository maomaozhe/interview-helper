"""Offline six-module exercise. All data and observations are synthetic."""
from __future__ import annotations

import argparse
from pathlib import Path

from eval.common import PIPELINES, SECTIONS, digest, write_json, write_jsonl
from eval.prepare import draft
from eval.run import run_section


def demo(output: Path):
    output.mkdir(parents=True, exist_ok=False)
    root = output / "fixture"
    root.mkdir()
    source = "一面\nRedis为什么快？\n如何排查慢查询？\n"
    (root / "source.md").write_text(source, encoding="utf-8")
    gold = {**draft("extraction", "synthetic-source", ["synthetic", "missing_question"]),
            "source_path": "source.md", "source_hash": digest((root / "source.md").read_bytes()), "sample_kind": "positive",
            "sessions": [{"id": "s", "metadata": {"company": "示例公司", "round": "FIRST", "position": None}}],
            "questions": [{"id": key, "session_id": "s", "raw_question": quote, "topic_l1": "Redis", "topic_l2": "性能",
                "question_type": "PRINCIPLE", "source_spans": [{"start_char": source.index(quote), "end_char": source.index(quote)+len(quote), "quote": quote}]}
                for key, quote in (("g1", "Redis为什么快？"), ("g2", "如何排查慢查询？"))], "followups": []}
    extract = {"id": gold["id"], "failed": False, "elapsed_ms": 12, "model_calls": [], "result": {
        "decision": "INCLUDED", "sessions": [{"id": "p-session", "metadata": gold["sessions"][0]["metadata"]}],
        "questions": [{**gold["questions"][0], "id": "p1", "session_id": "p-session"}], "followups": []}}
    alignment = {"id": gold["id"], "kind": "synthetic_alignment", "human_verified": False,
        "prediction_sha256": digest(extract["result"]), "pairs": [{"gold_id": "g1", "prediction_id": "p1"}], "sessions": []}
    labels = [{**draft("labels", "synthetic-labels", ["synthetic", "abstention"]), "response_form": "CODE", "coding_focus": "ENGINEERING"}]
    pairs = [{**draft("pair-"+label, "synthetic-pair-"+label, ["synthetic", label]), "label": label,
              "left": {"id": label+"-a", "text": "问题甲"}, "right": {"id": label+"-b", "text": "问题乙"}}
             for label in ("SAME", "RELATED", "DIFFERENT")]
    retrieval = [{**draft("retrieval-positive", "synthetic-retrieval-positive", ["synthetic"]), "query": "慢查询", "sample_kind": "positive",
                  "filters": {}, "relevance": {"c1": 2, "c2": 1, "c3": 0}},
                 {**draft("retrieval-negative", "synthetic-retrieval-negative", ["synthetic", "negative"]), "query": "无答案",
                  "sample_kind": "negative", "filters": {}, "relevance": {}}]
    routing = [{**draft("routing", "synthetic-routing", ["synthetic", "wrong_argument"]), "message": "前40题",
        "required_tools": ["list_questions"], "forbidden_tools": ["record_review"], "expected_plan": {"top_n": 40},
        "assertions": [{"id": "rows", "path": "result.rows", "op": "not_empty"}]}]
    sql = [{**draft("sql", "synthetic-sql", ["synthetic"]), "request": {"page_size": 2},
        "assertions": [{"id": "ids", "path": "result.rows.*.canonical_question_id", "op": "eq", "value": ["c1", "c2"]},
                       {"id": "zero_models", "path": "model_calls", "op": "length", "value": 0}]}]
    manifest = {"version": "synthetic-demo-v2", "status": "draft", "kind": "fixture", "id_namespace": "canonical",
                "extraction": [gold], "task_labels": labels, "dedup": pairs, "retrieval": retrieval, "routing": routing, "sql": sql}
    write_json(root / "manifest.json", manifest)
    predictions = {"extraction": [extract], "task_labels": [{"id": "labels", "result": {"response_form": "UNKNOWN", "coding_focus": "UNKNOWN"}}],
        "dedup": [{"id": p["id"], "result": {"label": p["label"]}} for p in pairs],
        "retrieval": [{"id": q["id"], "pipelines": {p: {"executed_pipeline": p, "ranked": ["c1", "c2"] if q["sample_kind"] == "positive" else [],
                      "candidate_ids": ["c1", "c2"] if q["sample_kind"] == "positive" else []} for p in PIPELINES}} for q in retrieval],
        "routing": [{"id": "routing", "task_success": True, "tool_trace": [{"name": "list_questions"}], "result": {"plan": {"top_n": 20}, "rows": [1]}}],
        "sql": [{"id": "sql", "model_calls": [], "result": {"rows": [{"canonical_question_id": "c1"}, {"canonical_question_id": "c2"}]}}]}
    write_jsonl(root / "alignments.jsonl", [alignment])
    reports = {}
    for section in SECTIONS:
        file = root / (section + ".predictions.jsonl")
        write_jsonl(file, predictions[section])
        reports[section] = run_section(root, section, file, output / "reports", split="dev",
                                      alignments=root / "alignments.jsonl" if section == "extraction" else None)
    links = "".join(f'<li><a href="{path.relative_to(output).as_posix()}/summary.html">{s}</a></li>' for s, path in reports.items())
    (output / "index.html").write_text('<!doctype html><meta charset="utf-8"><title>评测框架验证</title><h1>SYNTHETIC 合成框架验证</h1><p>六个模块仅验证评分与报告，不代表业务质量。抽取漏题、分类弃权和错误参数是刻意设置的失败。</p><ul>'+links+'</ul>', encoding="utf-8")
    return reports


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    demo(args.output)
    print(args.output / "index.html")


if __name__ == "__main__":
    main()
