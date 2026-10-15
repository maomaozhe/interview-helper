"""Score the complete sealed v3 intervention and retain source audits of SAME FP/FN."""
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import json

from eval.common import digest, inside, read_json, read_jsonl, write_json
from eval.run import run_section
from eval.validate_gold import require, validate_gold


ROOT = Path("data/reports/source-optimization-20261015-v2")
OLD = Path("data/reports/source-optimization-20261014-v1")
GOLD = Path("data/gold/source-reviewed-dedup-20261014-v2")
GOLD_DIGEST = "50af04a36756fc9af27d973d3c976d356603ffc2e59eb40e6669d42b5f28f4b6"

NOTES = {
    "source-dedup-20261014-034": {
        "diagnostic": "SUBJECT_REFERENCE_NOT_RESOLVED",
        "basis": "左侧完整原文第43行‘先问redis’，第51-56行依次问Redis锁、集群、持久化、缓存淘汰，随后第58行才切MySQL；右侧第33行明示redis淘汰策略。左侧实际输入references也含第52行Redis集群和第54行Redis持久化，足以承接Redis对象。v3仍以generic cache vs named Redis判RELATED，不是此次没给独立原文上下文。",
        "independent_requirement_difference": "没有真实独立任务或条件差异；本端Redis指代有真实来源支持，不能因raw省去Redis一词而误拆。",
        "repair": "检查本端可证实的省略主体是否确实进入比较；不要将所有省略产品名的泛问机械降为RELATED，也不能从另一端复制Redis。规则已存在，后续应另作可复核的主体/范围证据诊断，而非据此直接改gold。",
    },
    "source-dedup-20261014-085": {
        "diagnostic": "LOCATING_VS_DIAGNOSIS_SCOPE",
        "basis": "左侧B站原文第20-21行从自己项目的最大表转到‘你是如何定位慢SQL语句的’，目标是找出慢语句；右侧去哪儿第22行是一般‘sql慢怎么排查’，覆盖慢语句已知后的原因分析流程。没有原文把左侧扩为全部排查优化。v3 reason把二者概括为slow SQL troubleshooting，丢掉定位对象与后续根因排查的范围边界。",
        "independent_requirement_difference": "只定位出哪条SQL vs 对慢SQL进行完整排查；存在范围/起点差异。",
        "repair": "保持定位对象、根因分析、优化等被独立要求的工作边界；不因常见答案共享工具而当成整体等价。该区分已写入v3，不能把剩余错误简单归为提示缺少此句。",
    },
    "source-dedup-20261014-089": {
        "diagnostic": "SUBSET_COLLAPSED_TO_SAME",
        "basis": "左侧唯品会第26行只问MySQL为何选B+树；右侧腾讯第22行并列要求索引快的原因、索引底层原理和B+树查询快的原因。二者有交集，但右题的完整索引机制范围更广，左右上下文均无证据删去这些独立要求。v3只按B+树索引rationale对齐。",
        "independent_requirement_difference": "MySQL B+树选型原因 vs 索引速度+底层原理+B+树查询原理的复合要求。",
        "repair": "按原文逐项核对全部要求及双向覆盖，保留包含关系为RELATED；不得把一方较长题干当作同题的任意详略。",
    },
    "source-dedup-20261014-096": {
        "diagnostic": "TARGET_RANGE_AND_REFERENCE_BOUNDARY",
        "basis": "冻结左target只问三类Redis缓存故障怎么解决，右target明确‘是什么，怎么解决’。左原文同一行后面另有‘然后就是经典八股，这三个是什么意思’，实际输入把它放在target_line_suffix/reference_only，并非左target span。若将后续独立定义追问并回当前目标会偏SAME；trace未证明模型具体内部路径，但输出reason声称双方均含定义和解决，与冻结target范围不符。",
        "independent_requirement_difference": "解决方案 vs 定义+解决方案；原文确有后续定义问题，但它不属于本pair的左target。",
        "repair": "区分帮助解析原题的引用与同一行后续新增任务；参考后缀不能自动扩大目标题。保留source span任务边界，并在未来诊断中记录每个要求来自target还是reference。",
    },
    "source-dedup-20261014-119": {
        "diagnostic": "EXPLICIT_ALTERNATIVE_OMITTED",
        "basis": "左侧TEG第94行问TCP为什么需要三次握手；其第96-99行是作者答案，谈两次与三次，未记载需比较四次。右侧另一篇第26行明示‘不是2次或者4次’，增加四次替代方案分析。不能凭常见完整答案应当谈四次，或从右端复制约束，来消除原题范围差异。",
        "independent_requirement_difference": "三次握手必要性 vs 明确比较两次和四次两种替代方案。",
        "repair": "显式替代方案/数量条件必须双向对齐，不用标准答案想象一方也要求同样范围。",
    },
}


def evidence_review(gold, prediction):
    audited = {}
    require("source_evidence" in prediction, "successful v3 result lacks actual source evidence")
    for side in ("left", "right"):
        target = gold[side]
        source = inside(GOLD, target["source_path"])
        raw = source.read_bytes()
        require(digest(raw) == target["source_hash"], "frozen source hash changed")
        text = raw.decode("utf-8")
        supplied = prediction["source_evidence"][side]
        require(supplied["source_hash"] == target["source_hash"] and supplied["source_path"] == target["source_path"]
                and supplied["raw_question"] == target["raw_question"], "side evidence miswired")
        for span in [*supplied["source_spans"], *supplied["references"]]:
            require(type(span.get("start_char")) is int and type(span.get("end_char")) is int
                    and 0 <= span["start_char"] < span["end_char"] <= len(text)
                    and text[span["start_char"]:span["end_char"]] == span["quote"], "source/reference span invalid")
        require(all(ref["use"] == "reference_only" for ref in supplied["references"]), "unmarked reference")
        audited[side] = {"source_file": str(source), "source_hash": target["source_hash"],
            "source_hash_valid": True, "all_supplied_spans_valid": True,
            "supplied_evidence_sha256": digest(supplied), "actual_model_evidence": supplied}
    contexts = [call["comparison_context"] for call in prediction["model_calls"] if call.get("call_stage") == "relation_judgement"]
    require(bool(contexts), "missing relation-stage evidence fingerprint")
    require(all(c["incoming_evidence_sha256"] == audited["left"]["supplied_evidence_sha256"]
                and c["candidate_evidence_sha256"] == audited["right"]["supplied_evidence_sha256"] for c in contexts),
            "model evidence fingerprint mismatch")
    return audited


def main():
    prediction_file = ROOT / "dedup.candidate.jsonl"
    require(prediction_file.is_file(), "wait for complete v3 final; partials cannot be scored")
    manifest = validate_gold(GOLD, "dedup", review_policy="delegated_agent")
    require(digest(manifest) == GOLD_DIGEST, "dedup frozen gold changed")
    golds = manifest["dedup"]
    predicted = read_jsonl(prediction_file)
    require(len(predicted) == 206 and {p["id"] for p in predicted} == {g["id"] for g in golds}, "complete 206-pair output required")
    collection = read_json(ROOT / "dedup.candidate.collection.json")
    protocol = read_json(ROOT / "dedup.protocol.json")
    require(collection["predictions_sha256"] == digest(prediction_file.read_bytes())
            and collection["protocol_sha256"] == digest(protocol), "v3 sealed receipt mismatch")
    require(protocol["dedup_guard"]["candidate"] is False and protocol["dedup_source_context"]["candidate"] is True,
            "unexpected intervention configuration")
    by_gold, by_pred = {g["id"]: g for g in golds}, {p["id"]: p for p in predicted}
    evidence_checks = {p["id"]: evidence_review(by_gold[p["id"]], p) for p in predicted if not p.get("failed")}
    old_receipt = read_json(OLD / "dedup-review-score-receipt.json")
    arms, old_predictions = {}, {}
    for name, item in old_receipt.items():
        score = Path(item["score_output"])
        metrics, metadata = read_json(score / "metrics.json"), read_json(score / "manifest.json")
        require(metadata["dataset_sha256"] == GOLD_DIGEST, "old arm used different gold")
        require(metadata["predictions_sha256"] == digest((score / "predictions.jsonl").read_bytes()), "old arm hash changed")
        arm_name = "v1_baseline" if name == "baseline" else "v2_with_guard"
        old_predictions[arm_name] = {p["id"]: p for p in read_jsonl(score / "predictions.jsonl")}
        arms[arm_name] = {"score_output": str(score), "metrics": metrics, "gates": read_json(score / "gates.json"),
            "collection": read_json(score / "collection.json"), "predictions_sha256": metadata["predictions_sha256"]}
    score_receipt = ROOT / "dedup-v3-score-receipt.json"
    if score_receipt.exists():
        saved = read_json(score_receipt)
        require(saved["predictions_sha256"] == collection["predictions_sha256"], "existing v3 score belongs to another final")
        score = Path(saved["score_output"])
    else:
        score = run_section(GOLD, "dedup", prediction_file, ROOT / "scored-dedup-v3",
            review_policy="delegated_agent", threshold_version="quality_gate_v3")
        write_json(score_receipt, {"score_output": str(score), "predictions_sha256": collection["predictions_sha256"],
            "dataset_sha256": GOLD_DIGEST, "review_policy": "delegated_agent", "threshold_version": "quality_gate_v3"})
    arms["v3_context_no_guard"] = {"score_output": str(score), "metrics": read_json(score / "metrics.json"),
        "gates": read_json(score / "gates.json"), "collection": collection, "predictions_sha256": collection["predictions_sha256"]}
    for arm in arms.values():
        raw_observations = read_jsonl(Path(arm["score_output"]) / "predictions.jsonl")
        arm["model_call_status_counts"] = dict(Counter(call.get("status") for row in raw_observations for call in row.get("model_calls", [])))
        arm["failed_model_attempts"] = [{"id": row["id"], "call_index": index, "status": call.get("status"),
            "error_code": call.get("error_code"), "retry_count": call.get("retry_count"), "call_stage": call.get("call_stage")}
            for row in raw_observations for index, call in enumerate(row.get("model_calls", [])) if call.get("status") != "SUCCEEDED"]
    errors = [g for g in golds if (g["label"] == "SAME") != ((by_pred[g["id"]].get("result") or {}).get("label") == "SAME")]
    require({g["id"] for g in errors} == set(NOTES), "new final FP/FN require explicit full-source review before reporting")
    error_reviews = []
    for gold in errors:
        prediction = by_pred[gold["id"]]
        error_reviews.append({"id": gold["id"], "error": "FN" if gold["label"] == "SAME" else "FP",
            "gold_label": gold["label"], "prediction": prediction["result"], "gold_review": gold["review"],
            "left": gold["left"], "right": gold["right"], "source_evidence": evidence_checks[gold["id"]],
            "full_originals_read": True, "diagnosis": NOTES[gold["id"]],
            "old_predictions": {name: rows[gold["id"]].get("result") for name, rows in old_predictions.items()},
            "gold_unchanged": True})
    transitions = {}
    for name, old in old_predictions.items():
        old_wrong = {g["id"] for g in golds if (old[g["id"]].get("result") or {}).get("label") != g["label"]}
        new_wrong = {g["id"] for g in golds if (by_pred[g["id"]].get("result") or {}).get("label") != g["label"]}
        transitions[name] = {"old_all_class_errors": len(old_wrong), "new_all_class_errors": len(new_wrong),
            "fixed_ids": sorted(old_wrong-new_wrong), "regressed_ids": sorted(new_wrong-old_wrong),
            "retained_error_ids": sorted(old_wrong&new_wrong)}
    receipt = {"created_at": datetime.now(timezone.utc).isoformat(), "reviewer_kind": "agent", "human_verified": False,
        "agent_verified": True, "dataset": str(GOLD), "dataset_sha256": GOLD_DIGEST,
        "gold_freeze_sha256": digest((GOLD / "freeze.json").read_bytes()), "script_sha256": digest(Path(__file__).read_bytes()),
        "scope": "Complete 206 seen-source diagnostic pairs; no new gold, production labels or unseen generalization claim.",
        "intervention": {"prompt": "dedup_judge_v3", "independent_source_context": "dedup_source_context_v1", "equivalence_guard": False,
            "attribution": "joint intervention; do not attribute changes to prompt alone",
            "model": "ark-code-latest", "model_revision": "auto", "same_weights_verified": False},
        "gold_class_counts": dict(Counter(g["label"] for g in golds)), "arms": arms,
        "source_evidence_validation": {"checked_successful_pairs": len(evidence_checks),
            "failed_pairs": sum(bool(p.get("failed")) for p in predicted), "side_bindings_hashes_and_all_reference_spans_valid": True,
            "guard_calls": sum(c.get("call_stage") != "relation_judgement" for p in predicted for c in p.get("model_calls", []))},
        "same_error_reviews": error_reviews, "classification_transitions": transitions,
        "full_classification_error_ids": sorted(g["id"] for g in golds if by_pred[g["id"]].get("result", {}).get("label") != g["label"]),
        "external_model_calls_by_reviewer": 0, "production_writes": 0, "gold_unchanged": True,
        "limitations": ["Only remaining SAME FP/FN received new full-source narrative audit; other label errors remain in metrics and error IDs.",
            "Source evidence confirms model inputs and available context; reason codes do not reveal hidden reasoning.",
            "Same configured model alias/auto revision does not establish identical model weights across dates/runtimes.",
            "Historical and new runtime hashes differ, and source context/guard/prompt changed together."]}
    write_json(ROOT / "dedup-v3-source-review.json", receipt)
    pct = lambda value: f"{100*value:.2f}%" if value is not None else "N/A"
    lines = ["# 去重 v3 真实来源审核", "", "同一冻结206对真实来源（SAME 43、RELATED 112、DIFFERENT 51）的完整结果已评分；全部失败保留在分母。", "",
        "| 完整实验 | SAME TP / FP / FN | SAME P / R / F1 | 三分类准确率 | 失败 | 调用 / 预算tokens | Gate |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |"]
    for name, arm in arms.items():
        m, c = arm["metrics"], arm["collection"]
        lines.append(f"| {name} | {m['tp']} / {m['fp']} / {m['fn']} | {pct(m['precision'])} / {pct(m['recall'])} / {pct(m['f1'])} | {pct(m['accuracy'])} | {c['failed']} / {c['samples']} | {c['budget']['charged_calls']} / {c['budget']['charged_tokens']:,} | {arm['gates']['status']} |")
    lines += ["", "v3是prompt v3、每端独立source context和关闭guard的联合干预，runtime也不同；不能把变化归因于提示词单项。模型配置均为ark-code-latest、revision=auto，但未验证同权重。旧v1、旧v2+guard的完整206结果及评分未改动，也未用partial替代final。", "",
        "样本失败与请求失败分别保留：v1和v2+guard请求失败均为0；v3有1次InternalServerError（pair 060，首尝试），重试后该样本成功，所以是207次请求、206个成功样本。该次usage未知，预算扣计含预留；v3已知输入492,589、输出5,984 tokens，预算扣计534,781不能当作实际账单。", "",
        "每个成功pair核对了左右独立source hash、target与全部reference字符span，实际模型调用的incoming/candidate evidence指纹均与各端独立证据一致；不能把剩余误判归咎于左右引用错接。模型有上下文仍可能未正确应用它。", "",
        "## 剩余误合并/漏合并的原文依据", ""]
    for error in error_reviews:
        note = error["diagnosis"]
        lines += [f"### {error['id']} — {error['error']} — gold {error['gold_label']} → {error['prediction']['label']}", "",
            f"实际reason_code：`{error['prediction']['reason_code']}`。", ""]
        for side in ("left", "right"):
            item = error[side];span=item["source_spans"][0];path=inside(GOLD,item["source_path"]).resolve().as_posix()
            lines += [f"{side}：{item['raw_question']}", "",
                f"[原文第{span['start_line']}行](<{path}:{span['start_line']}>)；SHA256 `{item['source_hash']}`；字符span " + ", ".join(f"[{s['start_char']},{s['end_char']})" for s in item['source_spans']) + "。", ""]
        lines += ["冻结gold依据："+error["gold_review"]["basis"], "", "本次独立原文审核："+note["basis"], "",
            "实际任务边界："+note["independent_requirement_difference"], "", "通用改进建议："+note["repair"], ""]
    lines += ["## 可复核记录与限制", "",
        "`dedup-v3-source-review.json`保存三组完整metrics/collection/预算/失败、每个剩余FP/FN两端raw/source/hash/span、实际独立reference输入及调用指纹、冻结review与旧预测变化。`dedup-v3-score-receipt.json`绑定本次final与正式`eval.run`评分目录；threshold=quality_gate_v3、review_policy=delegated_agent。", "",
        "本次只对剩余SAME FP/FN新增全文叙述审核；RELATED与DIFFERENT之间的错误仍保留在三分类准确率、混淆矩阵及错误ID中，未通过只报SAME指标隐藏。reason_code只是观测到的模型解释，不代表已证明内部误判因果。", "",
        "所有来源为seen-source诊断，gold与历史结果未改；本审核没有付费模型调用、没有生产标签发布。预算tokens不是货币账单；未知usage与实际tokens保留在各完整metrics/collection，耗时受共享运行环境影响。", ""]
    (ROOT / "dedup-v3-source-review.md").write_text("\n".join(lines),encoding="utf-8")
    print(json.dumps({"score":str(score),"v3":{k:arms['v3_context_no_guard']['metrics'][k] for k in ('tp','fp','fn','precision','recall','f1','accuracy')},"remaining_same_errors":len(error_reviews)},ensure_ascii=False))


if __name__ == "__main__":
    main()
