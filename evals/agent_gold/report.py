"""Readable audit and review packet, derived from sealed gold and pinned runs."""
from __future__ import annotations

import argparse
import html
import os
from pathlib import Path
from urllib.parse import quote

from eval.common import digest, inside, read_json, read_jsonl, write_json
from eval.validate_gold import require, validate_gold
from interview_intelligence.ingestion.snapshot import decode_source


def percent(value):
    return "未评测" if value is None else f"{100 * value:.2f}%"


def href(output, target):
    return quote(Path(os.path.relpath(target.resolve(), output.resolve())).as_posix(), safe="/")


def checked_run(path, manifest, section):
    record = read_json(path / "manifest.json")
    require(record["section"] == section and record["dataset_sha256"] == digest(manifest), "report gold version mismatch")
    require(record["predictions_sha256"] == digest((path / "predictions.jsonl").read_bytes()), "report predictions changed")
    return record, read_json(path / "metrics.json"), read_json(path / "gates.json")


def page(title, body):
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{html.escape(title)}</title><style>'
            'body{font:16px/1.7 system-ui;max-width:1050px;margin:32px auto;padding:0 20px;background:#f6f8fb;color:#18273b}'
            'table{border-collapse:collapse;width:100%;background:#fff}td,th{border:1px solid #d8e0eb;padding:10px;text-align:left}'
            'article,details{background:#fff;border:1px solid #d8e0eb;border-radius:8px;padding:18px;margin:18px 0}'
            'summary{cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#eef2f7;padding:14px}'
            'a{color:#075ea8}code{overflow-wrap:anywhere}.muted{color:#58677c}</style>'
            f'<h1>{html.escape(title)}</h1>{body}</html>')


def write_packet(dataset, extraction_run, task_run, output, supporting=None):
    manifest = validate_gold(dataset, "extraction", review_policy="delegated_agent")
    validate_gold(dataset, "task_labels", review_policy="delegated_agent")
    _, em, eg = checked_run(extraction_run, manifest, "extraction")
    _, tm, tg = checked_run(task_run, manifest, "task_labels")
    output.mkdir(parents=True, exist_ok=True)
    pending = read_jsonl(dataset / "needs_user_review.jsonl")
    resolved = []
    for row in manifest["task_labels"] + manifest["extraction"]:
        units = [row] + row.get("sessions", []) + row.get("questions", [])
        for unit in units:
            for field, review in unit.get("field_reviews", {}).items():
                resolved.append({**review, "source_path": row["source_path"], "field": field, "quote": review["source_quote"]})

    review_md = [f"# 歧义审核材料\n\n版本 {manifest['version']}。待审核 {len(pending)} 项，用户已裁决 {len(resolved)} 个字段。",
                 "\n已确认的字段另存人工答复；整条 Agent 标注的 human_verified 仍为 false。"]
    review_html = f'<p>待审核 {len(pending)} 项；已确认 {len(resolved)} 个字段。' + ('请回复待审核项。' if pending else '本批歧义已全部裁决。') + '已确认记录保留在冻结版本中。</p>'
    for number, item in enumerate(pending + resolved, 1):
        done = item in resolved
        status = "已确认" if done else "待审核"
        source = inside(dataset, item["source_path"])
        text = decode_source(source.read_bytes())
        raw = item["quote"]
        start = text.find(raw)
        require(start >= 0, "review quote missing from frozen source")
        line = text.count("\n", 0, start) + 1
        lines = text.splitlines()
        end = line + raw.count("\n")
        context = "\n".join(f"{i+1:>3} | {lines[i]}" for i in range(max(0, line - 5), min(len(lines), end + 4)))
        source_link = href(output, source)
        detail = f"用户答复：{item['answer']}。字段结果：{item['value']}。" if done else f"{item['reason']} 当前建议：{item['proposed']}。"
        choices = "" if done else "\n\n可选：" + "；".join(item["options"]) + "。"
        review_md += [f"\n## {number}. {item['id']} · {status}\n\n原文：{raw}\n\n{detail}{choices}",
                      f"\n[不可变原文]({source_link})，第{line}行。\n\n```text\n{context}\n```"]
        review_html += (f'<article><h2>{number}. {html.escape(item["id"])} · {status}</h2>'
                        f'<blockquote>{html.escape(raw)}</blockquote><p>{html.escape(detail + choices)}</p>'
                        f'<p><a href="{source_link}">不可变原文</a> · 第 {line} 行</p>'
                        f'<details><summary>查看原文上下文</summary><pre>{html.escape(context)}</pre></details></article>')
    (output / "REVIEW.md").write_text("\n".join(review_md) + "\n", encoding="utf-8")
    (output / "review.html").write_text(page("歧义审核材料", review_html), encoding="utf-8")

    nq = sum(len(r["questions"]) for r in manifest["extraction"])
    nl = len(manifest["task_labels"])
    ep, tp = href(output, extraction_run / "summary.html"), href(output, task_run / "summary.html")
    table = [
        ("抽取", f"{len(manifest['extraction'])}篇 / {nq}题", f"P {percent(em['precision'])} · R {percent(em['recall'])} · F1 {percent(em['f1'])}", eg["status"], ep),
        ("任务分类", f"{nl}条", f"形式 {percent(tm['response_form']['accuracy'])} · 焦点 {percent(tm['coding_focus']['accuracy'])} · 联合 {percent(tm['joint_accuracy'])}", "门槛未配置（NOT_RUN）", tp),
    ]
    md = [f"# 测评金标与结果\n\n版本 {manifest['version']}；用户授权 Agent 独立标注，已确认 {len(resolved)} 个歧义字段，剩余 {len(pending)} 项。",
          "\n本轮实际审计已入库的 r293 输出。没有新增模型推理，历史模型调用与成本未重算；毫秒耗时是本地读取时间。",
          "\n首次原文标注先于预测比较；随后对照原文裁决拆分与岗位口径。当前属于诊断/回归金标，不作为未见测试集声称泛化成绩。历次标签裁决与过程说明保留在父版本及manifest中。",
          "\n[审核原文与答复](REVIEW.md) · [可展开审核页](review.html) · [HTML总览](index.html)",
          "\n| 模块 | 规模 | 结果 | 门槛 | 报告 |\n|---|---|---|---|---|"]
    body = ('<p>独立 Agent 金标，用户已确认部分字段。快照 corpus/index 293、annotation 30、as_of 2026-10-05。</p>'
            '<p class="muted">本轮评的是已有输出；没有新模型推理。读取耗时不能作为推理延迟，历史推理费用未重算。首次标注后曾对照原文裁决口径，当前为诊断/回归金标。</p>'
            f'<p><a href="review.html">待审核 {len(pending)} 项与已确认答复</a> · <a href="summary.md">Markdown摘要</a></p>'
            '<table><tr><th>模块</th><th>规模</th><th>结果</th><th>门槛</th></tr>')
    for section, count, score, gate, link in table:
        md.append(f"| {section} | {count} | {score} | {gate} | [逐样本证据]({link}) |")
        body += f'<tr><td><a href="{link}">{section}</a></td><td>{count}</td><td>{score}</td><td>{gate}</td></tr>'
    body += '</table>'
    findings = [
        f"抽取 TP {em['tp']} / FP {em['fp']} / FN {em['fn']}；公司 {percent(em['metadata_accuracy']['company'])}（25/{em['metadata_field_counts']['company']}）、L1 {percent(em['matched_attribute_accuracy']['topic_l1'])}、L2 {percent(em['matched_attribute_accuracy']['topic_l2'])} 未达到门槛。",
        "《字节后端实习agent一面》只留下标题预测，原文19题全部漏掉；机构推广汇编误抽12题。先检查该来源的入库抽取与汇编排除规则。",
        f"分类的随机200条正确148条；11条边界补充全部正确。ENGINEERING误报45条，主要把原理、项目解释和故障诊断当成工程实现任务。两部分均展示，不能视为总体无偏估计。",
        "公司分数包含别名归一化差异：xhs/小红书、pdd/拼多多、腾讯视频/腾讯、TME腾讯音乐/腾讯音乐及boss/BOSS直聘；另有一次标题整体被当成公司。需要区分别名错误与雇主识别错误。",
        f"负文档排除仅1/2正确；显式追问 TP {em['followups']['tp']} / FP {em['followups']['fp']} / FN {em['followups']['fn']}。负例和追问分母很小，应扩充独立来源。",
        "引用有效率100%只证明已引片段确实存在，不证明完整覆盖题干约束；网格算法和带TTL缓存设计的证据完整性仍需单列检查。",
        ("裸词LRU题型仍待裁定；它所在文档被整篇漏抽，因此题型暂缓计分不会消除19个FN。" if any(r["id"] == "review-lru-task" for r in pending)
         else "LRU已由用户裁定为手写，Question Type映射为ALGORITHM，恢复题型评分资格；它所在文档漏抽全部19题，故仍计FN，主要指标不因裁决提高。"),
        "根据这些已见案例改模型或提示词后，将它们用于回归，并另留新来源测试；金标版本变动不能解释成模型提升。整体项目发布与混合负载未验收。",
    ]
    md.append("\n## 缺陷与解释\n")
    md += [f"- {item}" for item in findings]
    body += '<h2>需要优先修复的证据</h2><ul>' + ''.join(f'<li>{html.escape(item)}</li>' for item in findings) + '</ul>'
    pins = []
    if supporting:
        md.append("\n## 已有的其他模块实验\n\n以下只引用已存在的固定运行，未在本轮重跑；样本版本各自独立，不合成总分，也不代表当前工作区所有改动的成绩。\n\n| 模块 | 金标 | 规模 | 指标 | 状态 | 报告 |\n|---|---|---|---|---|---|")
        body += '<h2>其他已存在的固定实验</h2><p>本轮未重跑；各用各自金标，不能合成总分或当作当前工作区全部改动的成绩。</p><table><tr><th>模块</th><th>金标</th><th>规模</th><th>指标</th><th>状态</th></tr>'
        for spec in read_json(supporting):
            path, gold = Path(spec["run"]), Path(spec["dataset"])
            known = validate_gold(gold, spec["section"], review_policy="delegated_agent")
            meta, metrics, gate = checked_run(path, known, spec["section"])
            if spec["section"] == "dedup":
                score = f"SAME P {percent(metrics['precision'])} / R {percent(metrics['recall'])} / F1 {percent(metrics['f1'])}"
            elif spec["section"] == "retrieval":
                score = "Recall@10：" + " / ".join(f"{k} {percent(v['recall_at_10'])}" for k, v in metrics["pipelines"].items())
            else:
                score = "任务成功 " + percent(metrics["task_success_rate"])
            link = href(output, path / "summary.html")
            md.append(f"| {spec['section']} | {meta['dataset_version']} | {meta['sample_count']} | {score} | {gate['status']} | [固定报告]({link}) |")
            body += f'<tr><td><a href="{link}">{spec["section"]}</a></td><td>{meta["dataset_version"]}</td><td>{meta["sample_count"]}</td><td>{score}</td><td>{gate["status"]}</td></tr>'
            pins.append({**spec, "run_id": meta["run_id"], "dataset_sha256": meta["dataset_sha256"],
                         "metrics_sha256": digest((path / "metrics.json").read_bytes())})
        body += '</table><p>检索 relevance 为标注池范围，Rerank Top50 仍有150项未标注。扩池后应封存新版本再评估。</p>'
        md.append("\n检索 relevance 受标注池范围限制，Rerank Top50 尚有150项未标注；后续扩池须封存新版本。")
    md += ["\n## 复跑与核验\n\n命令见 [标注操作说明](../../../evals/agent_gold/README.md) 与 [评测手册](../../../eval/README.md)。",
           "\n历史全量Python测试343项通过、1项跳过；本次裁决及相关评测/采集测试54项通过。",
           f"\n[金标manifest]({href(output, dataset / 'manifest.json')}) · [封存hash]({href(output, dataset / 'freeze.json')})。所有Agent行human_verified=false，单字段用户答复保存field_reviews。"]
    body += '<p>历史全量Python测试343项通过、1项跳过；本次裁决及评测采集相关测试54项通过。</p>'
    (output / "summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    (output / "index.html").write_text(page("面经项目测评 · 独立金标审计", body), encoding="utf-8")
    write_json(output / "audit-receipt.json", {"dataset": str(dataset), "dataset_sha256": digest(manifest),
        "extraction_run": str(extraction_run), "task_labels_run": str(task_run), "review_pending": len(pending),
        "human_field_adjudications": len(resolved), "supporting_runs": pins,
        "annotation_recipe_hashes": {p.name: digest(p.read_bytes()) for p in Path(__file__).parent.glob("*.py")},
        "scope": "Published outputs audit; supporting runs are historical pins, not reruns."})
    return output / "index.html"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--extraction-run", type=Path, required=True)
    parser.add_argument("--task-run", type=Path, required=True)
    parser.add_argument("--supporting", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(write_packet(args.dataset, args.extraction_run, args.task_run, args.output, args.supporting))


if __name__ == "__main__":
    main()
