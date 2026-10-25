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
        selected = (meta.get("collection_selection") or {}).get("selected") or {}
        if selected.get("kind") == "category":
            scope = f"当前分类「{selected['label']}」"
        if meta["count_scope"] != "filtered_corpus":
            scope = "当前全库"
        counts = meta["counts"]
        if plan.semantic_task == "ENGINEERING_DESIGN" and not meta["semantic_scope"]["complete"]:
            pending = meta["pending_counts"]
            return (f"{scope}已确认 {counts['canonical_questions']:,} 道工程系统设计题、"
                    f"{counts['occurrences']:,} 次提问；另有 {pending['canonical_questions']:,} 道题的"
                    f"{pending['occurrences']:,} 次提问待核验（可能与确认题重合），完整总数尚未确定。")
        return (f"{scope}共有 {counts['canonical_questions']:,} 道去重题目，"
                f"{counts['occurrences']:,} 条提问记录，来自 "
                f"{counts['interviews']:,} 场面试、{counts['source_documents']:,} 篇面经。")
    if plan.action == "CLARIFY":
        return plan.clarification
    if plan.action == "RECORD_REVIEW":
        return f"已保存 {len(plan.review_items)} 道题的复习记录。"
    if plan.action == "REVIEW_STATE":
        return "已读取当前题目的复习状态。"
    if meta.get("relevance_status") == "UNAVAILABLE":
        return "本次语义核验未完成，未返回未经核验的题目，请稍后重试。"
    if meta.get("candidate_workset_progress"):
        progress = meta["candidate_workset_progress"]
        answer = (f"完整工程候选范围共 {progress['candidate_total']} 道，候选数量不是业务题总数。"
            f"已核验 {progress['processed']} 道，其中确认匹配 {progress['confirmed']} 道、"
            f"待核验 {progress['pending']} 道、排除 {progress['excluded']} 道；本页显示 {len(rows)} 道。")
        if progress["unprocessed"]:
            answer += f"还有 {progress['unprocessed']} 道候选未核验。"
            answer += ("可继续核验并翻页。" if progress["next_requires_processing"] else "可继续查看缓存中的下一页。")
        else:
            answer += "全部候选已处理，待核验项仍保留。"
            answer += "可继续查看缓存中的下一页。" if meta["pagination"]["next_cursor"] else "已到当前结果最后一页。"
        return answer
    if (meta.get("pagination") or {}).get("kind") == "semantic_candidates":
        counts = meta["scope_candidate_counts"]
        label = "当前业务范围" if (meta.get("relevance_scope") or {}).get("target_unit") == "BUSINESS_APPLICATION" else "当前题意范围"
        return (f"{label}的本次候选中，已确认匹配 {counts['confirmed']} 道，"
            f"另有 {counts['pending']} 道范围待核验；本页显示 {len(rows)} 道。"
            "结果来自本次最多50个候选，不代表全库全部匹配题；范围待核验项单独标记，不当作已确认。"
            + ("可继续查看下一页。" if meta["pagination"]["next_cursor"] else "已显示到本次候选最后一页。"))
    if plan.action in {"STATS", "NEXT"} and plan.group_by != "question" and plan.semantic_task == "ENGINEERING_DESIGN":
        pagination = meta["pagination"]
        notice = ("完整总数尚未确定；各组分别展示确认提问与待核验提问。"
                  if not meta["semantic_scope"]["complete"] else "统计基于完整审核范围的确认提问。")
        return (f"本页显示 {len(rows)} 个统计分组。" + notice
                + ("可继续查看下一页。" if pagination["next_cursor"] else "已显示到最后一页。"))
    if plan.action in {"LIST", "NEXT"} or plan.action == "STATS" and plan.group_by == "question":
        if meta.get("collection_category"):
            category = meta["collection_category"]
            counts, pagination = category["counts"], meta["pagination"]
            return (f"按已有主题浏览「{category['label']}」：本分类可浏览 {counts['visible']} 道，"
                    f"已确认 {counts['confirmed']} 道、{counts['pending']} 道含待核验提问。"
                    f"本页显示 {len(rows)} 道；分类不明确和待核验题保留在各自分类，可切换全量分页。"
                    + ("可继续查看下一页。" if pagination["next_cursor"] else "已显示到最后一页。"))
        if plan.semantic_task:
            pagination = meta["pagination"]
            label = "工程系统设计" if plan.semantic_task == "ENGINEERING_DESIGN" else "算法"
            order = {"frequency": "真实提问频次", "importance": "重要性", "gap": "复习薄弱程度"}[plan.sort]
            if not meta["semantic_scope"]["complete"]:
                counts = meta["semantic_counts"]
                return (f"当前范围已确认 {counts['confirmed_canonical_questions']} 道{label}题、"
                        f"{counts['confirmed_occurrences']} 次提问；另有 "
                        f"{counts['pending_canonical_questions']} 道题包含待核验提问"
                        f"（其中 {counts['pending_only_canonical_questions']} 道尚无确认提问）。"
                        f"本页显示 {len(rows)} 道，按已确认提问的{order}排序，待核验项保留并标记；"
                        "完整总数尚未确定。" + ("可继续查看下一页。" if pagination["next_cursor"] else "已显示到最后一页。"))
            return (f"当前已收录的{plan.filters.company or '所选范围'}面经中，共有 "
                    f"{pagination['result_total']} 道去重{label}题、"
                    f"{meta['sample_counts']['selected_occurrences']} 次{label}提问；"
                    f"本页显示 {len(rows)} 道，按{order}排序。"
                    + ("可继续查看下一页。" if pagination["next_cursor"] else "已显示到最后一页。"))
        if plan.sort == "frequency":
            return f"返回 {len(rows)} 道题，按真实提问频次排序。"
    if meta.get("relevance_status") == "UNAVAILABLE":
        return "相关性核验未完成，请重试。"
    if plan.action == "SEARCH":
        verified = meta.get("relevance_status") == "VERIFIED"
        if meta.get("verification_basis") == "empty_sql_scope":
            return "当前筛选范围没有可检索题目。"
        partial = meta.get("candidate_verification_status") == "PARTIAL"
        coverage = "本次结果来自检索候选，不代表题库全部匹配题。"
        if partial:
            coverage = "部分候选因证据或格式异常未能完成核验，结果可能不完整。" + coverage
        if search_ranking == "frequency":
            if verified:
                return f"本次语义核验候选中，按当前筛选范围的真实提问频次排序，返回 {len(rows)} 道题。" + coverage
            return f"本次检索候选中，按当前筛选范围的真实提问频次排序，返回 {len(rows)} 道题；相关性未核验。" + coverage
        if verified:
            return f"本次候选中找到 {len(rows)} 道经语义筛选的题目，按相关度排列。" + coverage
        return f"返回 {len(rows)} 条检索候选，相关性未核验。" + coverage
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
