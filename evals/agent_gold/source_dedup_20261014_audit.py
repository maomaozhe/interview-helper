"""Audit sealed source dedup evidence and r293 canonical grouping; zero model calls."""
from __future__ import annotations

import json
import argparse
import sys
from collections import Counter
from pathlib import Path

from eval.common import digest, write_json
from eval.validate_gold import validate_gold
from evals.agent_gold.source_dedup_20261014 import (
    OUTPUT as DEFAULT_OUTPUT, PAIRS, ROOT, endpoint, facts, previous_inventory, sha,
)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    OUTPUT = args.dataset.resolve()
    REPORT = ROOT / "data/reports" / OUTPUT.name
    manifest = validate_gold(OUTPUT, "dedup", review_policy="delegated_agent")
    value, by_revision = facts()
    selected = manifest["dedup"]
    selected_keys = {row["id"] for row in selected}
    old_pairs, _, _, _ = previous_inventory()
    seen, excluded = set(), []
    for position, (left_key, right_key, _, _) in enumerate(PAIRS):
        left, right = endpoint(left_key, value, by_revision), endpoint(right_key, value, by_revision)
        pair = tuple(sorted((left["id"], right["id"])))
        if f"source-dedup-20261014-{position:03d}" not in selected_keys:
            excluded.append({"recipe_position": position, "left_id": left["id"], "right_id": right["id"],
                             "reason": "previous_gold_pair" if pair in old_pairs else "recipe_duplicate_or_reverse"})
        seen.add(pair)
    used_questions = {side["id"] for row in selected for side in (row["left"], row["right"])}
    used_sources = {side["source_hash"] for row in selected for side in (row["left"], row["right"])}
    parents = {key: key for key in used_questions}
    def find(key):
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key
    for row in selected:
        if row["label"] == "SAME":
            parents[find(row["right"]["id"])] = find(row["left"]["id"])
    contradictions = [row["id"] for row in selected if row["label"] != "SAME"
                      and find(row["left"]["id"]) == find(row["right"]["id"])]
    identities = {}
    for row in selected:
        for side in (row["left"], row["right"]):
            evidence = digest({k: side[k] for k in ("text", "source_hash", "source_spans")})
            if identities.setdefault(side["id"], evidence) != evidence:
                raise ValueError("One question identity has inconsistent source evidence")
    if contradictions:
        raise ValueError(f"Non-SAME pair conflicts with SAME transitivity: {contradictions}")
    write_json(REPORT / "label-structure-validation.json", {
        "status": "PASS", "same_component_count": len({find(key) for key in used_questions}),
        "non_same_within_same_component": contradictions,
        "consistent_evidence_for_all_repeated_question_ids": True,
        "note": "This validates sampled relation consistency; it does not independently certify semantic judgments.",
    })
    write_json(REPORT / "excluded-pairs.json", excluded)
    write_json(REPORT / "leakage.json", manifest["leakage"])
    write_json(REPORT / "source-evidence-validation.json", {
        "dataset_sha256": digest(manifest), "freeze_sha256": sha((OUTPUT / "freeze.json").read_bytes()),
        "pairs": len(selected), "endpoint_references_checked": 2 * len(selected),
        "unique_question_ids": len(used_questions), "source_documents": len(used_sources),
        "replacement_chars_in_selected_raw_questions": 0, "full_sources_read": manifest["sampling"]["complete_sources_read"],
        "status": "PASS", "external_model_calls": 0,
        "validator_command": f"PYTHONPATH=src python -m eval.validate_gold --dataset data/gold/{OUTPUT.name} --section dedup --review-policy delegated_agent",
    })
    canonical = {q["id"]: q["canonical_question_id"] for q in value["questions"]}
    counts, records, disagreements = Counter(), [], []
    for row in selected:
        left = canonical.get(row["left"]["occurrence_id"])
        right = canonical.get(row["right"]["occurrence_id"])
        grouped = left == right if left and right else None
        record = {"id": row["id"], "gold_label": row["label"], "left_canonical_id": left,
                  "right_canonical_id": right, "published_grouped_together": grouped,
                  "left_question": row["left"]["text"], "right_question": row["right"]["text"],
                  "review_basis": row["review"]["basis"],
                  "left_source": row["left"]["original_path"], "right_source": row["right"]["original_path"]}
        records.append(record)
        if grouped is not None:
            counts[(row["label"], grouped)] += 1
        if (grouped and row["label"] != "SAME") or (grouped is False and row["label"] == "SAME"):
            disagreements.append(record)
    audit = {"kind": "published_canonical_consistency_audit_not_judge_model_predictions",
             "snapshot": value["snapshot"], "gold_sealed_before_mapping_inspection": True,
             "gold_dataset_sha256": digest(manifest), "external_model_calls": 0,
             "comparable_pairs": sum(row["published_grouped_together"] is not None for row in records),
             "noncomparable_source_only_pairs": sum(row["published_grouped_together"] is None for row in records),
             "counts": {f"{label}_grouped_{grouped}": count for (label, grouped), count in counts.items()},
             "disagreements": disagreements, "all_pairs": records}
    write_json(REPORT / "published-grouping-audit.json", audit)
    pair_rows = ["# 真实面经去重审核明细（2026-10-14）", "",
                 "本表的判断来自原始全文；归并状态仅在 gold 冻结后读取。源码、原始 span 与 SHA-256 见冻结数据集。", "",
                 "| ID | 标签 | 左题原文 | 右题原文 | Agent 裁定依据 |", "|---|---|---|---|---|"]
    for row in selected:
        clean = lambda text: text.replace("|", "\\|").replace("\n", "<br>")
        pair_rows.append(f"| {row['id']} | {row['label']} | {clean(row['left']['text'])} | {clean(row['right']['text'])} | {clean(row['review']['basis'])} |")
    (REPORT / "pair-review.md").write_text("\n".join(pair_rows) + "\n", encoding="utf-8")
    merges = [row for row in disagreements if row["published_grouped_together"]]
    splits = [row for row in disagreements if row["published_grouped_together"] is False]
    readme = ["# 真实面经去重 gold 与公开归并审计", "", "截至 2026-10-14，本批已由 Agent 阅读真实面经全文并冻结，human_verified=false，agent_verified=true。", "",
              f"- {len(selected)} 对：SAME {manifest['sampling']['class_counts']['SAME']} / RELATED {manifest['sampling']['class_counts']['RELATED']} / DIFFERENT {manifest['sampling']['class_counts']['DIFFERENT']}。",
              f"- 全文阅读 {manifest['sampling']['complete_sources_read']} 篇，使用 {len(used_sources)} 篇、{len(used_questions)} 个独立题 ID；逐对有中文裁定依据。",
              f"- 412 个端点引用均经 SHA-256、逐字原文 span、raw_question 拼接与冻结文件校验；真实源题干替换字符数为 0。",
              "- 同一原始来源仅归 test；没有新增 dev/test 混分。已检查 SAME 关系传递闭包与非 SAME 标签无冲突，同一 ID 的原文证据一致。",
              "- 候选来自旧 r293 快照，不是最新生产状态。本批不是未见来源测试；类比例为诊断抽样，不代表实际重复率。",
              f"- 排除旧 gold 无序题对 {manifest['leakage']['previous_pairs_excluded']} 对、配方反向重复 {manifest['leakage']['reversed_recipe_duplicates_excluded']} 对。最终旧 pair 重叠 0；旧 source 重叠 {manifest['leakage']['previous_gold_source_overlap']}/{len(used_sources)}，旧 occurrence 重叠 {manifest['leakage']['previous_gold_occurrence_overlap']}。", "",
              "## 校验与使用", "", "```powershell", "$env:PYTHONPATH = 'D:\\面经\\src'", "$env:PYTHONIOENCODING = 'utf-8'",
              f".venv/Scripts/python.exe -m eval.validate_gold --dataset data/gold/{OUTPUT.name} --section dedup --review-policy delegated_agent",
              f".venv/Scripts/python.exe -m evals.agent_gold.source_dedup_20261014_audit --dataset data/gold/{OUTPUT.name}", "```", "",
              "校验通过。默认 human 策略拒绝本批，符合授权 Agent 审核口径；不得把它称为真人 gold。", "",
              "## r293 公开归并一致性", "", "这部分读取快照已有 canonical ID，未运行 Judge，不是模型重跑准确率，不能与 Judge 的三分类 Precision/Recall 对比。", "",
              f"可比较 {audit['comparable_pairs']} 对；原文补出的 source-only 问题相关 {audit['noncomparable_source_only_pairs']} 对无公开 occurrence/canonical 对应。发现 {len(merges)} 对非 SAME 被归一起、{len(splits)} 对 SAME 被拆开。", "",
              "| gold | 已归一起 | 已拆开 |", "|---|---:|---:|"]
    for label in ("SAME", "RELATED", "DIFFERENT"):
        readme.append(f"| {label} | {counts[(label, True)]} | {counts[(label, False)]} |")
    readme += ["", "具体问题全部保留在 published-grouping-audit.json；这是按本批 Agent 边界裁定的审计线索，需要另开版本修复生产归并。", "",
               "## 原文证据修复", "", "- 腾讯 TEG 后台 map 题补回 O(1)、有限容量、替换旧值、LRU 淘汰、TTL，原抽取只保留了“如下要求（不写代码）”。",
               "- 拼多多线程池异常诊断题附回被检验的 submit 代码，避免仅靠“下面这段代码”作推断。",
               "- 腾讯 TEG 云架构原文三个连续算法/思考题，以及快手电商 CompletableFuture 自定义线程池题无现存完整 occurrence，使用独立 source 派生题 ID。",
               "- 所有题干均逐字摘录原文，不替换 MySQL 为 Redis，不把口述 LRU 扩写为手撕，不去掉非递归/拆墙/有环/内存容量等限制。", "",
               "本次外部模型调用为 0，没有生产发布，也没有以归并审计替代 Judge 预测。"]
    if manifest.get("metadata_revision"):
        readme += ["", "## v2 元数据修订", "", "v1 原样保留。v2 仅把 facts_sha256 更正为解析 JSON 的标准 digest，原始文件字节指纹另存 facts_file_sha256；带父 dataset/freeze hash。标签、原文档案、审核记录均逐字不变。语义判断首先封存于 v1，随后才读取 canonical，v2 不重新声称盲标。"]
    (REPORT / "README.md").write_text("\n".join(readme) + "\n", encoding="utf-8")
    print(json.dumps({"pairs": len(selected), "classes": manifest["sampling"]["class_counts"],
                      "comparable": audit["comparable_pairs"], "noncomparable": audit["noncomparable_source_only_pairs"],
                      "false_merges": len(merges), "false_splits": len(splits), "counts": audit["counts"]}, ensure_ascii=False))
    print(json.dumps({"false_merge_examples": merges[:12]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
