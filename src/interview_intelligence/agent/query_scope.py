"""Bind query restrictions to user language instead of a planner's rewrite.

These guards cover explicit taxonomy selection, natural scenario/design search,
and the one supported complete semantic set. They are not a general NL parser.
"""
from __future__ import annotations

import re

from interview_intelligence.relevance_scope import (
    compile_relevance_scope, semantic_messages, validate_relevance_scope,
)

VERSION = "query_user_scope_v1"
RECALL_VERSION = "source_only_recall_v1"
_CONTINUATION = re.compile(
    r"^(?:再|继续|更多|还有|下一|下页|同上|同类|这些|这类|这批|其中|在这|在刚才|上面|刚才|前面|"
    r"重查|重新|那|它们|只看|只要|仅看|仅要|按|改按|不按|不再|不要|不限|取消|去掉|"
    r"我说的是|相关(?:的)?(?:再|继续|更多|还)|一共|总共|有多少|多少|前\s*\d|全部|所有|都要)")
_NEW_GOAL = re.compile(r"^(?:换(?:个|一个|成|为|到|话题|方向)|改查|改找|另找|现在(?:查|找|看)|不聊|不看刚才)")
_SCENARIO = re.compile(r"场景|设计(?:题|类|问题)|系统设计|架构(?:题|设计)", re.I)
_ALGORITHM = re.compile(r"算法|\balgorithm(?:ic|s)?\b|\bleetcode\b", re.I)
_NEGATION = re.compile(r"(?:不(?:要|是|限|按|再)|别|排除|无需|不用|取消|去掉|不限于|not|exclude).{0,10}$", re.I)
_COMPLETE = re.compile(r"全库|全集|全部|所有|完整|穷尽|(?:总共|一共|总计)|最高频|频次|频率|出现次数|高频", re.I)
_ENGINEERING = re.compile(r"(?:工程(?:系统|场景)?|业务系统|后端系统)(?:的)?设计(?:题|类|问题|集合)|系统设计题", re.I)
_BROAD_ENGINEERING = re.compile(r"工程(?:系统|场景)?(?:的)?设计(?:题|类|问题|集合)|系统设计题", re.I)
_ALL = re.compile(r"全库|全集|全部|所有|完整|全量|穷尽", re.I)
_COUNT = re.compile(r"多少|几道|数量|总数|总共|一共", re.I)
_ROUND_NAMES = {
    "FIRST": ("第一轮", "第1轮", "第一面", "1面", "一面", "首轮"),
    "SECOND": ("第二轮", "第2轮", "第二面", "2面", "二面"),
    "THIRD": ("第三轮", "第3轮", "第三面", "3面", "三面"),
    "FOURTH_PLUS": ("四面及以上", "第四轮及以上", "四面", "第四轮"),
    "HR": ("HR面", "HR轮"),
}


def engineering_collection_request(messages, filters=None, *, prior_filters=None, group_by=None):
    """Recognize the supported broad task without swallowing extra semantics.

    This is an admission grammar for an audited collection, not a classifier of
    interview questions. Unknown qualifiers require a narrower audited scope or
    clarification; they never disappear into the broad collection.
    """
    text = "\n".join(messages)
    if not _positive(_ENGINEERING, text):
        return False, False
    residual = _BROAD_ENGINEERING.sub("", text)
    if filters is not None:
        from interview_intelligence.company import COMPANY_ALIASES
        for field in ("company", "position", "language"):
            values = {getattr(filters, field, None), (prior_filters or {}).get(field)}
            for value in values - {None}:
                names = [str(value), *[alias for alias, name in COMPANY_ALIASES.items() if name == value]]
                for name in sorted(names, key=len, reverse=True):
                    residual = re.sub(re.escape(name), "", residual, flags=re.I)
        for value in {getattr(filters, "round", None), (prior_filters or {}).get("round")} - {None}:
            for name in (*_ROUND_NAMES.get(value, ()), str(value)):
                residual = re.sub(re.escape(name), "", residual, flags=re.I)
    if group_by in {"company", "round", "topic"}:
        # Group labels are not generic filler: only consume the dimension that
        # the typed STATS plan actually selected.
        names = {"company": ("公司", "企业"), "round": ("轮次", "轮面"),
                 "topic": ("主题", "知识点")}[group_by]
        for name in names:
            residual = re.sub(rf"(?:按|各){name}|{name}(?:分布|统计|占比)", "", residual)
    residual = re.sub(r"(?:请|帮我|麻烦|我想|我要|需要|给我|把|一下|列出|列举|展示|找出|查找|查询|查看|看看|找|查|看|有哪些|"
        r"全库|全集|全部|所有|完整|全量|穷尽|总共|一共|总计|总数|数量|多少|几道|按|真实|提问|出现|频次|频率|次数|"
        r"高频|最高频|重要性|薄弱|排序|排名|统计|列表|题目|问题|集合|题|的|都|第?一页|下一页|继续|重查|重新检索|重新查询|重新|再|这些|这类|这批|其中|上面|刚才|前面|它们|有|前\s*\d+|每页\s*\d+|道|个|条|和|与|及)", "", residual)
    residual = re.sub(r"[\s，,。；;！？!?、:：()（）\-]", "", residual)
    return True, not residual


def engineering_all_source_messages(messages, filters=None):
    """Original language granting the engineering superset, even with refinement.

    A business goal alone is not permission to restrict free search to an old
    engineering annotation.  The explicit upstream ALL request can appear in
    the same sentence as its business refinement.
    """
    marker = re.compile(r"工程(?:系统|场景)?(?:的)?设计(?:题|类|问题|集合)|(?<!业务)(?<!后端)系统设计题", re.I)
    return [message for message in messages if isinstance(message, str)
        and _positive(_ALL, message) and _positive(marker, message)]


def collection_sort(message):
    cues = [(match.start(), value) for pattern, value in (
        (r"频次|频率|次数|高频|frequency", "frequency"),
        (r"重要性|importance", "importance"), (r"薄弱|gap", "gap"))
        for match in re.finditer(pattern, message, re.I)]
    return max(cues)[1] if cues else None


def collection_top_n(messages):
    """The latest user limit wins; a later unrestricted 'all' clears it."""
    for message in reversed(messages):
        matches = list(re.finditer(r"前\s*(\d+)\s*(?:道|个|条|题)?", message))
        if matches:
            return int(matches[-1].group(1))
        if _ALL.search(message):
            return None
    return None


def collection_page_size(messages):
    for message in reversed(messages):
        matches = list(re.finditer(r"每页\s*(\d+)\s*(?:道|个|条|题)?", message))
        if matches:
            return int(matches[-1].group(1))
    return None


def inherited_ui_filters(run):
    """Recover UI provenance, never promote an old model plan to a UI filter."""
    if not continues_scope(run):
        return {}
    saved = run.state.get("query_scope_intent") or {}
    if saved.get("version") == VERSION and "explicit_filters" in saved:
        return dict(saved["explicit_filters"])
    return dict(run.state.get("list_explicit_filters") or {})


def _current_filter_value(message, key, value):
    """A changed model field needs current language supporting that value."""
    if value is None:
        return False
    if key in {"question_type", "topic_l1", "topic_l2", "coding_focus"}:
        return explicit_taxonomy_value([message], key, value)
    names = [str(value)]
    if key == "company":
        from interview_intelligence.company import COMPANY_ALIASES
        names.extend(alias for alias, name in COMPANY_ALIASES.items() if name == value)
    elif key == "round":
        names.extend(_ROUND_NAMES.get(value, ()))
    return any(name.casefold() in message.casefold() for name in names)


def _engineering_filters(plan, explicit, messages):
    """Keep user filters; remove model-only taxonomy/domain restrictions."""
    text = "\n".join(messages)
    changes = {}
    for key in ("question_type", "topic_l1", "topic_l2", "coding_focus"):
        value = getattr(plan.filters, key)
        if key not in explicit and value is not None and not explicit_taxonomy_value(messages, key, value):
            changes[key] = None
    cues = {"job_family": r"后端|backend|AI应用|算法岗", "language": r"Java|Python|Go|C\+\+|Rust|SQL",
            "response_form": r"手写|手撕|代码|编程|口述|CODE|SQL|VERBAL", "annotation_status": r"KNOWN|VERIFIED|标注状态"}
    for key, cue in cues.items():
        if key not in explicit and getattr(plan.filters, key) is not None and not re.search(cue, text, re.I):
            changes[key] = None
    from interview_intelligence.company import COMPANY_ALIASES
    for key in ("company", "position"):
        value = getattr(plan.filters, key)
        aliases = [str(value), *[alias for alias, normalized in COMPANY_ALIASES.items() if normalized == value]]
        if key not in explicit and value is not None and not any(name.casefold() in text.casefold() for name in aliases):
            changes[key] = None
    if not re.search(r"最近|近\s*\d|今年|去年|\d{4}|日期|时间范围|月|天", text):
        for key in ("start_date", "end_date"):
            if key not in explicit and getattr(plan.filters, key) is not None:
                changes[key] = None
    if "round" not in explicit and plan.filters.round is not None and not re.search(r"[一二三四五12345]面|第?[一二三四五12345]轮|首轮|轮次|轮面|FIRST|SECOND|THIRD|HR", text, re.I):
        changes["round"] = None
    return plan.filters.model_copy(update=changes), changes


def _positive(pattern, text):
    return any(not _NEGATION.search(re.split(r"[，,。；;！？!?\n]", text[:m.start()])[-1])
               for m in pattern.finditer(text))


def explicit_taxonomy_value(messages, key, value):
    if value is None:
        return False
    value = getattr(value, "value", value)
    aliases = {"SCENARIO": ("场景", "情景"), "SYSTEM_DESIGN": ("系统设计",),
               "KNOWLEDGE": ("知识",), "PROJECT": ("项目",), "ALGORITHM": ("算法",),
               "AI": ("人工智能",), "ENGINEERING": ("工程",)}
    labels = (str(value), *aliases.get(str(value), ()))
    selector = re.compile({"question_type": r"题型|分类|标签|question_type",
        "topic_l1": r"主题|分类|标签|topic_l1", "topic_l2": r"主题|分类|标签|topic_l2",
        "coding_focus": r"任务焦点|编程任务|coding_focus"}[key], re.I)
    for message in reversed(messages):
        for clause in re.split(r"[，,。；;！？!?\n]", message):
            if key == "question_type" and re.search(r"主题|topic_l[12]", clause, re.I) and not re.search(r"题型|question_type", clause, re.I):
                continue
            if key.startswith("topic_") and re.search(r"题型|question_type", clause, re.I) and not re.search(r"主题|topic_l[12]", clause, re.I):
                continue
            if any(label.casefold() in clause.casefold() for label in labels) and re.search(r"取消|去掉|不再|不限|不按|不要|排除", clause):
                return False
            if _positive(selector, clause) and any(label.casefold() in clause.casefold() for label in labels):
                return True
    return False


def continues_scope(run):
    current = run.request.message.strip()
    if current.rstrip("。！!？? ") in {"全量分页", "全量", "全部", "全部分页", "查看全量", "查看全部"}:
        return True
    return bool(_CONTINUATION.search(current)) and not _NEW_GOAL.search(current)


def clarification_fragment(message):
    """A short answer can fill a pending goal without matching model options.

    New commands and complete questions stand on their own. This deliberately
    uses only the user's form, never the assistant's suggested target.
    """
    return bool(message) and len(message) <= 80 and not _NEW_GOAL.search(message) and not re.search(
        r"题|问题|[？?！!。\n]|(?:请|帮我)?(?:找|查|列|统计|解释|介绍|分析|回答)|多少|如何|怎么|为什么|有哪些", message)


def user_messages(run):
    """Only original user messages; never clarification text or model plans."""
    current = run.request.message.strip()
    messages = []
    continuation = continues_scope(run)
    pending = run.state.get("pending_clarification") or {}
    # A fresh named target must not silently inherit an unrelated old goal.
    # Exact option selection binds the user's selection, not all model options.
    selected = current in pending.get("options", [])
    if selected and current == "查看完整工程设计集合":
        return [current]
    if pending and (continuation or selected or clarification_fragment(current)):
        messages.append(pending.get("original_message", ""))
    origin = getattr(run, "requery_origin", None) or {}
    if origin and (continuation or current == origin.get("original_message")):
        messages.extend(origin.get("user_scope_messages") or [origin.get("original_message", "")])
    elif continuation and not pending:
        saved = run.state.get("query_scope_intent") or {}
        if saved.get("version") == VERSION:
            messages.extend(saved.get("messages", []))
        else:
            # Legacy fallback is original language, not the old semantic_task.
            source = (run.state.get("list_goal") if current.startswith("下一") else
                      run.state.get("conversation_focus")) or run.state.get("last_response") or {}
            messages.append(source.get("message", ""))
    messages.append(current)
    # Repeated corrections have order: exclude -> allow -> exclude must end
    # excluded. Only collapse adjacent copies introduced by restoration.
    ordered = []
    for message in messages:
        if isinstance(message, str) and message.strip() and (not ordered or ordered[-1] != message):
            ordered.append(message)
    return ordered or [run.request.message]


def relevance_intent(messages):
    messages = semantic_messages(messages)
    if not messages:
        raise ValueError("RELEVANCE_SCOPE_GOAL_MISSING")
    if len(messages) == 1:
        return messages[0]
    return "用户此前原话：\n" + "\n".join(messages[:-1]) + "\n用户本轮原话（纠正优先）：\n" + messages[-1]


def recall_intent(messages):
    """Use source language for soft recall, without evidence-wrapper headings.

    The complete ordered messages remain in the host relevance scope. The
    bounded recall field is not an authorization or semantic membership test;
    when long, keep its latest correction rather than an old broad request.
    """
    messages = semantic_messages(messages)
    if not messages:
        raise ValueError("RELEVANCE_SCOPE_GOAL_MISSING")
    return "\n".join(messages)[-500:]


def guard_plan(run, name, plan, explicit, *, structured=False):
    """Return a safe executable route and an auditable scope decision."""
    from interview_intelligence.agent.query_contract import QuerySpec

    messages = user_messages(run)
    text = "\n".join(messages)
    current = messages[-1]
    policy = {"version": VERSION, "user_messages": messages,
              "planned_action": plan.action, "route_decision": "unchanged",
              "semantic_task": {"planned": plan.semantic_task, "applied": plan.semantic_task,
                                "decision": "unchanged"}}
    if structured:
        # A typed SQL operation is a new, explicitly selected scope. A previous
        # semantic snapshot cannot remain active under this different route.
        for key in ("relevance_scope", "semantic_candidate_pages", "semantic_recall", "relevance_scope_recovery",
                "candidate_workset_snapshot", "engineering_collection_authorization", "complete_workset_mode"):
            run.state.pop(key, None)
        policy["route_decision"] = "structured_request"
        return name, plan, policy
    if plan.action in {"ANSWER", "DETAILS", "REVIEW_STATE", "RECORD_REVIEW"}:
        # Read/explain/review operations use the current page but do not replace
        # its retrieval scope or invalidate the durable next-page snapshot.
        policy["route_decision"] = "preserve_current_retrieval_scope"
        return name, plan, policy
    prior_scope = run.state.get("relevance_scope")
    if prior_scope is not None:
        prior_scope = validate_relevance_scope(prior_scope)
    raw_current = run.request.message.strip()
    selected_candidate = (raw_current == "按题意查找候选"
        and raw_current in (run.state.get("pending_clarification") or {}).get("options", []))
    retains_candidate_mode = bool(prior_scope and prior_scope["retrieval_mode"] == "CANDIDATES"
        and (continues_scope(run) or getattr(run, "requery_origin", None) or raw_current in prior_scope["source_messages"]))
    scope = compile_relevance_scope(messages, candidate_mode=selected_candidate or retains_candidate_mode)
    if scope is not None:
        policy["relevance_scope"] = scope
        policy["user_messages"] = semantic_messages(messages)
        run.state["relevance_scope"] = scope
    else:
        run.state.pop("relevance_scope", None)
    if plan.semantic_task:
        # A current denial overrides an earlier positive; a clarification's
        # suggested 'algorithm' option alone is never authorization.
        if plan.semantic_task == "ENGINEERING_DESIGN":
            granted = (_positive(_ENGINEERING, current) if _ENGINEERING.search(current)
                       else _positive(_ENGINEERING, text))
        else:
            granted = (_positive(_ALGORITHM, current) if _ALGORITHM.search(current)
                       else _positive(_ALGORITHM, text))
        if not granted:
            plan = plan.model_copy(update={"semantic_task": None})
            policy["semantic_task"].update(applied=None, decision="unsupported_model_scope_removed")
            if _ALGORITHM.search(current) and re.search(r"不要|排除|不看|别", current):
                plan = QuerySpec(action="CLARIFY", filters=plan.filters,
                    clarification="本轮要求排除算法题，不能反向套用算法全集或直接返回未排除的全库。请明确可用的标签范围，或改为按题意查找候选。")
                name = "list_questions"
                policy["route_decision"] = "negative_semantic_scope_requires_clarification"
    exact_type = explicit_taxonomy_value(messages, "question_type", plan.filters.question_type)
    prior_filters = ((run.state.get("conversation_focus") or {}).get("filters") or {}) if continues_scope(run) else {}
    engineering, broad_engineering = engineering_collection_request(messages, plan.filters,
        prior_filters=prior_filters, group_by=plan.group_by if plan.action == "STATS" else None)
    complete_engineering = engineering and (_ALL.search(text) or _COUNT.search(current) or plan.semantic_task == "ENGINEERING_DESIGN")
    candidate_opt_in = selected_candidate or retains_candidate_mode
    if candidate_opt_in and plan.semantic_task == "ENGINEERING_DESIGN":
        plan = plan.model_copy(update={"semantic_task": None})
    if selected_candidate and plan.action in {"LIST", "COUNT", "STATS", "SEARCH", "CLARIFY"}:
        # A trusted option is an execution-mode choice. Its model proposal must
        # not reinterpret the choice, lose the business goal, or ask it again.
        goal = relevance_intent(messages)
        plan = QuerySpec(action="SEARCH", filters=plan.filters, sort=plan.sort,
            page_size=min(plan.page_size, 50),
            search_query=plan.search_query if plan.action == "SEARCH" else recall_intent(messages),
            relevance_query=goal if len(goal) <= 500 else None, final=True)
        name = "search_questions"
        policy["route_decision"] = "selected_semantic_candidates"
    if complete_engineering and not candidate_opt_in and plan.action in {"LIST", "SEARCH", "COUNT", "STATS", "NEXT"} and plan.action != "NEXT" and not exact_type:
        requested_top_n = collection_top_n(messages)
        requested_page_size = collection_page_size(messages)
        if requested_page_size is not None and not 1 <= requested_page_size <= 100:
            plan = QuerySpec(action="CLARIFY", filters=plan.filters,
                clarification="每页题数必须在1到100之间，请调整每页数量。")
            name = "list_questions"
            policy["route_decision"] = "engineering_collection_page_size_out_of_range"
        elif requested_top_n is not None and not 1 <= requested_top_n <= 1000:
            plan = QuerySpec(action="CLARIFY", filters=plan.filters,
                clarification="前N题的N必须在1到1000之间；请调整上限，或查看不截取前N的完整集合。")
            name = "list_questions"
            policy["route_decision"] = "engineering_collection_top_n_out_of_range"
        elif requested_top_n is not None and plan.action == "COUNT" and re.search(r"前\s*\d+", current):
            plan = QuerySpec(action="CLARIFY", filters=plan.filters,
                clarification="本轮指定了前N题的子集，不能直接统计整个工程设计集合；请先列出该前N题子集再统计。")
            name = "list_questions"
            policy["route_decision"] = "engineering_collection_top_n_count_requires_subset"
        elif not broad_engineering:
            workset_available = bool(scope and scope["target_unit"] == "BUSINESS_APPLICATION"
                and engineering_all_source_messages(messages, plan.filters))
            plan = QuerySpec(action="CLARIFY", filters=plan.filters,
                clarification=("已保留你的限定条件；" +
                    ("当前按具体业务应用或业务流程设计查找，AI业务应用不因技术领域被排除，通用基础组件不替代业务目标。"
                     if scope and scope["target_unit"] == "BUSINESS_APPLICATION" else "") +
                    ("可以在完整工程候选集合中分批核验；候选数量不是业务题总数。待核验和分类不明确的题保留。请选择候选核验或全量分页。"
                     if workset_available else "当前完整审核集合只覆盖宽范围的工程系统设计任务。这个细分范围尚不能保证全集，可按题意检索候选，或明确切换完整工程设计集合。")),
                clarification_options=["按题意查找候选", "全量分页", "查看完整工程设计集合"] if workset_available else ["按题意查找候选", "查看完整工程设计集合"])
            name = "list_questions"
            policy["route_decision"] = "qualified_engineering_collection_not_audited"
        else:
            inherited_ui = inherited_ui_filters(run)
            values = plan.filters.model_dump(mode="json")
            restored_ui = {key: value for key, value in inherited_ui.items()
                           if key not in explicit and values.get(key) != value
                           and not _current_filter_value(current, key, values.get(key))}
            if restored_ui:
                # An omitted model field cannot cancel the user's UI. A current
                # UI clear or an explicitly changed non-null text value wins.
                filters = type(plan.filters).model_validate({**plan.filters.model_dump(mode="json"), **restored_ui})
                plan = plan.model_copy(update={"filters": filters})
            values = plan.filters.model_dump(mode="json")
            trusted_ui = {key: value for key, value in inherited_ui.items()
                          if key not in explicit and values.get(key) == value}
            filters, removed = _engineering_filters(plan, {**trusted_ui, **explicit}, messages)
            action = "COUNT" if _COUNT.search(current) and plan.action != "STATS" else "STATS" if plan.action == "STATS" else "LIST"
            plan = plan.model_copy(update={"action": action, "semantic_task": "ENGINEERING_DESIGN",
                "filters": filters, "top_n": None if action == "COUNT" else requested_top_n if requested_top_n is not None else None if _ALL.search(text) else plan.top_n,
                "page_size": requested_page_size if requested_page_size is not None else plan.page_size,
                "sort": collection_sort(current) or plan.sort,
                "search_query": None, "relevance_query": None, "lexical_facets": [],
                "preferred_question_type": None, "final": True})
            name = {"COUNT": "get_question_count", "STATS": "get_question_stats"}.get(action, "list_questions")
            policy["route_decision"] = "complete_engineering_collection"
            policy["removed_model_filters"] = removed
            policy["semantic_task"].update(applied="ENGINEERING_DESIGN", decision="original_user_task")
    natural_scenario = bool(_SCENARIO.search(text)) and not exact_type
    explicit_type = explicit.get("question_type") is not None
    if natural_scenario and not explicit_type and not plan.semantic_task and plan.action in {"LIST", "COUNT", "STATS"}:
        complete_text = re.sub(r"(?:全部|所有)方向", "方向", text)
        if plan.action != "LIST" or (_COMPLETE.search(complete_text) and not candidate_opt_in) or plan.review_statuses:
            # SEARCH cannot compute a semantic corpus total/global ranking, or
            # faithfully apply the SQL-only personal review filters.
            plan = QuerySpec(action="CLARIFY", filters=plan.filters,
                clarification="这个范围是按题意描述的场景或设计任务，不能用旧分类标签保证完整数量或全库排名。请选择按题意查找候选，或明确按现有题型/主题标签统计。",
                clarification_options=["按题意查找候选", "按现有标签统计"])
            name = "list_questions"
            policy["route_decision"] = "semantic_complete_scope_requires_clarification"
        elif len(text) > 500:
            plan = QuerySpec(action="CLARIFY", filters=plan.filters,
                clarification="请把本次要找的场景或设计任务概括到 500 字以内，并保留必要条件。")
            name = "list_questions"
            policy["route_decision"] = "semantic_query_too_long"
        else:
            plan = plan.model_copy(update={"action": "SEARCH", "semantic_task": None,
                "search_query": text, "relevance_query": text, "top_n": None,
                "page_size": min(plan.page_size, plan.top_n or 50, 50), "group_by": "question"})
            name = "search_questions"
            policy["route_decision"] = "natural_task_requires_semantic_search"
    if plan.action == "SEARCH":
        policy["user_messages"] = semantic_messages(messages)
        policy["relevance"] = {"planned": plan.relevance_query,
                               "applied": relevance_intent(messages), "source": "user_messages"}
        # Long original requests remain complete in the execution audit. The
        # optional plan field must still satisfy its 500-character wire schema.
        applied = policy["relevance"]["applied"]
        plan = plan.model_copy(update={"relevance_query": applied if len(applied) <= 500 else None})
    policy["applied_action"] = plan.action
    return name, plan, policy
