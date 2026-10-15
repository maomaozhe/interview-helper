"""Read sealed source-pair predictions, score completed arms, and inspect errors.

No model calls, no changes to source gold, and no changes to sealed runtime.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from eval.common import digest, read_json, read_jsonl, write_json
from eval.metrics import classification_metrics, efficiency
from eval.run import run_section
from eval.validate_gold import require, validate_gold

ROOT = Path(__file__).resolve().parents[2]


def label(row):
    return "FAILED" if row.get("failed") else row.get("result", {}).get("label", "FAILED")


def summarize(rows, golds):
    selected = [golds[row["id"]] for row in rows]
    metric = classification_metrics([row["label"] for row in selected], [label(row) for row in rows], positive="SAME")
    guarded = [row for row in rows if any(call.get("call_stage") == "equivalence_verification" for call in row.get("model_calls", []))]
    downgraded = [row for row in guarded if not row.get("failed") and label(row) == "RELATED"
                 and row.get("result", {}).get("reason_code", "").startswith("equivalence_not_proven_")]
    total_same = sum(row["label"] == "SAME" for row in golds.values())
    best_possible_tp = total_same - metric["fn"]
    primary_rows = [row for row in rows if not row.get("failed")]
    primary_labels = ["SAME" if any(call.get("call_stage") == "equivalence_verification"
                                   for call in row.get("model_calls", [])) else label(row)
                      for row in primary_rows]
    primary_metrics = classification_metrics([golds[row["id"]]["label"] for row in primary_rows],
                                             primary_labels, positive="SAME")
    return {"coverage": len(rows), "gold_classes_visited": dict(Counter(row["label"] for row in selected)),
            "metrics_on_visited_only": metric, "efficiency": efficiency(rows),
            "final_same_recall_upper_bound": best_possible_tp / total_same if total_same else None,
            "final_same_precision_upper_bound": best_possible_tp / (best_possible_tp + metric["fp"])
                if best_possible_tp + metric["fp"] else None,
            "false_merges": [row["id"] for row in rows if golds[row["id"]]["label"] != "SAME" and label(row) == "SAME"],
            "false_splits": [row["id"] for row in rows if golds[row["id"]]["label"] == "SAME"
                             and label(row) not in {"SAME", "FAILED"}],
            "failed": [row["id"] for row in rows if label(row) == "FAILED"],
            "all_classification_errors": [row["id"] for row in rows if label(row) != golds[row["id"]]["label"]],
            "guard_rows": [row["id"] for row in guarded],
            "guard_downgrades": [row["id"] for row in downgraded],
            "guard_correct_non_same_stops": [row["id"] for row in downgraded if golds[row["id"]]["label"] != "SAME"],
            "guard_incorrect_same_downgrades": [row["id"] for row in downgraded if golds[row["id"]]["label"] == "SAME"],
            "guard_failed": [row["id"] for row in guarded if row.get("failed")],
            "same_run_first_stage_label_reconstruction": {
                "status": "reconstructed_labels_on_nonfailed_rows_not_a_fresh_prompt_only_arm",
                "rows": len(primary_rows), "metrics": primary_metrics,
                "basis": "Sealed guard_same code invokes equivalence_verification only after an initial SAME; other successful final labels equal first-stage labels.",
                "excluded_failed_rows": len(rows) - len(primary_rows),
                "first_stage_confidence_and_eight_guard_booleans_not_reconstructed": True,
            },
            "observed_model_identities": sorted({(call.get("model"), call.get("model_revision"))
                                                for row in rows for call in row.get("model_calls", [])}, key=repr),
            "fingerprints": list({digest(row["provider_fingerprint"]): row["provider_fingerprint"]
                                  for row in rows if row.get("provider_fingerprint")}.values())}


def verify_arm(rows, golds, protocol, arm, root):
    ids = [row["id"] for row in rows]
    require(len(ids) == len(set(ids)), "DUPLICATE_OBSERVATION_ID")
    require(set(ids) <= set(golds), "UNKNOWN_OBSERVATION_ID")
    runtime = root / protocol["runtime"]
    expected_prompt = hashlib.sha256((runtime / "prompts" / (protocol["definition"][arm] + ".md"))
                                     .read_text(encoding="utf-8").encode("utf-8")).hexdigest()
    for row in rows:
        require(row["snapshot"] == protocol["snapshot"], "OBSERVATION_SNAPSHOT_CHANGED")
        if row.get("provider_fingerprint"):
            fingerprint = row["provider_fingerprint"]
            require(fingerprint["prompt_sha256"] == expected_prompt, "OBSERVATION_PROMPT_CHANGED")
            require(fingerprint["verify_equivalence"] == protocol["dedup_guard"][arm], "OBSERVATION_GUARD_CHANGED")
        for call in row.get("model_calls", []):
            require(call["prompt_sha256"] == expected_prompt, "CALL_PROMPT_CHANGED")


def score_final(root, dataset, arm, manifest):
    predictions = root / f"dedup.{arm}.jsonl"
    receipt = read_json(predictions.with_suffix(".collection.json"))
    protocol = read_json(root / "dedup.protocol.json")
    require(receipt["predictions_sha256"] == digest(predictions.read_bytes()), "COLLECTION_PREDICTION_CHANGED")
    require(receipt["dataset_sha256"] == digest(manifest), "COLLECTION_GOLD_CHANGED")
    require(receipt["runtime_sha256"] == protocol["runtime_sha256"], "COLLECTION_RUNTIME_CHANGED")
    require(receipt["protocol_sha256"] == digest(protocol), "COLLECTION_PROTOCOL_CHANGED")
    cache_path = root / "dedup-review-score-receipt.json"
    cache = read_json(cache_path) if cache_path.exists() else {}
    prior = cache.get(arm, {})
    if prior.get("predictions_sha256") == receipt["predictions_sha256"]:
        output = Path(prior["score_output"])
        require(output.is_dir(), "SCORE_OUTPUT_MISSING")
    else:
        output = run_section(dataset, "dedup", predictions, root / f"scores-{arm}",
                             review_policy="delegated_agent", threshold_version="quality_gate_v3")
        cache[arm] = {"predictions_sha256": receipt["predictions_sha256"], "score_output": str(output),
                      "review_policy": "delegated_agent", "threshold_version": "quality_gate_v3"}
        write_json(cache_path, cache)
    return {"output": output.as_posix(), "metrics": read_json(output / "metrics.json"),
            "gates": read_json(output / "gates.json"), "collection": receipt}


def percentage(value):
    return "—" if value is None else f"{value * 100:.2f}%"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT / "data/reports/source-optimization-20261014-v1")
    parser.add_argument("--final", action="store_true", help="Require both complete arms and run formal module scoring")
    args = parser.parse_args()
    root = args.root.resolve()
    protocol = read_json(root / "dedup.protocol.json")
    runtime = root / protocol["runtime"]
    require(digest(protocol["runtime_files"]) == protocol["runtime_sha256"], "RUNTIME_INDEX_CHANGED")
    for name, expected in protocol["runtime_files"].items():
        require(digest((runtime / name).read_bytes()) == expected, "SEALED_RUNTIME_CHANGED")
    dataset = ROOT / protocol["dataset"]
    manifest = validate_gold(dataset, "dedup", review_policy="delegated_agent")
    require(digest(manifest) == protocol["dataset_sha256"], "SOURCE_GOLD_CHANGED")
    golds = {row["id"]: row for row in manifest["dedup"]}
    arms, summaries, score_results = {}, {}, {}
    for arm in ("baseline", "candidate"):
        final_path = root / f"dedup.{arm}.jsonl"
        path = final_path if final_path.exists() else root / f"dedup.{arm}.partial.jsonl"
        require(path.is_file(), "OBSERVATIONS_NOT_STARTED")
        if args.final:
            require(final_path.is_file() and final_path.with_suffix(".collection.json").is_file(), "FINAL_COLLECTION_NOT_READY")
        rows = read_jsonl(path)
        verify_arm(rows, golds, protocol, arm, root)
        if args.final:
            require({row["id"] for row in rows} == set(golds), "FINAL_IDS_INCOMPLETE")
            score_results[arm] = score_final(root, dataset, arm, manifest)
        arms[arm] = {row["id"]: row for row in rows}
        summaries[arm] = summarize(rows, golds)
    common = sorted(set(arms["baseline"]) & set(arms["candidate"]))
    changes = []
    for key in common:
        expected, baseline, candidate = golds[key]["label"], label(arms["baseline"][key]), label(arms["candidate"][key])
        if baseline != candidate:
            changes.append({"id": key, "gold": expected, "baseline": baseline, "candidate": candidate,
                            "change": "fixed" if candidate == expected else "regressed" if baseline == expected else "both_wrong_changed"})
    analysis = {"status": "FINAL_SCORED" if args.final else "PARTIAL_DIAGNOSTIC_ONLY",
                "generated_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
                "dataset_sha256": digest(manifest), "runtime_sha256": protocol["runtime_sha256"],
                "total_pairs": len(golds), "paired_covered": len(common), "arms": summaries,
                "changes": changes, "scores": score_results,
                "guard_evidence_limit": "Only call-stage and final reason code stored; original eight verification booleans not retained.",
                "model_identity_limit": "Requested model alias and observed revision recorded; auto revision does not prove identical weights.",
                "production_writes": 0, "model_calls_by_reviewer": 0}
    write_json(root / "dedup-review.json", analysis)
    lines = ["# 真实面经去重对照审核", "", f"生成时间：{analysis['generated_at']}。状态：{analysis['status']}。", "",
             "Gold、原文档案和已封存 runtime 均校验指纹，未修改。审核脚本不发模型请求；下面的调用成本来自 root 统一采集的两 arm 记录。", "",
             f"固定 gold 共 {len(golds)} 对（SAME 43 / RELATED 112 / DIFFERENT 51），来自旧 r293 的真实原文；本次是已见来源诊断回归，不能称为新来源泛化。两 arm 已共同覆盖 {len(common)} 对。", ""]
    if args.final:
        baseline_metrics = score_results["baseline"]["metrics"]
        candidate_metrics = score_results["candidate"]["metrics"]
        lines += [f"两 arm 均为 **BELOW_GATE**。v2+guard 的误并从10对减到8对，但漏认SAME从1对增到7对；SAME F1由 {percentage(baseline_metrics['f1'])} 降到 {percentage(candidate_metrics['f1'])}，accuracy由 {percentage(baseline_metrics['accuracy'])} 降到 {percentage(candidate_metrics['accuracy'])}。当前候选不宜替换生产默认。",
                  "", f"206个配对标签中，候选修正 {sum(row['change'] == 'fixed' for row in changes)} 对、引入 {sum(row['change'] == 'regressed' for row in changes)} 对回退。没有请求失败；质量差异来自有效语义判断。", ""]
    if not args.final:
        lines += ["采集尚未完成，下面只记录已覆盖题对的错误线索，不是完整测评分数或门禁结果。样本按既定顺序采集，前缀以 SAME 为主，不能据前缀无错误推断全体精度。", ""]
    lines += ["| arm | 覆盖 | 已访问 gold 类别 | 非SAME被判SAME | SAME被判其他有效类 | 失败 |", "|---|---:|---|---:|---:|---:|"]
    for arm in ("baseline", "candidate"):
        summary = summaries[arm]
        lines.append(f"| {arm} | {summary['coverage']}/{len(golds)} | {summary['gold_classes_visited']} | {len(summary['false_merges'])} | {len(summary['false_splits'])} | {len(summary['failed'])} |")
    if args.final:
        lines += ["", "两 arm 使用 eval.run、delegated_agent、quality_gate_v3 评分；去重 SAME Precision 门槛95%，Recall门槛85%，只代表该模块题对门禁。", "",
                  "| arm | accuracy | SAME Precision | SAME Recall | SAME F1 | 门禁 |", "|---|---:|---:|---:|---:|---|"]
        for arm in ("baseline", "candidate"):
            score = score_results[arm]
            m = score["metrics"]
            lines.append(f"| {arm} | {percentage(m['accuracy'])} | {percentage(m['precision'])} | {percentage(m['recall'])} | {percentage(m['f1'])} | {score['gates']['status']} |")
        lines += ["", "候选召回与端到端归并为 NOT_RUN：本实验直接判断固定原文题对，没有运行 embedding、候选银行或 resolver。题对通过不证明整体项目质量或生产归并可发布。"]
    else:
        for arm in ("baseline", "candidate"):
            bound = summaries[arm]["final_same_recall_upper_bound"]
            if bound is not None and bound < .85:
                lines += ["", f"固定gold共有43个SAME，{arm} 已出现 {summaries[arm]['metrics_on_visited_only']['fn']} 个未认SAME。即便其余全部正确，最终Recall最多 {percentage(bound)}，已不可能达到85%门槛；这只是由已观测错误得到的上限，不是完整测评分数。"]
            precision_bound = summaries[arm]["final_same_precision_upper_bound"]
            if precision_bound is not None and precision_bound < .95:
                lines += ["", f"{arm} 已出现 {summaries[arm]['metrics_on_visited_only']['fp']} 个非SAME误合并；即便未观测SAME都正确且不再增加误并，最终Precision最多 {percentage(precision_bound)}，已不可能达到95%门槛。"]
    candidate_summary = summaries["candidate"]
    lines += ["", "## Guard 实际变化", "",
              f"候选已复核 {len(candidate_summary['guard_rows'])} 对首判 SAME；有 {len(candidate_summary['guard_downgrades'])} 对降为 RELATED，其中 {len(candidate_summary['guard_correct_non_same_stops'])} 对停止非 SAME 合并，{len(candidate_summary['guard_incorrect_same_downgrades'])} 对错误撤回 gold SAME；guard 失败 {len(candidate_summary['guard_failed'])} 对。",
              "这来自成功的 equivalence_verification 调用及最终 equivalence_not_proven reason_code。八项原始布尔值未写入采集文件，因此不凭空报告每一轴的实际 false 次数。", "",
              "按已封存guard_same控制流，有复核调用的成功行首判必为SAME；其它成功行最终标签就是首判标签。因此可恢复同次运行的首判分类标签，用来说明guard增减了哪些错误。下面不是新的prompt-only模型实验，也没有恢复首判confidence或guard八项布尔值；失败行不参与恢复。", "",
              "| 候选同次运行 | 覆盖 | accuracy | SAME Precision | SAME Recall | FP | FN |", "|---|---:|---:|---:|---:|---:|---:|"]
    primary = candidate_summary["same_run_first_stage_label_reconstruction"]
    for title, m in (("首判标签恢复", primary["metrics"]), ("复核后实际标签", candidate_summary["metrics_on_visited_only"])):
        lines.append(f"| {title} | {m['evaluated_samples']} | {percentage(m['accuracy'])} | {percentage(m['precision'])} | {percentage(m['recall'])} | {m['fp']} | {m['fn']} |")
    lines += ["",
              "## 配对变化", "", "| ID | gold | baseline | candidate | 变化 |", "|---|---|---|---|---|"]
    for row in changes:
        lines.append(f"| {row['id']} | {row['gold']} | {row['baseline']} | {row['candidate']} | {row['change']} |")
    if not changes:
        lines.append("| — | — | — | — | 已共同覆盖部分暂无标签变化 |")
    lines += ["", "## 错例与真实原文", "", "| ID | gold | baseline | candidate | 左题 | 右题 | Agent 原裁定依据 | 最终模型理由 |", "|---|---|---|---|---|---|---|---|"]
    error_ids = sorted(set(summaries["baseline"]["all_classification_errors"]) |
                       set(summaries["candidate"]["all_classification_errors"]))
    clean = lambda text: html.escape(str(text)).replace("|", "\\|").replace("\n", "<br>")
    for key in error_ids:
        gold = golds[key]
        values = [arms[arm].get(key) for arm in ("baseline", "candidate")]
        reasons = "; ".join(f"{arm}: {row.get('result',{}).get('reason_code',row.get('error_code'))}" for arm, row in zip(("baseline", "candidate"), values) if row)
        lines.append(f"| {key} | {gold['label']} | {label(values[0]) if values[0] else '未覆盖'} | {label(values[1]) if values[1] else '未覆盖'} | {clean(gold['left']['text'])} | {clean(gold['right']['text'])} | {clean(gold['review']['basis'])} | {clean(reasons)} |")
    if not error_ids:
        lines.append("| — | — | — | — | 已覆盖部分暂无错例 | — | — | — |")
    else:
        lines += ["", "每个错例的完整原文与审核span可从下列位置复核：", ""]
        for key in error_ids:
            gold = golds[key]
            references = []
            for side in ("left", "right"):
                evidence = gold[side]
                source_path = (dataset / evidence["source_path"]).as_posix()
                line = evidence["source_spans"][0].get("start_line", 1)
                references.append(f"[{side} 原文](<{source_path}:{line}>)")
            lines.append(f"- {key}：{' / '.join(references)}。")
    lines += ["", "## 成本与身份边界", "", "| arm | 调用 | 输入token | 输出token | provider p95 ms | queue p95 ms |整轮p95 ms |", "|---|---:|---:|---:|---:|---:|---:|"]
    for arm in ("baseline", "candidate"):
        m = summaries[arm]["efficiency"]
        lines.append(f"| {arm} | {m['observed_model_calls']} | {m['input_tokens_known']} | {m['output_tokens_known']} | {m['phase_p95_ms']['provider_ms']} | {m['phase_p95_ms']['queue_ms']} | {m['p95_ms']} |")
    lines += ["", f"实际响应模型身份：baseline {summaries['baseline']['observed_model_identities']}；candidate {summaries['candidate']['observed_model_identities']}。revision=auto 是未固定的模型身份，不能宣称同权重实验。",
              "两 arm 使用同一已封存代码、原文输入和共享 gate；并发阶段的 queue 等待与其它评测竞争会影响整轮延迟，不能把整轮 p95 当作纯模型推理速度。v2+guard 同时改变首判提示词及增加复核，尚不能分离两者贡献。",
              "", "## 下一版边界", "", ("以下依据完整固定gold对照提出下一版待验证方向，不改当前gold或封存runtime。" if args.final else
                                                       "以下仅依据已观测错例确定待验证方向，不改当前 gold 或封存 runtime；完整采集后再给出最终建议。")]
    if candidate_summary["guard_incorrect_same_downgrades"]:
        lines.append("候选 guard 已出现错误撤回 SAME，需要区分输入遗漏原文上下文、把同义常规展开当独立要求，以及原始词句不足三种原因。不能通过全判 RELATED 或把所有未指明产品默认补为某产品解决。")
        reason_codes = [arms["candidate"][key].get("result", {}).get("reason_code", "")
                        for key in candidate_summary["guard_incorrect_same_downgrades"]]
        if any("lacks_mysql" in reason for reason in reason_codes):
            lines += ["", "已全文复看隔离级别回退：右题“事物隔离级别”前一条原文为“mysql用什么模型，有什么优势”；“事务等级级别”前一条为“MySql聚类索引和簇类索引”。固定 gold 按各自完整原文裁为 MySQL 题，当前 runner 只传两段 raw_question，guard理由明确指出缺少MySQL上下文。这个失败首先表明标注证据与模型输入的信息不对等，不能直接解释为产品边界规则应该放宽。",
                      "", "建议下一版用新协议和新runtime，不改当前封存数据：",
                      "", "1. 每端独立编译原文局部上下文，含同场段落标题、邻近提问及所需代码/条件，绑定source SHA和字符span；raw_question原样保留，不借另一端补主体。",
                      "2. 首判和guard共享相同证据，把raw_question、context_before、section_context分别作为数据字段。上下文中明确主体才允许补省略，原文没有就保留未知；不能以gold label或canonical ID生成上下文。",
                      "3. 保留作答产物、真实故障范围、禁止方案及条件缺失的阻断，只豁免各自原文能证明的同义/错别字。对现有206对冻结新输入配方，单独比较v2裸题、v2有上下文、v3有上下文，分离补证据和改prompt的贡献。",
                      "4. 采集保存first_stage_result和完整verification八项值，不仅保存最终reason_code，才能确认是哪一轴误拒绝；记录新增上下文token和guard调用成本。"]
        if any("unresolved_acid" in reason for reason in reason_codes):
            lines += ["", "另一个已观测回退把“ACID各自如何实现”与“事务特性及其实现”判不等价，guard理由是ACID实现对象未解析。这里是标准缩写和已具名概念的组成项，不应仅因‘各自’或表述没有展开全称而认定缺指代。下一版可明确：组成项能由问题中已命名概念解析时，不是对象缺失；只对确实缺少所指对象的代词或占位题干阻断。原文编号和OCR拼写也不能成为独立任务条件。这是针对一般概念表达的规则建议，不给模型嵌入本题正确标签。"]
        if any("common_use_cases" in arms["candidate"][key].get("result", {}).get("reason_code", "")
               for key in candidate_summary["false_merges"]):
            lines += ["", "候选还把“项目里实际用了哪些线程池应用”与“一般用途”同判SAME且通过guard。下一版应明确：只有双方均为未具名个人经历模板才允许模板对齐；一端要求具体已有实践、另一端要求一般用途或可选方案，回答边界不同。不能因为项目通常会用到常见用途就把所问事实替换成可能方案。与此同时，‘这个系统’这类措辞若没有引入实际新约束，不能仅凭代词自动撤回已可解析的同义策略题，必须结合该端原文判定。"]
        if any("jmm_is_standard" in arms["candidate"][key].get("result", {}).get("reason_code", "")
               for key in candidate_summary["false_merges"]):
            lines += ["", "候选把JMM与原帖另列的JVM内存模型误对齐，并把它们当成标准缩写与展开名称。标准缩写校验应核实实体及任务，而不能只按近似名称。Java语言内存模型定义并发执行中允许出现的读写行为；JVM规范另行描述堆、栈、方法区等运行时区域。[JLS 17.4](https://docs.oracle.com/javase/specs/jls/se21/html/jls-17.html#jls-17.4)、[JVMS 2.5](https://docs.oracle.com/javase/specs/jvms/se21/html/jvms-2.html#jvms-2.5)。",
                      "", "固定gold依据原帖两题并列的上下文，把JVM内存模型按运行时区域裁定；原帖只有短题名，没有完整作答，因此真实术语指代仍适合另列口径复核。规范资料支持概念区别，不能单独证明原面试官的用词意图；本轮不在看到预测后改该标签。"]
    elif error_ids:
        lines.append("当前发现分类错例，需区分实际主体/任务/产物/关键条件差异与共同技术话题；优先修复仍被判 SAME 的真实边界差异，同时避免削减 gold SAME 召回。")
    else:
        lines.append("已覆盖题对没有可据以修改语义规则的错例，暂不提出未经证据支持的额外规则。")
    lines += ["", "如果抽取或归并已经删除真实条件、残句又没有缺项提示，Judge不能恢复原文。本次不会据 gold 修改候选或全库回填来代替模型实验。"]
    (root / "dedup-review.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"status": analysis["status"], "coverage": {arm: s["coverage"] for arm,s in summaries.items()},
                      "false_merges": {arm: len(s["false_merges"]) for arm,s in summaries.items()},
                      "false_splits": {arm: len(s["false_splits"]) for arm,s in summaries.items()},
                      "guard_bad_downgrades": candidate_summary["guard_incorrect_same_downgrades"],
                      "paired_changes": len(changes)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
