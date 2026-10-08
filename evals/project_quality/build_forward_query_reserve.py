"""Keep fifty new composite scenarios unrun for the next refinement."""
from datetime import datetime, timezone
from pathlib import Path

from eval.common import digest, read_json, write_json, write_jsonl
from eval.validate_gold import load_dataset, require, validate_gold
from evals.project_quality.build_gold import AUTHORIZATION, FACTS_HASH
from evals.project_quality.build_query_holdout import listing
from interview_intelligence.contracts import FilterSpec


def build():
    snapshot = Path("data/reports/resume-quality-snapshot-20261005-v1")
    facts = read_json(snapshot / "facts.json")
    require(digest(facts) == FACTS_HASH, "SNAPSHOT_CHANGED")
    output = Path("data/gold/query-forward-reserved-agent-20261006-v4")
    require(not output.exists(), "GOLD_VERSION_ALREADY_EXISTS")
    cases, at = [], datetime.now(timezone.utc).isoformat()
    for i, topic in enumerate(("Java", "Redis", "数据库", "Spring", "AI")):
        definitions = [
            (f"按频次展示界面当前范围，不限总数，每页11道", {"topic_l1": topic, "round": "SECOND", "language": "JAVA"}, None, None, None, 11),
            (f"{topic}一面中的算法代码题按频次取13道", {"topic_l1": topic, "round": "FIRST", "response_form": "CODE", "coding_focus": "ALGORITHM"}, "只取消技术大类，轮次、算法代码和数量都保留", {"topic_l1": None, "round": "FIRST", "response_form": "CODE", "coding_focus": "ALGORITHM"}, 13, None),
            (f"{topic}口述题且JAVA语言标签，按频次取19道", {"topic_l1": topic, "response_form": "VERBAL", "language": "JAVA"}, "只取消口述形式，技术分类、语言和数量都保留", {"topic_l1": topic, "response_form": None, "language": "JAVA"}, 19, None),
            (f"{topic}里的口述工程构造任务，频次取8道", {"topic_l1": topic, "response_form": "VERBAL", "coding_focus": "ENGINEERING"}, "仅取消任务焦点，仍只要口述，分类数量不变", {"topic_l1": topic, "response_form": "VERBAL", "coding_focus": None}, 8, None),
            (f"{topic}二面且Python语言的题，频次取12道", {"topic_l1": topic, "round": "SECOND", "language": "PYTHON"}, "取消语言限制，轮次改为三面，分类数量不变", {"topic_l1": topic, "round": "THIRD", "language": None}, 12, None),
            (f"阿里云的{topic}分类频次取9道", {"topic_l1": topic, "company": "阿里云"}, "只取消公司，保持原分类和数量", {"topic_l1": topic, "company": None}, 9, None),
            (f"{topic}按发布时间筛2026年7月1日到9月30日，频次取16道", {"topic_l1": topic, "date_basis": "PUBLISH", "start_date": "2026-07-01", "end_date": "2026-10-01"}, "只取消结束日期，仍按发布时间，起始日期分类数量不动", {"topic_l1": topic, "date_basis": "PUBLISH", "start_date": "2026-07-01", "end_date": None}, 16, None),
            (f"{topic}写SQL的工程题，频次取20道", {"topic_l1": topic, "response_form": "SQL", "coding_focus": "ENGINEERING"}, "只把回答形式改为写代码，保持工程焦点分类数量", {"topic_l1": topic, "response_form": "CODE", "coding_focus": "ENGINEERING"}, 20, None),
            (f"腾讯二面的{topic}口述工程构造题，频次取15道", {"topic_l1": topic, "company": "腾讯", "round": "SECOND", "response_form": "VERBAL", "coding_focus": "ENGINEERING"}, "只取消公司和轮次，工程口述和技术分类数量保留", {"topic_l1": topic, "company": None, "round": None, "response_form": "VERBAL", "coding_focus": "ENGINEERING"}, 15, None),
            (f"{topic}HR轮的题，频次取17道", {"topic_l1": topic, "round": "HR"}, "范围数量不变，只按面试日期筛2026年8月，包含整个8月", {"topic_l1": topic, "round": "HR", "date_basis": "INTERVIEW", "start_date": "2026-08-01", "end_date": "2026-09-01"}, 17, None),
        ]
        for j, (message, scope, followup, after, count, size) in enumerate(definitions):
            for filters in (scope, after):
                if filters is not None:
                    FilterSpec.model_validate(filters)
            turns = [listing(facts, message, scope, count, size, **(
                {"explicit_filters": scope, "default_page_size": 11} if j == 0 else {}))]
            if followup:
                turns.append(listing(facts, followup, after, count))
            cases.append({"id": f"query-forward-{i}-{j}", "group_id": f"forward-{i}-{j}", "split": "test",
                          "human_verified": False, "agent_verified": True, "tags": ["unrun_forward_reserve", "composite_clear_and_inherit"],
                          "turns": turns, "review": {"reviewer": "Codex", "reviewer_kind": "agent", "reviewed_at": at,
                              "guide_version": "project_quality_agent_v2",
                              "basis": "New explicit composite requirements; independent frozen-fact SQL IDs/counts, and typed filter contract validated before inference."}})
    require(len(cases) == 50, "RESERVE_COUNT")
    output.mkdir()
    write_jsonl(output / "routing.jsonl", cases)
    write_json(output / "manifest.json", {"version": output.name, "status": "frozen", "kind": "corpus",
        "snapshot": facts["snapshot"], "routing": "routing.jsonl", "review_authorization": AUTHORIZATION,
        "facts_sha256": FACTS_HASH, "annotation_guide": "evals/project_quality/review_protocol_v2.json",
        "frozen_at": at, "review_recipe_sha256": digest(Path(__file__).read_bytes()),
        "review_lifecycle": "Forward reserve authored before any inference on these fifty scenarios; zero predictions.",
        "limitations": "New authored combinations within the same corpus and contract; not natural traffic or semantic-family independence."})
    write_json(output / "freeze.json", {"dataset_sha256": digest(load_dataset(output)),
        "file_hashes": {p.name: digest(p.read_bytes()) for p in output.iterdir() if p.is_file() and p.name != "freeze.json"}})
    validate_gold(output, "routing", review_policy="delegated_agent")
    print({"dataset": str(output), "scenarios": len(cases), "turns": sum(len(c["turns"]) for c in cases), "model_calls": 0})


if __name__ == "__main__":
    build()
