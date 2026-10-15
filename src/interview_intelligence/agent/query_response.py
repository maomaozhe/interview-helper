"""Present completed query facts without changing execution or session state."""

from interview_intelligence.agent.query_contract import QuerySpec


def _answer(plan: QuerySpec, facts: dict, search_ranking: str | None) -> str:
    rows = facts.get("data", [])
    meta = facts["meta"]
    if plan.action == "ANSWER":
        return plan.answer_text
    if plan.action == "COUNT":
        labels = [str(value) for value in (plan.filters.company, plan.filters.topic_l1,
                                         plan.filters.topic_l2) if value]
        scope = f"当前筛选范围（{' / '.join(labels)}）" if labels else "当前筛选范围"
        if meta["count_scope"] != "filtered_corpus":
            scope = "当前全库"
        counts = meta["counts"]
        return (f"{scope}共有 {counts['canonical_questions']:,} 道去重题目，"
                f"{counts['occurrences']:,} 条提问记录，来自 "
                f"{counts['interviews']:,} 场面试、{counts['source_documents']:,} 篇面经。")
    if plan.action == "CLARIFY":
        return plan.clarification
    if plan.action == "RECORD_REVIEW":
        return f"已保存 {len(plan.review_items)} 道题的复习记录。"
    if plan.action == "REVIEW_STATE":
        return "已读取当前题目的复习状态。"
    if plan.action in {"LIST", "NEXT"} and plan.sort == "frequency":
        if plan.semantic_task:
            pagination = meta["pagination"]
            return (f"当前已收录的{plan.filters.company or '所选范围'}面经中，共有 "
                    f"{pagination['result_total']} 道去重算法题、"
                    f"{meta['sample_counts']['selected_occurrences']} 次算法提问；"
                    f"本页显示 {len(rows)} 道，按真实提问频次排序。"
                    + ("可继续查看下一页。" if pagination["next_cursor"] else "已显示到最后一页。"))
        return f"返回 {len(rows)} 道题，按真实提问频次排序。"
    if meta.get("relevance_status") == "UNAVAILABLE":
        return "相关性核验未完成，请重试。"
    if plan.action == "SEARCH":
        verified = meta.get("relevance_status") == "VERIFIED"
        if search_ranking == "frequency":
            if verified:
                return f"本次语义核验候选中，按当前筛选范围的真实提问频次排序，返回 {len(rows)} 道题。"
            return f"本次检索候选中，按当前筛选范围的真实提问频次排序，返回 {len(rows)} 道题；相关性未核验。"
        if verified:
            return f"找到 {len(rows)} 道经语义筛选的题目，按相关度排列。"
    return f"返回 {len(rows)} 条结果。"


def build_query_response(plan: QuerySpec, facts: dict, *, search_ranking: str | None,
                         tool_name: str, prompt_version: str, warnings: list[str]) -> dict:
    """Keep authoritative facts and warnings attached by reference to the receipt."""
    return {
        "intent": plan.action,
        "answer": _answer(plan, facts, search_ranking),
        "facts": facts,
        "planning": {"spec": plan.model_dump(mode="json"), "version": prompt_version},
        "tool_trace": [{"name": tool_name, "parameters": plan.model_dump(mode="json"),
                        "elapsed_ms": facts["meta"]["timings"]["tool_ms"]}],
        "warnings": warnings,
    }
