"""Deterministic tool routing and fact-only answer composition.

The router never accepts instructions from retrieved Markdown. Ambiguous writes
become review events awaiting resolution; corpus facts come only from tools.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from interview_intelligence.analytics.detail import get_question_detail
from interview_intelligence.analytics.stats import query_question_stats, subtract_calendar_months
from interview_intelligence.contracts import ReviewItem, ReviewRequest, ReviewStatus, StatsRequest
from interview_intelligence.domain.models import CorpusState, UserRevision
from interview_intelligence.review.service import ReviewService
from interview_intelligence.search.service import search_questions


class AgentService:
    def __init__(self, database, retriever=None, *, user_id: str = "local"):
        self.database = database
        self.retriever = retriever
        self.user_id = user_id
        self.reviews = ReviewService(database)

    def _filters(self, message: str, *, group_by="question", sort="frequency", limit=20):
        company = next((name for name in ("字节", "腾讯", "阿里", "百度", "美团", "拼多多", "得物", "小红书")
                        if name in message), None)
        round_name = next((value for text, value in (("一面", "FIRST"), ("二面", "SECOND"),
                          ("三面", "THIRD"), ("四面", "FOURTH_PLUS")) if text in message), None)
        topic = next((name for name in ("Redis", "Spring", "数据库", "算法", "AI")
                      if name.casefold() in message.casefold()), None)
        if topic is None and "Java知识点" in message:
            topic = "Java"
        if "MySQL" in message or "索引" in message and topic is None:
            topic = "数据库"
        if "手撕" in message or "算法题" in message:
            topic = None
        recent = "最近" in message or "近期" in message
        start = subtract_calendar_months(date.today(), 3) if recent else None
        end = date.today() + timedelta(days=1) if recent else None
        position = "Java后端" if "Java" in message and ("后端" in message or "服务端" in message) else None
        return StatsRequest(company=company, round=round_name, topic_l1=topic,
                            position=position, start_date=start, end_date=end,
                            group_by=group_by, sort=sort, limit=limit,
                            question_type="ALGORITHM" if "手撕" in message or "算法题" in message else None)

    def chat(self, message: str, *, request_id: str | None = None) -> dict:
        if not message.strip() or len(message) > 2000:
            raise ValueError("invalid message length")
        trace = []
        if (message.strip().startswith("记录") or any(word in message for word in
            ("帮我记录", "记下", "标记为", "更新状态"))):
            return self._record(message, request_id, trace)
        if any(word in message for word in ("冲刺", "清单", "薄弱项", "复习缺口")):
            return self._composite(message, trace)
        if any(word in message for word in ("类似", "相似", "问法", "检索")):
            return self._search(message, trace)
        if "详情" in message or "原文来源" in message:
            from interview_intelligence.domain.models import CanonicalQuestion
            from sqlalchemy import select
            uuid = re.search(r"\b[0-9a-f]{8}-[0-9a-f-]{27,}\b", message, re.I)
            with self.database.session() as session:
                if uuid:
                    ids = [uuid.group(0)]
                else:
                    normalized_message = re.sub(r"[\W_]+", "", message).casefold()
                    ids = [item.id for item in session.scalars(select(CanonicalQuestion).where(
                        CanonicalQuestion.lifecycle == "ACTIVE"))
                           if re.sub(r"[\W_]+", "", item.canonical_text).casefold() in normalized_message]
                if len(ids) != 1:
                    return {"intent": "DETAIL", "tool_trace": trace,
                            "facts": {"candidate_ids": ids[:10]},
                            "answer": "请指定唯一的标准题或提供题目 ID。", "needs_clarification": True}
                filters = self._filters(message)
                detail = get_question_detail(session, ids[0], filters)
            trace.append({"name": "get_question_detail", "parameters": {
                "canonical_question_id": ids[0], "filters": filters.model_dump(mode="json")}})
            return {"intent": "DETAIL", "tool_trace": trace, "facts": detail,
                    "answer": f"这道题在当前筛选范围出现 {detail['occurrence_count']} 次，来源见引用列表。"}
        if "我的" in message and ("状态" in message or "复习" in message):
            with self.database.session() as session:
                from interview_intelligence.domain.models import CanonicalQuestion
                from sqlalchemy import select
                ids = list(session.scalars(select(CanonicalQuestion.id).where(
                    CanonicalQuestion.lifecycle == "ACTIVE").limit(100)))
            states = self.reviews.get_states(self.user_id, ids)
            trace.append({"name": "get_user_question_state", "parameters": {"limit": 100}})
            return {"intent": "USER_STATE", "tool_trace": trace, "facts": states,
                    "answer": f"共查看 {len(ids)} 道题的复习状态。"}
        if any(word in message for word in ("核心", "长尾", "优先级", "重要性")):
            params = self._filters(message, sort="importance")
            if params.topic_l1 is None:
                return {"intent": "ANALYTICS", "tool_trace": trace, "facts": {},
                        "answer": "请指定要分析的知识主题。", "needs_clarification": True}
            with self.database.session() as session:
                result = query_question_stats(session, params, user_id=self.user_id)
            trace.append({"name": "get_topic_overview", "parameters": params.model_dump(mode="json")})
            return {"intent": "ANALYTICS", "tool_trace": trace,
                    "facts": {"groups": result["data"], **result["meta"]},
                    "answer": f"按当前语料的重要性公式分层，范围内共有 {result['meta']['sample_counts']['occurrences']} 次真实提问。"}
        if "手撕" in message or "算法题" in message:
            params = self._filters(message)
            with self.database.session() as session:
                result = query_question_stats(session, params, user_id=self.user_id)
                trace.append({"name": "query_question_stats", "parameters": params.model_dump(mode="json")})
                questions = []
                for row in result["data"][:5]:
                    detail = get_question_detail(session, row["canonical_question_id"], params)
                    trace.append({"name": "get_question_detail", "parameters": {
                        "canonical_question_id": row["canonical_question_id"]}})
                    questions.append({**row, "algorithm_matches": detail["algorithm_matches"],
                                      "sources": detail["sources"][:3]})
            return {"intent": "ANALYTICS", "tool_trace": trace,
                    "facts": {"questions": questions, "sample_counts": result["meta"]["sample_counts"]},
                    "answer": (f"找到 {len(questions)} 道范围内算法题；仅原文明确或经核验的题号可作为已识别编号。"
                               if questions else "当前范围没有可核实的算法题。")}
        group_by = "topic" if "知识点" in message or "考点" in message else "question"
        params = self._filters(message, group_by=group_by)
        with self.database.session() as session:
            result = query_question_stats(session, params, user_id=self.user_id)
        trace.append({"name": "query_question_stats", "parameters": params.model_dump(mode="json")})
        count = result["meta"]["sample_counts"]["occurrences"]
        answer = f"当前筛选范围有 {count} 次真实提问；以下为出现频率最高的结果。" if count else "当前筛选范围没有可核实的真实提问。"
        return {"intent": "ANALYTICS", "tool_trace": trace,
                "facts": {"groups": result["data"], **result["meta"]}, "answer": answer}

    def _composite(self, message: str, trace: list) -> dict:
        params = self._filters(message, sort="gap", limit=5)
        with self.database.session() as session:
            initial_revision = session.get(CorpusState, 1).current_revision
            stats = query_question_stats(session, params, user_id=self.user_id)
            ids = [row["canonical_question_id"] for row in stats["data"]]
            trace.append({"name": "query_question_stats", "parameters": params.model_dump(mode="json")})
            states = self.reviews.get_states(self.user_id, ids)
            trace.append({"name": "get_user_question_state", "parameters": {"canonical_question_ids": ids}})
            questions = []
            for row in stats["data"]:
                detail = get_question_detail(session, row["canonical_question_id"], params)
                trace.append({"name": "get_question_detail", "parameters": {
                    "canonical_question_id": row["canonical_question_id"]}})
                questions.append({**row, "status": states["states"][row["canonical_question_id"]]["status"],
                                  "sources": detail["sources"][:3],
                                  "observed_followups": detail["observed_followups"]})
        with self.database.session() as latest:
            corpus = latest.get(CorpusState, 1)
            user = latest.get(UserRevision, self.user_id)
            if (corpus.current_revision != initial_revision or
                    (user.state_revision if user else 0) != states["user_state_revision"]):
                raise ValueError("SNAPSHOT_CHANGED")
        answer = ("建议按复习缺口依次准备：" + "；".join(
            f"{row['canonical_text']}（{row['occurrence_count']} 次，{row['status']}）" for row in questions)
                  if questions else "当前目标范围没有足够的真实提问，暂不能给出有依据的清单。")
        return {"intent": "COMPOSITE", "tool_trace": trace,
                "facts": {"questions": questions, "sample_counts": stats["meta"]["sample_counts"],
                          "corpus_revision": initial_revision,
                          "user_state_revision": states["user_state_revision"]},
                "answer": answer}

    def _search(self, message: str, trace: list) -> dict:
        if self.retriever is None:
            raise ValueError("INDEX_NOT_READY")
        params = self._filters(message)
        with self.database.session() as session:
            state = session.get(CorpusState, 1)
            if state.current_revision == 0 or state.current_revision != state.indexed_revision:
                raise ValueError("INDEX_NOT_READY")
            result = search_questions(session, self.retriever, message, params, top_k=10)
            trace.append({"name": "search_questions", "parameters": {"query": message,
                          "filters": params.model_dump(mode="json")}})
            for item in result["data"][:5]:
                detail = get_question_detail(session, item["canonical_question_id"], params)
                item["observed_followups"] = detail["observed_followups"]
                item["inferred_relations"] = detail["inferred_relations"]
                trace.append({"name": "get_question_detail", "parameters": {
                    "canonical_question_id": item["canonical_question_id"]}})
        return {"intent": "SEARCH", "tool_trace": trace, "facts": result,
                "answer": ("找到可核实的类似问法，详情及来源见结果。" if result["data"]
                           else "没有找到符合条件的类似问法。")}

    def _record(self, message: str, request_id: str | None, trace: list) -> dict:
        mapping = (("没答出", ReviewStatus.WEAK), ("答不好", ReviewStatus.WEAK),
                   ("薄弱", ReviewStatus.WEAK), ("掌握", ReviewStatus.MASTERED),
                   ("复习过", ReviewStatus.REVIEWED), ("答得不错", ReviewStatus.REVIEWED))
        clauses = [part.strip() for part in re.split(r"[,，;；。]", message) if part.strip()]
        from interview_intelligence.domain.models import CanonicalQuestion
        from sqlalchemy import select

        def normalized(value):
            return re.sub(r"[\W_]+", "", value).casefold()

        items = []
        with self.database.session() as session:
            canonicals = list(session.scalars(select(CanonicalQuestion).where(
                CanonicalQuestion.lifecycle == "ACTIVE")))
        for clause in clauses:
            status = next((value for phrase, value in mapping if phrase in clause), None)
            if status is None:
                return {"intent": "USER_STATE", "tool_trace": trace, "facts": {},
                        "answer": "请为每道题说明薄弱、已复习或已掌握，并指明题目。",
                        "needs_clarification": True}
            raw = clause
            for phrase, _ in mapping:
                raw = raw.replace(phrase, "")
            raw = re.sub(r"^(?:请|帮我|记录|一下|把|将|这题)+", "", raw).strip()
            canonical_id = re.search(r"\b[0-9a-f]{8}-[0-9a-f-]{27,}\b", raw, re.I)
            if canonical_id:
                items.append(ReviewItem(canonical_question_id=canonical_id.group(0), status=status))
                continue
            query = normalized(raw)
            matches = [canonical for canonical in canonicals
                       if len(query) >= 3 and query in normalized(canonical.canonical_text)]
            if len(matches) > 1:
                return {"intent": "USER_STATE", "tool_trace": trace,
                        "facts": {"candidates": [{"canonical_question_id": candidate.id,
                                                 "canonical_text": candidate.canonical_text}
                                                for candidate in matches[:10]]},
                        "answer": f"“{raw}”可对应多道题，请选择具体题目后再记录；本批次尚未写入。",
                        "needs_clarification": True}
            items.append(ReviewItem(canonical_question_id=matches[0].id, status=status)
                         if matches else ReviewItem(raw_question=raw or clause, status=status))
        request = ReviewRequest(idempotency_key=request_id or message, items=items)
        result = self.reviews.record(self.user_id, request)
        trace.append({"name": "record_review", "parameters": {
            "items": [{"status": item.status.value, "canonical_question_id": item.canonical_question_id,
                       "raw_question": item.raw_question} for item in items]}})
        unresolved = any(item["resolution_status"] == "UNRESOLVED" for item in result["items"])
        return {"intent": "USER_STATE", "tool_trace": trace, "facts": result,
                "answer": ("已保存待匹配复习记录；已明确定位的题目已记录。"
                           if unresolved else f"已记录 {len(items)} 道题的复习状态。")}
