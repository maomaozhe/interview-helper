"""Readable, escaped reports and explicit release gates."""
from __future__ import annotations

import csv
import html
import json
from pathlib import Path

from eval.common import PIPELINES, lookup, MISSING, write_json, write_jsonl


def gates(section, metrics, *, eligible, default_pipeline="HYBRID", threshold_version="quality_gate_v2"):
    if threshold_version not in {"quality_gate_v2", "quality_gate_v3"}:
        raise ValueError("UNKNOWN_THRESHOLD_VERSION")
    thresholds = {
        "extraction": [("precision", .92, "min"), ("recall", .88, "min"), ("f1", .90, "min"),
                       ("metadata_accuracy.company", .95, "min"), ("metadata_accuracy.round", .90, "min"),
                       ("matched_attribute_accuracy.topic_l1", .90, "min"), ("matched_attribute_accuracy.topic_l2", .85, "min"),
                       ("null_field_hallucination_count", 0, "max"), ("invalid_citation_count", 0, "max")],
        "dedup": [("precision", .95, "min"), ("recall", .85, "min")],
        "retrieval": [(f"pipelines.{default_pipeline}.recall_at_10", .85, "min")],
        "routing": [("tool_selection_accuracy", .90, "min"), ("argument_accuracy", .95, "min"),
                    ("task_success_rate", .90, "min"), ("unknown_assertions", 0, "max"),
                    ("unauthorized_write_count", 0, "max")],
        "sql": [("task_success_rate", 1.0, "min"), ("unknown_assertions", 0, "max")],
        "task_labels": [],
    }
    if threshold_version == "quality_gate_v3":
        thresholds["task_labels"] = [("joint_accuracy", .90, "min"), ("predicted_coverage", .95, "min"),
            ("coding_focus.per_class.ENGINEERING.precision", .90, "min"),
            ("coding_focus.per_class.ENGINEERING.recall", .85, "min")]
    checks = []
    for key, threshold, direction in thresholds[section]:
        value = lookup(metrics, key)
        valid = value is not MISSING and value is not None
        passed = valid and (value >= threshold if direction == "min" else value <= threshold)
        checks.append({"metric": key, "value": value if valid else None, "threshold": threshold,
                       "direction": direction, "status": "NOT_RUN" if not valid else "PASSED" if passed else "BELOW_GATE"})
    if not eligible:
        status = "NOT_ELIGIBLE"
    elif not checks or any(c["status"] == "NOT_RUN" for c in checks):
        status = "NOT_RUN"
    else:
        status = "PASSED" if all(c["status"] == "PASSED" for c in checks) else "BELOW_GATE"
    return {"status": status, "scope": "module thresholds only; not overall V1 release", "checks": checks,
            "threshold_version": threshold_version,
            "unconfigured_thresholds": ["task_labels accuracy"] if threshold_version == "quality_gate_v2" else []}


def _flat(value, prefix=""):
    result = {}
    for key, item in value.items():
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(item, dict):
            result.update(_flat(item, name))
        elif not isinstance(item, list):
            result[name] = item
    return result


def write_report(output: Path, manifest, config, metrics, gate, rows):
    write_json(output / "manifest.json", manifest)
    write_json(output / "config.json", config)
    write_json(output / "metrics.json", metrics)
    write_json(output / "gates.json", gate)
    write_jsonl(output / "per_sample.jsonl", rows)
    with (output / "per_sample.csv").open("w", encoding="utf-8-sig", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=["id", "pipeline", "status", "failed", "error_code", "checks", "scores"])
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(row[key], ensure_ascii=False) if key in {"checks", "scores"} else row.get(key)
                             for key in writer.fieldnames})
    label = "合成框架验证" if manifest.get("kind") == "fixture" else "开发评测 未用于发布" if manifest["split"] == "dev" else "正式模块评测"
    if manifest.get("review_policy") == "delegated_agent":
        label = "用户授权的 Agent 审核评测"
    summary = [f"# 面经项目评测报告\n\n{label}。模块 {manifest['section']}，门槛状态 {gate['status']}。",
               f"\n数据版本 {manifest['dataset_version']}，样本 {manifest['sample_count']}；人工确认 {manifest['human_verified_count']}。",
               f"\n审核策略 {manifest.get('review_policy', 'human')}；Agent 审核 {manifest.get('agent_verified_count', 0)}。",
               "\n[逐样本](per_sample.csv) · [预测](predictions.jsonl) · [指标](metrics.json) · [门槛](gates.json) · [配置](config.json)",
               "\n| 指标 | 值 |\n|---|---|"]
    summary.extend(f"| {key} | {value if value is not None else '未评测'} |" for key, value in _flat(metrics).items())
    bad = [r for r in rows if r["status"] != "PASS"]
    bad_text = ["# 需复核的评测案例\n"]
    for row in bad:
        bad_text.append(f"\n## {row['id']} {row.get('pipeline', '')}\n\n状态 {row['status']}；错误 {row.get('error_code') or '无'}。\n")
        bad_text.extend(f"- {c['id']}：{c['status']}；期望 {json.dumps(c.get('expected'), ensure_ascii=False)}；实际 {json.dumps(c.get('actual'), ensure_ascii=False)}；{c.get('reason') or ''}"
                        for c in row["checks"] if c["status"] != "PASS")
    if not bad:
        bad_text.append("\n本次已执行断言未发现失败。正式业务质量仍以数据集资格和模块门槛为准。")
    (output / "summary.md").write_text("\n".join(summary) + "\n", encoding="utf-8")
    (output / "bad_cases.md").write_text("\n".join(bad_text) + "\n", encoding="utf-8")
    escape = html.escape
    cards = []
    for row in rows:
        content = escape(json.dumps({"checks": row["checks"], "scores": row["scores"], "observation": row["observation"]}, ensure_ascii=False, indent=2))
        cards.append(f'<details data-status="{escape(row["status"])}"><summary>{escape(row["id"])} {escape(row.get("pipeline", ""))} — {escape(row["status"])}</summary><pre>{content}</pre></details>')
    metrics_html = "".join(f"<tr><td>{escape(key)}</td><td>{escape(str(value))}</td></tr>" for key, value in _flat(metrics).items())
    document = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>面经项目评测报告</title><style>body{font:16px system-ui;max-width:1150px;margin:32px auto;padding:0 20px;background:#f7f9fc;color:#16243b}table{border-collapse:collapse;width:100%;background:white}td{border:1px solid #ddd;padding:8px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#eef2f8;padding:16px}details{margin:12px 0;border:1px solid #ccd6e5;padding:12px;background:white}summary{cursor:pointer}select{padding:8px}</style>'
    document += f'<h1>面经项目评测报告</h1><p>{escape(label)} · {escape(manifest["section"])} · {escape(gate["status"])}</p><p>样本 {manifest["sample_count"]}，人工确认 {manifest["human_verified_count"]}，Agent 审核 {manifest.get("agent_verified_count",0)}。<a href="summary.md">摘要</a> · <a href="per_sample.csv">CSV</a> · <a href="bad_cases.md">坏案例</a></p><table>{metrics_html}</table><h2>逐样本证据</h2><select id="filter"><option value="ALL">全部</option><option>PASS</option><option>FAIL</option><option>UNKNOWN</option></select>'
    document += "".join(cards) + '<script>document.getElementById("filter").addEventListener("change",e=>document.querySelectorAll("details").forEach(d=>d.hidden=e.target.value!=="ALL"&&d.dataset.status!==e.target.value));</script></html>'
    (output / "summary.html").write_text(document, encoding="utf-8")
    if "pipelines" in metrics:
        fields = ["pipeline", "recall_at_5", "recall_at_10", "mrr", "ndcg_at_10", "failed_queries"]
        with (output / "ablation.csv").open("w", encoding="utf-8-sig", newline="") as target:
            writer = csv.DictWriter(target, fieldnames=fields)
            writer.writeheader()
            for pipeline in PIPELINES:
                values = metrics["pipelines"].get(pipeline)
                writer.writerow({"pipeline": pipeline, **{key: values.get(key) if values else "NOT_RUN" for key in fields[1:]}})
        table = ["# 检索消融\n", "| " + " | ".join(fields) + " |", "| " + " | ".join("---" for _ in fields) + " |"]
        for pipeline in PIPELINES:
            values = metrics["pipelines"].get(pipeline)
            table.append("| " + " | ".join([pipeline] + [str(values.get(k)) if values else "NOT_RUN" for k in fields[1:]]) + " |")
        (output / "ablation.md").write_text("\n".join(table) + "\n", encoding="utf-8")
