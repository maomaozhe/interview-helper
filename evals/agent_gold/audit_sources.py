"""Verify and package immutable, original-interview Agent reference datasets.

This command does not generate labels, call a model, or mutate production data.
The HTML viewer includes the actual reviewed text and original source archives.
"""
from __future__ import annotations

import argparse
import html
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from eval.common import SECTIONS, digest, inside, read_json, write_json
from eval.source_gold import release_eligibility_issues
from eval.validate_gold import require, validate_gold
from interview_intelligence.ingestion.snapshot import decode_source


def source_hashes(manifest):
    hashes = {r["source_hash"] for section in ("extraction", "task_labels")
              for r in manifest[section] if r.get("source_hash")}
    hashes.update(side["source_hash"] for r in manifest["dedup"] for side in (r["left"], r["right"]))
    for reference in manifest.get("relevance_evidence", {}).values():
        hashes.update(r["source_hash"] for r in reference.get("source_evidence", [])
                      + reference.get("canonical_variants_review", []))
    return hashes


def audit(snapshot: Path, datasets: list[Path], output: Path):
    require(not output.exists(), "SOURCE_AUDIT_OUTPUT_ALREADY_EXISTS")
    facts = read_json(snapshot / "facts.json")
    snapshot_manifest = read_json(snapshot / "manifest.json")
    require(digest(facts) == snapshot_manifest["facts_sha256"], "SOURCE_SNAPSHOT_FACTS_CHANGED")
    documents = {d["source_hash"]: d for d in facts["documents"]}
    package = {"schema": "source_gold_audit_v1", "created_at": datetime.now(timezone.utc).isoformat(),
               "review_policy": "delegated_agent", "human_verified": False,
               "snapshot": facts["snapshot"], "snapshot_path": snapshot.as_posix(),
               "facts_sha256": digest(facts), "facts_file_sha256": digest((snapshot / "facts.json").read_bytes()),
               "datasets": {}, "model_calls": 0, "production_writes": 0,
               "scope": "Reference/source validation only; not current production quality or unseen generalization."}
    all_sources, evidence_spans, cards = {}, 0, []
    escape = html.escape

    def card(module, key, text, basis):
        cards.append(f'<details class="sample" data-module="{escape(module)}"><summary>{escape(module)} · {escape(key)}</summary>'
                     f'<pre>{escape(text)}</pre><p>{escape(basis)}</p></details>')

    for dataset in datasets:
        manifest = None
        sections = []
        # Each requested section independently passes formal delegated provenance.
        from eval.validate_gold import load_dataset
        loaded = load_dataset(dataset)
        for section in SECTIONS:
            if loaded[section]:
                manifest = validate_gold(dataset, section, review_policy="delegated_agent")
                sections.append(section)
        require(manifest is not None, "EMPTY_SOURCE_DATASET")
        require(manifest["snapshot"] == facts["snapshot"], "SOURCE_DATASET_SNAPSHOT_MISMATCH")
        require(manifest.get("facts_sha256") == digest(facts), "SOURCE_DATASET_FACTS_HASH_MISMATCH")
        hashes = source_hashes(manifest)
        require(hashes <= documents.keys(), "GOLD_SOURCE_NOT_IN_REAL_SNAPSHOT")
        for sha in hashes:
            path = inside(dataset, "sources/" + sha + ".md")
            original = inside(snapshot, documents[sha]["source_path"])
            require(path.read_bytes() == original.read_bytes() and digest(path.read_bytes()) == sha,
                    "GOLD_ARCHIVE_DIFFERS_FROM_ORIGINAL_INTERVIEW")
            all_sources[sha] = {"original_path": documents[sha]["path"], "source_hash": sha,
                                "text": decode_source(original.read_bytes())}
        evidence = {"path": dataset.as_posix(), "dataset_sha256": digest(manifest),
                    "manifest_file_sha256": digest((dataset / "manifest.json").read_bytes()),
                    "freeze_file_sha256": digest((dataset / "freeze.json").read_bytes()),
                    "source_count": len(hashes), "source_evidence_contract": manifest.get("source_evidence_contract"),
                    "sections": {section: {"samples": len(manifest[section]),
                        "release_eligibility_issues": release_eligibility_issues(manifest, section)} for section in sections}}
        if manifest["extraction"]:
            rows = manifest["extraction"]
            evidence["extraction"] = {"positive_documents": sum(r["sample_kind"] == "positive" for r in rows),
                                      "negative_documents": sum(r["sample_kind"] == "negative" for r in rows),
                                      "questions": sum(len(r["questions"]) for r in rows),
                                      "sessions": sum(len(r["sessions"]) for r in rows),
                                      "followups": sum(len(r.get("followups", [])) for r in rows)}
            for row in rows:
                card("extraction", row.get("original_path", row["id"]),
                     "\n\n".join(q["id"] + "\n" + q["raw_question"] for q in row["questions"]),
                     row["review"]["basis"])
                evidence_spans += sum(len(q["source_spans"]) + len(q.get("corroborating_spans", [])) for q in row["questions"])
        if manifest["task_labels"]:
            evidence["task_labels"] = dict(Counter(r["response_form"] + "/" + r["coding_focus"] for r in manifest["task_labels"]))
            for row in manifest["task_labels"]:
                card("task_labels", row["id"], row["response_form"] + "/" + row["coding_focus"] + "\n" + row["raw_question"], row["review"]["basis"])
                evidence_spans += len(row["source_spans"])
        if manifest["dedup"]:
            evidence["dedup"] = dict(Counter(r["label"] for r in manifest["dedup"]))
            for row in manifest["dedup"]:
                card("dedup", row["id"], row["label"] + "\n左：" + row["left"]["text"] + "\n右：" + row["right"]["text"], row["review"]["basis"])
                evidence_spans += len(row["left"]["source_spans"]) + len(row["right"]["source_spans"])
        if manifest["retrieval"]:
            evidence["retrieval_scope"] = manifest.get("retrieval_evidence_scope")
            evidence["relevance_judgments"] = dict(Counter(str(g) for r in manifest["retrieval"] for g in r["relevance"].values()))
            for row in manifest["retrieval"]:
                reviewed = []
                for key, grade in row["relevance"].items():
                    original = manifest["relevance_evidence"][key]["source_evidence"][0]
                    reviewed.append(f"grade={grade} · {key}\n{original['raw_question']}\n{row['relevance_reviews'][key]['basis']}")
                card("retrieval", row["id"], row["query"] + "\n\n" + "\n\n".join(reviewed), row["review"]["basis"])
            evidence_spans += sum(len(v["source_spans"]) for r in manifest["relevance_evidence"].values()
                                  for v in r["source_evidence"] + r["canonical_variants_review"])
        package["datasets"][manifest["version"]] = evidence

    package["unique_source_archives"] = len(all_sources)
    package["checked_span_references"] = evidence_spans
    package["status"] = "SOURCE_EVIDENCE_VERIFIED"
    output.mkdir(parents=True)
    write_json(output / "audit.json", package)
    write_json(output / "source-index.json", [{k: v for k, v in source.items() if k != "text"}
                                            for source in all_sources.values()])
    source_cards = [f'<details class="sample" data-module="sources"><summary>sources · {escape(source["original_path"])}</summary>'
                    f'<p>SHA-256：{sha}</p><pre>{escape(source["text"])}</pre></details>' for sha, source in all_sources.items()]
    document = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>真实面经 Gold 原文审核</title>'
    document += '<style>body{font:16px system-ui;color:#17243b;background:#f6f8fb;max-width:1150px;margin:32px auto;padding:0 20px}input,select{padding:9px;margin:8px}details{background:white;border:1px solid #d4deea;border-radius:8px;margin:12px 0;padding:14px}summary{cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.6}p{line-height:1.7}</style>'
    document += f'<h1>真实面经 Gold 原文审核</h1><p>用户授权 Agent 审核 · 人工确认 false · {len(all_sources)} 份真实来源档案 · {evidence_spans} 次跨度校验。</p>'
    document += '<p>绑定历史 r293 快照；不是当前线上质量结论。检索为锚点诊断，不能作为全库 Recall 或发布通过证据。</p>'
    document += '<label>模块 <select id="module"><option value="all">全部</option><option>extraction</option><option>task_labels</option><option>dedup</option><option>retrieval</option><option>sources</option></select></label><label>检索文本 <input id="query" placeholder="题干、判定或原文件名"></label>'
    document += "".join(cards + source_cards)
    document += '<script>const filter=()=>{const m=document.getElementById("module").value,q=document.getElementById("query").value.toLowerCase();document.querySelectorAll(".sample").forEach(d=>d.hidden=(m!=="all"&&d.dataset.module!==m)||!d.textContent.toLowerCase().includes(q))};document.getElementById("module").addEventListener("change",filter);document.getElementById("query").addEventListener("input",filter);</script></html>'
    (output / "index.html").write_text(document, encoding="utf-8")
    return package


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--dataset", action="append", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.snapshot, args.dataset, args.output)
    print(json.dumps({"status": result["status"], "datasets": len(result["datasets"]),
                      "source_archives": result["unique_source_archives"],
                      "span_references": result["checked_span_references"], "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
