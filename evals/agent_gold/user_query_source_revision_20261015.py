"""Freeze a narrowly scoped v2 title-evidence correction; preserve the v1 run."""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import shutil

from eval.common import digest, read_json, write_json, write_jsonl
from evals.agent_gold.user_query_source_20261015 import validate_dataset

BASE = Path("data/gold/user-query-source-reviewed-20261015-v1")
OUTPUT = Path("data/gold/user-query-source-reviewed-20261015-v2")
REPORT = Path("data/reports/user-query-source-reviewed-20261015-v2")
PARENT_MANIFEST_SHA256 = "9ab5c9671da5d648f436e49c0fc59477f5156fef6a4aef1009c507cbb7426a09"
PARENT_FREEZE_SHA256 = "d5c0236e71d8fc06abe8d160af9ab9ec381b1dd3a6e93289ab843cb695fe7262"
SOURCE_ONLY_IDS = ("uq-source-only-ad-system", "uq-source-only-distributed-flow")
REASONS = {
    SOURCE_ONLY_IDS[0]: "全文明确字节后端二面、50min，并将广告推送系统设计列于场景题分组；原题真实存在且r293未抽取，保持无occurrence/canonical映射。仅有业务系统标题，未记录输入输出、职责、约束或进一步闭合行为；依照分布式ID标题题uq-occ-0319的相同证据门槛，工程系统设计范围保留BOUNDARY，不补写广告画像/竞价/实时性等要求。",
    SOURCE_ONLY_IDS[1]: "全文明确字节后端二面、50min，并将大流量分布式架构设计列于场景题分组；原题真实存在且r293未抽取，保持无occurrence/canonical映射。大流量与分布式是宽负载/架构标题，未记录具体交付系统、职责或闭合行为；依照分布式ID标题题uq-occ-0319的相同证据门槛保留BOUNDARY，不把相邻消息队列八股借作本题条件。",
}


def require(test, message):
    if not test:
        raise ValueError(message)


def file_hashes(root):
    return {p.relative_to(root).as_posix(): digest(p.read_bytes())
            for p in sorted(root.rglob("*")) if p.is_file()}


def verify_narrow_revision(before, after):
    """Reject any unrequested question/scope edits before freezing or scoring."""
    before_questions = {q["id"]: q for q in before["questions"]}
    after_questions = {q["id"]: q for q in after["questions"]}
    require(before_questions.keys() == after_questions.keys(), "revision changed question identities")
    for gid, old in before_questions.items():
        new = deepcopy(after_questions[gid])
        if gid in SOURCE_ONLY_IDS:
            require(old["scope_judgments"]["engineering_system_design"]["grade"] == "ACCEPT",
                    "parent source-only grade is not ACCEPT")
            require(new["scope_judgments"]["engineering_system_design"] == {
                "grade": "BOUNDARY", "basis": REASONS[gid]}, "revision source-only decision differs")
            new["scope_judgments"]["engineering_system_design"] = deepcopy(
                old["scope_judgments"]["engineering_system_design"])
        require(new == old, "revision changed unrelated question fields: " + gid)
    old_scopes, new_scopes = before["scopes"], deepcopy(after["scopes"])
    primary = new_scopes["engineering_system_design"]
    require(primary["source-only_positive_gold_ids"] == primary["source_only_positive_gold_ids"] == [],
            "v2 source-only positives not empty")
    require(primary["source_only_boundary_gold_ids"] == list(SOURCE_ONLY_IDS), "v2 source-only boundaries differ")
    primary.pop("source_only_boundary_gold_ids")
    for field in ("source-only_positive_gold_ids", "source_only_positive_gold_ids"):
        primary[field] = deepcopy(old_scopes["engineering_system_design"][field])
    require(new_scopes == old_scopes, "revision changed unrelated scope fields")
    require(after["cases"] == before["cases"], "revision changed query cases")
    counts = Counter(q["scope_judgments"]["engineering_system_design"]["grade"]
                     for q in after["questions"] if not q.get("source_only"))
    require(counts == {"ACCEPT": 21, "REJECT": 2728, "BOUNDARY": 22}, "revision changed occurrence denominator")
    return dict(counts)


def revise(base=BASE, output=OUTPUT, report=REPORT):
    base, output, report = Path(base), Path(output), Path(report)
    require(not output.exists(), "v2 output is immutable and must not exist")
    require(not report.exists(), "revision audit output must not exist")
    before = validate_dataset(base)
    parent_hashes = file_hashes(base)
    require(parent_hashes["manifest.json"] == PARENT_MANIFEST_SHA256, "unexpected parent manifest")
    require(parent_hashes["freeze.json"] == PARENT_FREEZE_SHA256, "unexpected parent freeze")
    require(before["manifest"]["dataset_id"] == BASE.name, "unexpected parent dataset")
    require(before["scopes"]["engineering_system_design"]["source_only_positive_gold_ids"] == list(SOURCE_ONLY_IDS),
            "unexpected parent source-only positives")
    now = datetime.now(timezone.utc).isoformat()
    shutil.copytree(base, output)
    questions = deepcopy(before["questions"])
    for q in questions:
        if q["id"] in SOURCE_ONLY_IDS:
            q["scope_judgments"]["engineering_system_design"] = {"grade": "BOUNDARY", "basis": REASONS[q["id"]]}
    write_jsonl(output / "questions.jsonl", questions)
    scopes = deepcopy(before["scopes"])
    scopes["engineering_system_design"].update({"source-only_positive_gold_ids": [],
        "source_only_positive_gold_ids": [], "source_only_boundary_gold_ids": list(SOURCE_ONLY_IDS)})
    write_json(output / "scopes.json", scopes)
    parent = {"dataset_id": before["manifest"]["dataset_id"], "manifest_file_sha256": parent_hashes["manifest.json"],
              "freeze_file_sha256": parent_hashes["freeze.json"], "facts_file_sha256": parent_hashes["facts.json"],
              "frozen_files_sha256": digest(read_json(base / "freeze.json")["files"])}
    manifest = deepcopy(before["manifest"])
    manifest.update({"dataset_id": output.name, "created_at": now, "parent": parent,
        "revision": {"kind": "source_only_title_scope_contract_correction", "reviewer_kind": "agent",
            "human_verified": False, "agent_verified": True, "changed_gold_ids": list(SOURCE_ONLY_IDS),
            "from_grade": "ACCEPT", "to_grade": "BOUNDARY", "reasons": REASONS,
            "policy_anchor_gold_id": "uq-occ-0319", "occurrence_denominator_changed": False,
            "source_only_extraction_defects_preserved": True,
            "authorization_basis": "用户已授权Agent自行审核并完善真实面经gold；依据完整原文统一最低证据门槛。"}})
    manifest["exposure"].update({"predictions_seen_before_freeze": True,
        "revision_context": "v1模型首测已开始/进行中后才冻结v2；v1与当前sealed run继续保留，不冒充最初blind版本。",
        "source_only_scope_reviewer_predictions_read": False,
        "revision_decided_from_predictions": False,
        "revision_evidence": "全文重读两份r293归档，并比较三个标题题最低要求；不是依据预测或分数调标。"})
    write_json(output / "manifest.json", manifest)
    readme = (output / "README.md").read_text(encoding="utf-8")
    readme = readme.replace("21 个确定主设计、22 个边界、2 个原文存在但漏抽。",
        "21 个已入库强主正例、2728 个已入库排除项、22 个已入库边界；另有2个标题级原文漏抽，范围待裁。")
    readme = readme.replace("## 原文漏抽", "## 标题级原文漏抽：工程范围待裁（BOUNDARY）")
    note = ("\n## v2 最低证据门槛修订\n\n"
        "仅把广告推送系统设计与大流量分布式架构设计的source-only工程范围从ACCEPT改为BOUNDARY。作者确在真实字节二面中将它们列作场景题，原题与漏抽事实保留；全文未记录详细闭合行为或约束，统一采用与分布式ID标题题uq-occ-0319相同的最低证据门槛。两题保持无occurrence/canonical映射，不归入21条入库正例，不再作为23条同强度正例显示。22个入库边界之外另列这2个标题级source-only边界。\n\n"
        "v1首测已在进行中后才冻结本版本：predictions_seen_before_freeze=true。source-only审核者未读预测，修订不由预测/分数决定；v2仍是已暴露r293来源的诊断gold，非初始blind或新来源泛化集。v1、当前sealed run和其评分均保留。cases/history/facts/184份源文件及2771条已入库题完全不变，Recall/COUNT仍NOT_CERTIFIED、release_eligible=false。父版本摘要及变更理由见manifest。\n")
    readme += note
    (output / "README.md").write_text(readme, encoding="utf-8")
    write_json(output / "freeze.json", {"schema": "user_query_gold_freeze_v1", "frozen_at": now,
        "files": {name: sha for name, sha in file_hashes(output).items() if name != "freeze.json"}})
    after = validate_dataset(output)
    counts = verify_narrow_revision(before, after)
    after_hashes = file_hashes(output)
    changed = [name for name in sorted(parent_hashes) if parent_hashes[name] != after_hashes.get(name)]
    require(set(changed) == {"questions.jsonl", "scopes.json", "manifest.json", "README.md", "freeze.json"},
            "revision changed unrelated files")
    require(file_hashes(base) == parent_hashes, "parent v1 changed during revision")
    diff = {"question_changes": [{"gold_id": gid,
        "before": next(q for q in before["questions"] if q["id"] == gid)["scope_judgments"]["engineering_system_design"],
        "after": next(q for q in after["questions"] if q["id"] == gid)["scope_judgments"]["engineering_system_design"]}
        for gid in SOURCE_ONLY_IDS], "source_only_positive_before": list(SOURCE_ONLY_IDS),
        "source_only_positive_after": [], "source_only_boundary_after": list(SOURCE_ONLY_IDS),
        "main_occurrence_grades_unchanged": counts,
        "files": {name: {"before_sha256": parent_hashes[name], "after_sha256": after_hashes[name]} for name in changed}}
    receipt = {"schema": "user_query_source_gold_revision_audit_v1", "created_at": now,
        "parent": parent, "output_dataset_id": output.name, "v2_manifest_sha256": after_hashes["manifest.json"],
        "v2_freeze_sha256": after_hashes["freeze.json"], "changed_files": changed,
        "unchanged_file_count": len(parent_hashes) - len(changed), "v1_unchanged": True,
        "same_2771_occurrence_question_records": True, "same_184_source_files": True,
        "same_facts_history_cases_census": True, "main_occurrence_counts": counts,
        "source_only_boundary_count": 2, "source_only_positive_count": 0,
        "formal_validator": "v1 and v2 validate_dataset passed",
        "predictions_seen_before_freeze": True, "source_only_scope_reviewer_predictions_read": False,
        "revision_decided_from_predictions": False, "current_sealed_run_modified": False,
        "model_calls": 0, "production_writes": 0, "script_sha256": digest(Path(__file__).read_bytes())}
    write_json(report / "revision-diff.json", diff)
    write_json(report / "revision-audit.json", receipt)
    (report / "revision-audit.md").write_text("# v2 标题级source-only范围修订\n\n"
        "仅两条真实漏抽原题的工程scope从ACCEPT改为BOUNDARY；保持原文、span、无映射及漏抽事实。作者场景题分类支持设计意图，但不补出闭合行为或约束，最低证据门槛与分布式ID标题0319一致。\n\n"
        "已入库21强主正/2728排除/22边界不变；另2条source-only标题边界。2771题其余内容、cases、history、facts、census及184源文件逐字节或逐记录相同。只改questions、scopes、manifest、README及freeze五个文件；v1所有193文件hash未变。\n\n"
        "v1模型首测已经进行，故v2 predictions_seen_before_freeze=true；source-only审核者未看预测，修订依据独立原文合同审核。当前sealed run及v1评分不改，不冒充初始blind。v1/v2严格校验通过，diff与parent/v2摘要在相邻JSON。\n", encoding="utf-8")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=BASE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--report", type=Path, default=REPORT)
    args = parser.parse_args()
    print(revise(args.base, args.output, args.report))


if __name__ == "__main__":
    main()
