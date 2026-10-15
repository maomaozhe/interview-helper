"""Strict source review of predeclared repair failures, never a full-set rescore."""
from datetime import datetime, timezone
import json
from pathlib import Path

from eval.common import digest, inside, read_json, read_jsonl, write_json, write_jsonl
from eval.scoring import score_extraction
from eval.validate_gold import require, validate_gold
from evals.agent_gold.source_optimization_alignment_20261014 import bare, source_candidates, stamp
from interview_intelligence.ingestion.snapshot import decode_source


ROOT = Path("data/reports/source-optimization-20261015-v2")
GOLD = Path("data/gold/source-reviewed-extraction-20261014-v3")
GOLD_DIGEST = "08f088debe4322b943e582fadc4f6d734621d19bde4f91768ffb466310084ab7"
HASHES = {
    "quote-repair-v2": "256ae2cc94edddbd6679319e54b2c3031e6c72c6b16ecca46e9a4ade6da20aa6",
    "ledger-repair-v2": "7a3460e3a6d2ea7174b48280241da9e5c0d3ae49edb76346a519e2db14e69dfb",
}
SESSIONS = {
    "quote-repair-v2": {"d3e713a6755b": {"s1": "bigo_2", "s2": "bigo_1"}},
    "ledger-repair-v2": {"dee8c70df3c5": {"s1": "i1"}, "d3e713a6755b": {"s1": "I1", "s2": "I2"},
                         "6a559eecded4": {"s1": "I1"}, "1acb80963445": {"s1": "I1"}},
}
MANUAL = {
    "g-new-dee8c70df3c5-001": ("i1/q1", "raw含紧接的候选人回答尾部，normalized只保留自我介绍要求；同一原文任务，未新增约束。"),
    "g-new-dee8c70df3c5-037": ("i1/q28", "正文MCP缩写和OCR完整英文名指同一面试任务；Tool注册机制对比完整保留。"),
    "g-new-dee8c70df3c5-039": ("i1/q30", "正文Max Value与OCR最大值等义，整数数组、窗口k及逐窗口输出条件全部保留。"),
}


def gids(prefix, suffixes, basis):
    return {f"g-new-{prefix}-{i:03}": basis for i in suffixes}


MISSING = {
    "quote-repair-v2": {
        **gids("d3e713a6755b", [1, 16], "两场原文分别明确要求自我介绍，最终预测没有对应独立题；不能以另一场业务介绍替代。"),
        **gids("d3e713a6755b", [45, 46], "预测bigo_1/q29将8个与9个西瓜两道独立问题合并，按冻结粒度不可一对一匹配；两题内容均出现不等于两个独立occurrence已恢复。"),
    },
    "ledger-repair-v2": {
        **gids("dee8c70df3c5", [2, 3], "i1/q2合并Java内存模型与堆栈区别两个独立要求，保留unmatched。"),
        **gids("dee8c70df3c5", [4, 5], "i1/q3合并HashMap底层实现与1.7/1.8区别两个独立要求，保留unmatched。"),
        **gids("dee8c70df3c5", [7, 8], "i1/q5合并线程池参数与拒绝策略数量两个独立要求，保留unmatched。"),
        **gids("dee8c70df3c5", [10, 11], "i1/q7合并动态代理方式与当前Agent框架使用方式，通用机制和项目选择为独立要求。"),
        **gids("dee8c70df3c5", [12, 13], "i1/q8合并GC算法与具体垃圾收集器两个独立要求，保留unmatched。"),
        **gids("dee8c70df3c5", [17, 18], "i1/q12合并是否支持多轮对话与Memory管理两个独立要求，保留unmatched。"),
        **gids("dee8c70df3c5", [20, 21], "i1/q14合并IoC启动流程与Bean生命周期两个独立要求，保留unmatched。"),
        **gids("dee8c70df3c5", [22, 23], "i1/q15合并AOP原理与项目Tool拦截关联两个独立要求，保留unmatched。"),
        **gids("dee8c70df3c5", [34, 35], "i1/q26合并Function Calling原理与Tool Use理解两个独立要求，保留unmatched。"),
        **gids("d3e713a6755b", [45, 46], "预测I2/Q30把8个与9个西瓜两道独立问题合并，两个场次及自我介绍已恢复，但此处仍不满足冻结原子粒度。"),
        **gids("1acb80963445", [5, 6], "I1/Q4合并输入不规范处理与混合检索权重配置两个独立要求，保留unmatched。"),
        **gids("1acb80963445", [7, 8], "I1/Q5合并向量检索准召保障与向量数据库差异两个独立要求，保留unmatched。"),
        **gids("1acb80963445", [12, 13], "I1/Q9合并AI未来方向与AGI认知两个独立要求，保留unmatched。"),
    },
}
EXTRA = {
    "quote-repair-v2": {"d3e713a6755b": {"bigo_1/q29"}},
    "ledger-repair-v2": {
        "dee8c70df3c5": {f"i1/q{i}" for i in [2, 3, 5, 7, 8, 12, 14, 15, 26]},
        "d3e713a6755b": {"I2/Q30"}, "6a559eecded4": set(), "1acb80963445": {"I1/Q4", "I1/Q5", "I1/Q9"},
    },
}
SOURCE_NOTES = {
    ("quote-repair-v2", "d3e713a6755b"): "两场身份和轮次正确；两处自我介绍漏抽，西瓜变体仍合并；图片2/3同一MySQL列表没有重复计数。未发现新造具体条件。",
    ("ledger-repair-v2", "dee8c70df3c5"): "正文和OCR确为同一面试，已去重复且覆盖其任务内容；9组独立要求仍合并。只输出1条明确追问关系，gold的4条关系中仍缺3条；不是完整语义恢复。",
    ("ledger-repair-v2", "d3e713a6755b"): "两场和各自自我介绍均保留，重复MySQL OCR列表未重抽；8个与9个西瓜仍合一。监控题承接消费故障，未额外虚构新监控组件要求。",
    ("ledger-repair-v2", "6a559eecded4"): "7道可恢复题干全部独立保留，线程数量、队列1000、SQL与ACM条件完整；泛化‘深挖简历项目’没有误抽。标题明示一面，但metadata.round为null。",
    ("ledger-repair-v2", "1acb80963445"): "原文13个原子任务以10条输出，3组独立要求仍合并；泛化‘深挖项目’已排除，未发现题干条件虚构。场次/公司/岗位正确。",
}


def review_group(name, manifest):
    directory = ROOT / name
    prediction_path = directory / "extraction.candidate.jsonl"
    require(prediction_path.is_file(), "complete targeted final required")
    require(digest(prediction_path.read_bytes()) == HASHES[name], "targeted prediction changed after source review")
    protocol, collection = read_json(directory / "extraction.protocol.json"), read_json(directory / "extraction.candidate.collection.json")
    require(collection["predictions_sha256"] == HASHES[name] and collection["protocol_sha256"] == digest(protocol), "collection binding mismatch")
    selected = protocol["definition"]["sample_ids"]
    require(protocol["dataset_sha256"] == GOLD_DIGEST and protocol["sample_count"] == len(selected), "predeclared subset changed")
    observations = read_jsonl(prediction_path)
    require(len(observations) == len(selected) and {r["id"] for r in observations} == set(selected), "must review only complete predeclared subset")
    require(all(not r["failed"] and r["snapshot"] == manifest["snapshot"] for r in observations), "targeted output/snapshot mismatch")
    gold_by_id = {g["id"]: g for g in manifest["extraction"]}
    output = ROOT / "targeted-repair-review" / name
    alignments, reviews = [], []
    for observed in observations:
        gold = gold_by_id[observed["id"]]
        short = observed["id"].removeprefix("extract-new-")
        source_path = inside(GOLD, gold["source_path"])
        require(digest(source_path.read_bytes()) == gold["source_hash"], "original source hash changed")
        text = decode_source(source_path.read_bytes())
        result = observed["result"]
        session_map = SESSIONS[name][short]
        require(set(session_map) == {s["id"] for s in gold["sessions"]} and len(set(session_map.values())) == len(session_map), "strict session map required")
        require(set(session_map.values()) == {s["id"] for s in result["sessions"]}, "unreviewed prediction session")
        by_pred = {q["id"]: q for q in result["questions"]}
        pairs, decisions = [], []
        for question in gold["questions"]:
            candidates = source_candidates(question, result, text)
            exact = [q for q in candidates if bare(question["raw_question"]) == bare(q["raw_question"])]
            if question["id"] in MISSING[name]:
                chosen, basis = None, MISSING[name][question["id"]]
            elif name == "ledger-repair-v2" and question["id"] in MANUAL:
                chosen, basis = MANUAL[question["id"]]
            else:
                require(len(exact) == 1, "unreviewed nonexact task: " + question["id"])
                chosen = exact[0]["id"]
                basis = "已对完整原文、当前normalized及场次复核：同一原子任务，原题对象、行为与条件保留；差异仅列表编号/空白/标点。"
            if chosen:
                require(chosen in {q["id"] for q in candidates}, "pair lacks source overlap")
                require(by_pred[chosen]["session_id"] == session_map[question["session_id"]], "cross-session match")
                pairs.append({"gold_id": question["id"], "prediction_id": chosen, "basis": basis})
            decisions.append({"gold_id": question["id"], "gold_session_id": question["session_id"],
                "gold_raw_question": question["raw_question"], "gold_source_spans": question["source_spans"],
                "corroborating_spans": question.get("corroborating_spans", []), "prediction_id": chosen, "basis": basis,
                "candidates": [{"id": q["id"], "session_id": q["session_id"], "raw": q["raw_question"],
                    "normalized": q["normalized_question"], "source_spans": q["source_spans"]} for q in candidates]})
        require(len({p["prediction_id"] for p in pairs}) == len(pairs), "one-to-one matching required")
        extras = set(by_pred) - {p["prediction_id"] for p in pairs}
        require(extras == EXTRA[name][short], "unreviewed extra prediction")
        alignment = {"id": gold["id"], "prediction_sha256": digest(result), "human_verified": False,
            "agent_verified": True, "review": stamp("Fresh targeted hash-bound source review; frozen gold, independent session mapping and atomic-task decisions. No historical alignment reused."),
            "pairs": pairs, "sessions": [{"gold_id": g, "prediction_id": p, "basis": "原文实际面试场次对应，轮次属性单独评分，不用属性缺失来放弃场次匹配。"} for g, p in session_map.items()]}
        alignments.append(alignment)
        reviews.append({"id": gold["id"], "original_path": gold["original_path"], "source_hash": gold["source_hash"],
            "source_file": str(source_path), "full_source_read": True, "source_hash_and_spans_valid": True,
            "gold_questions": len(gold["questions"]), "predicted_questions": len(result["questions"]),
            "gold_sessions": gold["sessions"], "predicted_sessions": result["sessions"],
            "decisions": decisions, "unmatched_predictions": [by_pred[p] for p in sorted(extras)],
            "basis": SOURCE_NOTES[(name, short)]})
    ordered_gold = [gold_by_id[row["id"]] for row in observations]
    metrics, per_sample = score_extraction(ordered_gold, observations, GOLD, {a["id"]: a for a in alignments}, review_policy="delegated_agent")
    write_jsonl(output / "alignments.jsonl", alignments)
    write_json(output / "source-review.json", {"predictions_sha256": HASHES[name], "reviewer_kind": "agent", "samples": reviews})
    write_jsonl(output / "per_sample.jsonl", per_sample)
    write_json(output / "metrics.json", metrics)
    return {"run": name, "scope": protocol["scope"], "predeclared_sample_ids": selected,
        "predictions": str(prediction_path), "predictions_sha256": HASHES[name],
        "protocol_sha256": collection["protocol_sha256"], "runtime_sha256": collection["runtime_sha256"],
        "collection": collection, "metrics": metrics, "review_path": str(output),
        "alignments_sha256": digest((output / "alignments.jsonl").read_bytes()),
        "source_review_sha256": digest((output / "source-review.json").read_bytes()),
        "samples": [{"id": r["id"], "gold_questions": r["gold_questions"], "predicted_questions": r["predicted_questions"],
            "strict_counts": next(p["scores"] for p in per_sample if p["id"] == r["id"]), "basis": r["basis"]} for r in reviews]}


def main():
    manifest = validate_gold(GOLD, "extraction", review_policy="delegated_agent")
    require(digest(manifest) == GOLD_DIGEST, "frozen v3 gold changed")
    arms = {name: review_group(name, manifest) for name in HASHES}
    old_path = ROOT / "quote-repair" / "extraction.candidate.jsonl"
    old = read_jsonl(old_path)
    receipt = {"created_at": datetime.now(timezone.utc).isoformat(), "reviewer_kind": "agent",
        "human_verified": False, "agent_verified": True, "source_evidence_contract": "raw_source_spans_v1",
        "dataset": str(GOLD), "dataset_sha256": GOLD_DIGEST, "gold_freeze_sha256": digest((GOLD / "freeze.json").read_bytes()),
        "script_sha256": digest(Path(__file__).read_bytes()),
        "scope": "Predeclared failed-source replay only; no stitching into the original 31-source experiment; not unseen generalization.",
        "prior_feedback_only_probe": {"predictions": str(old_path), "predictions_sha256": digest(old_path.read_bytes()),
            "samples": len(old), "failed": sum(bool(r["failed"]) for r in old), "model_calls": sum(len(r["model_calls"]) for r in old)},
        "runs": arms, "gold_unchanged": True, "original_full_arm_results_unchanged": True,
        "external_model_calls_by_reviewer": 0, "production_writes": 0,
        "limitations": ["Structural success is not complete atomic question, relationship, metadata or attribute recovery.",
            "These inputs were preselected after previous failure and are seen sources, with no independent control/unseen validation.",
            "bigo appears in both run scopes; do not aggregate as five independent unique sources.",
            "Counts use the unchanged strict one-to-one atomic grain; merged tasks remain FP+FN despite semantic mention."]}
    write_json(ROOT / "targeted-repair-source-review.json", receipt)
    pct = lambda x: f"{100*x:.2f}%" if x is not None else "N/A"
    lines = ["# 定向修复真实原文审核", "",
        "v2的1+4个预声明复跑记录均通过结构校验；来源去重后是4篇，bigo在两个实验中各出现一次。语义审核仍发现独立任务合并、自我介绍遗漏、追问关系遗漏和轮次字段缺失，不能称为题目全部恢复。", "",
        "先前仅汇总quote反馈的`quote-repair`仍是1篇/3calls失败；新增保留上一完整draft待修的`quote-repair-v2`是另一份协议和输出。本报告分别保存两者，不覆盖旧结果。", "",
        "## 预声明子集结果", "",
        "| 实验 | 结构成功 | 调用 / 预算扣计tokens | 严格TP / FP / FN | P / R / F1 |", "| --- | ---: | ---: | ---: | ---: |"]
    for name, run in arms.items():
        c, m = run["collection"], run["metrics"]
        lines.append(f"| {name} | {c['samples']-c['failed']} / {c['samples']} | {c['budget']['charged_calls']} / {c['budget']['charged_tokens']:,} | {m['tp']} / {m['fp']} / {m['fn']} | {pct(m['precision'])} / {pct(m['recall'])} / {pct(m['f1'])} |")
    lines += ["", "这些是预声明失败子集的诊断数值，未与旧31篇结果拼接；不同prompt、样本数和曾失败条件使两行不能当作随机A/B比较。原全量v5/v6 F1 76.17%/76.41%继续原样保留。", "",
        "## 每篇实际恢复与缺口", ""]
    for name, run in arms.items():
        for item in run["samples"]:
            counts = item["strict_counts"]
            lines += [f"### {name} / {item['id']}", "",
                f"冻结gold {item['gold_questions']}个原子任务，预测{item['predicted_questions']}条；严格TP{counts['tp']} / FP{counts['fp']} / FN{counts['fn']}。", "",
                item["basis"], ""]
    lines += ["## 证据与审核约束", "",
        "逐篇完整阅读不变原文，并独立核对当前输出的raw、normalized、场次和条件。仅忽略列表编号/空白的逐字候选仍经场次与语义复核；阿里自我介绍回答尾部、MCP缩写/全名、Max Value/最大值三项另记显式同义依据。OCR重复仅按预冻结的同一事件corroborating_spans使用，没有把它当另一场再计题。", "",
        "每个实验的`targeted-repair-review/<实验名>/alignments.jsonl`绑定本次完整result hash；`source-review.json`包含所有gold题的候选、span、独立决定和unmatched理由；`metrics.json`/`per_sample.jsonl`由项目`score_extraction`对该预声明子集计算，采用delegated_agent审核策略。没有复用旧published或旧A/B alignment。", "",
        "阿里内容虽已覆盖，但9组相邻独立任务合并，按严格粒度计9 FP和18 FN；不是18条内容均未提及。bigo两种西瓜都提到仍只有一个occurrence。京东同理，3个合并预测对应6个独立gold缺失。保持这一契约，避免把结构成功或语义提及当原子occurrence完整。", "",
        "CRM一面round=null、阿里岗位空格等元信息按冻结严格比较单独记录；完整条件/题数不等于属性准确率100%。所有实际引用span通过合法性评分；这也不证明抽取语义完整。Gold和历史预测均未修改，没有生产标签发布，审核者没有外部模型调用。", "",
        "目前证据支持v2反馈帮助这些历史失败来源返回可验证结构，尚不支持全量质量提升或未见来源泛化。后续若主张默认版本改善，应另做同源全集或独立新来源配对，不能以此挑选子集替代。", ""]
    (ROOT / "targeted-repair-source-review.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({k: {"samples": v["collection"]["samples"], "failed": v["collection"]["failed"],
        "tp": v["metrics"]["tp"], "fp": v["metrics"]["fp"], "fn": v["metrics"]["fn"], "f1": v["metrics"]["f1"]} for k, v in arms.items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
