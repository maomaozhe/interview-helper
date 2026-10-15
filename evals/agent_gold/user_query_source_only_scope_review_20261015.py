"""Append a source-only scope-strength audit without modifying frozen gold."""
from datetime import datetime, timezone
from pathlib import Path

from eval.common import digest, read_json, write_json
from evals.agent_gold.user_query_independent_design_review_20261015 import (
    DATASET, REPORT, audit_question)
from evals.agent_gold.user_query_source_20261015 import validate_dataset

ADDENDUM_ID = "SOURCE_ONLY_TITLE_EVIDENCE_CONTRACT"
DECISIONS = {
    "uq-source-only-ad-system": {
        "independent_status": "TITLE_ONLY_SCOPE_PLAUSIBLE_NOT_STRONG",
        "basis": "完整二面原文第7-11行有项目讲解、面试结果/时间回忆，第15-17行明确字节后端二面与50min；第19/25/27/32行将项目、手撕、场景题和消息队列八股分开。第29行广告推送系统设计因此是真实面试中的独立设计题目，且有具体业务系统对象，可解释弱ACCEPT。全文没有输入/输出职责、推送条件、规模数值、交付边界或进一步设计追问，不能补写用户画像、竞价、实时性等约束，也不足以称为完整工程场景。若无功能/约束是0319保留BOUNDARY的最低门槛，此题同样未满足；作者场景题分类支持任务意图，不能替代缺失要求。",
        "author_explicit_scenario_heading": True,
        "concrete_interview_event": True,
        "explicit_design_target": "广告推送系统",
        "recorded_behavior_or_constraints_beyond_title": [],
        "scope_strength": "TITLE_ONLY_BUSINESS_SYSTEM_DESIGN_INTENT",
    },
    "uq-source-only-distributed-flow": {
        "independent_status": "CONTRACT_CONFLICT_MINIMUM_EVIDENCE",
        "basis": "完整二面原文第27-30行明确场景题及大流量分布式架构设计，与项目/手撕/消息队列八股分节，足以证明作者记载了一道设计场景题。大流量是宽负载目标，分布式是架构形式；全文未写要构造何种业务系统或闭合基础设施机制，也没有行为、状态、接口、节点/流量数值或后续条件。不能把后面消息队列八股自动接成此题需求。相较0319的分布式ID设计，它有作者场景题分类，却没有更完整的新交付对象/职责证据；按0319的无功能/约束门槛，强ACCEPT缺乏一致支持。记录合同冲突，不断言题目不存在，也不把它并入21入库正例分母。",
        "author_explicit_scenario_heading": True,
        "concrete_interview_event": True,
        "explicit_design_target": "未命名业务对象的大流量分布式架构",
        "recorded_behavior_or_constraints_beyond_title": [],
        "scope_strength": "TITLE_ONLY_GENERIC_ARCHITECTURE_DESIGN_INTENT",
    },
    "uq-occ-0319": {
        "independent_status": "BOUNDARY_SUPPORTED_IF_THRESHOLD_IS_SHARED",
        "basis": "全文第1/11/13行有8.10字节后端一面/秋招一面，第15行实习开场、第21行LRU手撕和第23行面试官重视实际场景的回忆，因此具体面试事件成立，不能用事件真实性区别于二面两题。第16行分布式id的设计有基础设施设计对象，但全文未给唯一性、时序、吞吐、节点或故障条件；保留BOUNDARY可解释为严格最低要求。第23行笼统实际场景评价不能补充ID题约束；同理，二面场景题标题也不能补约束。若只凭新设计对象/作者分类接纳标题级题，本题为何仍BOUNDARY需要统一合同说明。",
        "author_explicit_scenario_heading": False,
        "author_generic_scenario_comment": True,
        "concrete_interview_event": True,
        "explicit_design_target": "分布式ID",
        "recorded_behavior_or_constraints_beyond_title": [],
        "scope_strength": "TITLE_ONLY_INFRASTRUCTURE_DESIGN_INTENT",
    },
}


def main():
    frozen_before = digest(read_json(DATASET / "freeze.json")["files"])
    bundle = validate_dataset(DATASET)
    questions = {row["id"]: row for row in bundle["questions"]}
    scope = bundle["scopes"]["engineering_system_design"]
    assert len(scope["positive_gold_ids"]) == 21
    assert set(scope["source_only_positive_gold_ids"]) == set(DECISIONS) - {"uq-occ-0319"}
    rows = []
    for gid, decision in DECISIONS.items():
        row = audit_question(questions[gid], decision["basis"], "source_only_scope_addendum")
        row.update({k: v for k, v in decision.items() if k != "basis"})
        row["full_source_line_count"] = len(Path(row["source_file"]).read_text(encoding="utf-8").splitlines())
        rows.append(row)
    facts = read_json(DATASET / "facts.json")
    source_only_revision = questions["uq-source-only-ad-system"]["source_revision_id"]
    doc = next(d for d in facts["documents"] if d["revision_id"] == source_only_revision)
    interview_ids = {i["id"] for i in facts["interviews"] if i["build_id"] == doc["build_id"]}
    published = [q for q in facts["questions"] if q["interview_id"] in interview_ids]
    missing_witnesses = []
    for gid in scope["source_only_positive_gold_ids"]:
        q = questions[gid]
        span = q["source_spans"][0]
        overlaps = [p["id"] for p in published for s in p["source_spans"]
                    if max(span["start_char"], s["start_char"]) < min(span["end_char"], s["end_char"])]
        assert not overlaps and q["occurrence_id"] is None and q["canonical_question_id"] is None
        missing_witnesses.append({"gold_id": gid, "published_overlapping_occurrence_ids": overlaps,
                                  "unmapped": True, "source_span": span})
    assert len(published) == 4
    validate_dataset(DATASET)
    assert digest(read_json(DATASET / "freeze.json")["files"]) == frozen_before
    result = {"id": ADDENDUM_ID, "created_at": datetime.now(timezone.utc).isoformat(),
        "reviewer_kind": "agent", "human_verified": False, "agent_verified": True,
        "status": "REAL_MISSING_QUESTIONS_WITH_SCOPE_CONTRACT_CONFLICT",
        "full_sources_read": 2, "questions_reviewed": rows,
        "source_only_extraction_witness": {"source_revision_id": source_only_revision,
            "published_occurrence_count": 4,
            "published_occurrences": [{"id": p["id"], "raw_question": p["raw_question"],
                                       "source_spans": p["source_spans"]} for p in published],
            "missing_witnesses": missing_witnesses},
        "contract_conclusion": "具体面试事件三题均成立；二面作者场景题分组有助于判断设计意图，广告推送有具体业务对象，但两标题都未记录功能/约束，泛大流量架构也缺闭合交付对象。不能据场景题三个字将二者当与21入库已审核题同强度的工程范围证据；按0319原依据的最低证据门槛，存在合同不一致，尤其distributed-flow不足强scope支持。",
        "recommended_display": "21条入库已审设计题；另2条原文明确的设计题标题，未入库，完整系统范围待统一裁定。",
        "main_positive_denominator": 21, "main_denominator_changed": False,
        "source_only_count": 2, "browser_21_plus_2_same_strength_claim_supported": False,
        "frozen_labels_changed": False, "historical_scores_changed": False,
        "recommendation": "当前冻结标签/评分保留；未来新版本单独定义标题级证据最低要求，并对广告推送、大流量架构、分布式ID共同应用。现阶段保留两条真实漏抽事实及无映射，不把scope争议改写成原题不存在。",
        "frozen_gold_changed": False, "external_model_calls": 0, "production_writes": 0,
        "predictions_read_for_this_audit": False, "script_sha256": digest(Path(__file__).read_bytes())}
    write_json(REPORT / "source-only-scope-review.json", result)
    report_path = REPORT / "independent-design-review.json"
    report = read_json(report_path)
    report["source_only_scope_addendum"] = result
    report["status"] = "SUPPORTED_WITH_SCOPE_CONTRACT_CONFLICT"
    report["scope_notes"] = [r for r in report["scope_notes"] if r["id"] != ADDENDUM_ID] + [{
        "id": ADDENDUM_ID, "affected_ids": list(DECISIONS), "basis": result["contract_conclusion"]}]
    write_json(report_path, report)
    md_path = REPORT / "independent-design-review.md"
    md = md_path.read_text(encoding="utf-8")
    marker = "## 补充：两条 source-only 短标题与分布式 ID 的证据门槛"
    md = md.split(marker)[0].rstrip() + "\n\n" + marker + "\n\n"
    md += "全文重读两份归档：字节二面43行、字节一面23行，hash及全部引用跨度/行号再次校验；两份均与r293原始归档逐字节一致。\n\n"
    md += "**两条漏抽事实成立，强scope支持不等价。** 二面当前仅入库4题，广告推送和大流量架构的原文跨度均没有对应occurrence/canonical。21条入库主正例分母保持不变。\n\n"
    for row in rows:
        line = row["source_spans"][0]["start_line"]
        path = Path(row["source_file"]).resolve().as_posix()
        md += f"- `{row['id']}` / 冻结 {row['frozen_judgment']['grade']} / 独立 `{row['independent_status']}`：[原文第{line}行](<{path}:{line}>)。{row['independent_basis']}\n"
    md += "\n" + result["contract_conclusion"] + "\n\n"
    md += "建议浏览器明确显示：**" + result["recommended_display"] + "** 不能把21+2当作23条同强度的完整工程设计gold。\n\n"
    md += "全部冻结标签、gold文件及历史评分保持；没有根据新预测改标，也未抹去两条真实漏抽。详细来源、逐题依据和4条已有题/零重叠证明见 `source-only-scope-review.json`，同时附入本报告JSON。\n"
    md_path.write_text(md, encoding="utf-8")
    print({"status": result["status"], "full_sources_read": 2, "questions_reviewed": 3,
           "main_positive_denominator": 21, "frozen_gold_changed": False})


if __name__ == "__main__":
    main()
