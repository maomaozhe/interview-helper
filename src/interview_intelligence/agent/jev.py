"""Fast closed-set Jev decisions. Unsupported or uncertain plans fall back to Pi."""
from __future__ import annotations

from datetime import date, timedelta
import math
import json
import re
import time

import httpx
from sqlalchemy import select

from interview_intelligence.agent.query_contract import QuerySpec
from interview_intelligence.analytics.stats import subtract_calendar_months
from interview_intelligence.contracts import FilterSpec
from interview_intelligence.domain.models import Interview
from interview_intelligence.taxonomy import load_taxonomy


_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}


def parse_chinese_number(text: str) -> int:
    if text.isdecimal():
        return int(text)
    value, digit = 0, 0
    for char in text:
        if char in _CN_DIGITS:
            digit = _CN_DIGITS[char]
        elif char in {"十", "百", "千"}:
            value += (digit or 1) * {"十": 10, "百": 100, "千": 1000}[char]
            digit = 0
        else:
            raise ValueError("invalid numeral")
    return value + digit


def quantity_candidates(message):
    """Over-generate literal spans; the model selects their semantic role."""
    return [{"id": f"n{i}", "text": m.group(), "start": m.start(), "end": m.end(),
             "value": parse_chinese_number(m.group())}
            for i, m in enumerate(re.finditer(r"\d+|[零一二两三四五六七八九十百千]+", message))][:32]


def choice(instructions, options):
    return {"type": "choice", "instructions": instructions, "criteria": options}


class JevPlanner:
    version = "jev_query_v1"

    def __init__(self, settings, database, gate, on_call=None):
        self.settings, self.database, self.gate, self.on_call = settings, database, gate, on_call

    def payload(self, context):
        quantities = quantity_candidates(context["message"])
        with self.database.session() as session:
            companies = list(session.scalars(select(Interview.company_normalized).where(
                Interview.company_normalized.is_not(None)).distinct().limit(180)))
        base = {"NONE": "No filter for this field in the requested final scope.",
                "INHERIT": "The user continues the prior query and keeps the prior value of this field."}
        questions = {
            "action": choice("Route the user's current request. Use FALLBACK for semantic search, detail, writes, fine topics, specific dates, positions, multiple steps, unclear scope or unsupported parameters.", {
                "LIST": "Complete database category list or global frequency/importance/gap Top N.",
                "STATS": "Counts grouped by company, topic or interview round.",
                "NEXT": "Next page of the previously saved database list.",
                "FALLBACK": "Requires generative reasoning or clarification."}),
            "company": choice("Choose the final company filter. Chinese company aliases refer to the listed normalized company.", {**base, **{c: c for c in companies}}),
            "topic_l1": choice("Choose the explicitly requested broad technical domain. Keep NONE for algorithm/coding task focus alone. Fine subtopics require FALLBACK.", {**base, **{t: t for t in load_taxonomy().topics}}),
            "round": choice("Choose the final interview round filter.", {**base, "FIRST": "一面", "SECOND": "二面", "THIRD": "三面", "FOURTH_PLUS": "四面及以后", "HR": "HR面", "OTHER": "其他轮次"}),
            "response_form": choice("Choose the requested answer form, independent of task topic. 手撕代码 means CODE. 算法题 alone need not filter response form.", {**base, "CODE": "Handwritten implementation code", "SQL": "Write SQL", "VERBAL": "Verbal explanation"}),
            "coding_focus": choice("Choose task focus. 手撕代码 without 算法 usually means ENGINEERING. 手撕算法/算法题/力扣 means ALGORITHM. Do not mix engineering implementation with algorithm puzzles.", {
                **base, "ALGORITHM": "Algorithm/data structure problem solving", "ENGINEERING": "Implement engineering code such as thread pool/singleton/LRU/concurrency", "MIXED": "Both are explicitly combined"}),
            "sort": choice("Choose final sorting for database rows; inherit sort only for a follow-up.", {"frequency": "Actual occurrence frequency/highest frequency", "importance": "Importance", "gap": "Review weakness/gap", "INHERIT": "Keep previous sort"}),
            "group_by": choice("Choose aggregation unit; question is the default for a list of questions.", {"question": "Question list", "company": "Compare companies", "topic": "Compare technical topics", "round": "Compare rounds"}),
            "top_n": choice("Select the candidate span that specifies the TOTAL number of requested ranked questions (前40个). Never choose year, LeetCode problem number, month count or page number. NONE means no requested Top N; INHERIT for follow-up keeping the old Top N.", {
                **base, **{q["id"]: {"span": q["text"], "start": q["start"], "end": q["end"]} for q in quantities}}),
            "time": choice("Choose the final date window. Use NONE if unrestricted; INHERIT only for follow-up. Specific dates/years or other windows require FALLBACK.", {
                **base, "RECENT_3_MONTHS": "Explicitly 最近三个月/近3个月 (three calendar months ending tomorrow)",
                "FALLBACK": "Any other date arithmetic or specific date/year interval"}),
        }
        if self.settings.jev_provider == "laya":
            # mmBERT's 256-token question head cannot represent the complete
            # company catalog. Defer new entities instead of losing options.
            questions["company"] = choice("Choose company scope. A new explicitly named company requires FALLBACK for generative entity resolution.", {
                **base, "FALLBACK": "A new company or company alias is explicitly requested."})
        # A small decision encoder does not need UUID pages or complete prior tool
        # output. It receives the structured scope and current language turn.
        state = {k: context[k] for k in ("message", "today", "explicit_filters", "default_page_size")}
        state["session"] = {k: context.get("session", {}).get(k) for k in ("filters", "sort", "last_plan", "recent_messages")}
        return {"state": state, "model": self.settings.jev_model, "questions": questions}, quantities

    def plan(self, run, context):
        payload, quantities = self.payload(context)
        started, provider_started, data, error, phase = time.perf_counter(), None, {}, None, {}
        reserved=run.limits.reserve_tokens(len(json.dumps(payload,ensure_ascii=False).encode("utf-8"))+256)
        try:
            with self.gate.call() as phase:
                provider_started = time.perf_counter()
                timeout = min(self.settings.jev_timeout_seconds, max(0.01, run.limits.deadline - time.monotonic()))
                with httpx.Client(timeout=timeout, trust_env=False) as client:
                    response = client.post(self.settings.jev_base_url.rstrip("/") + "/systemone",
                        headers={"Authorization": f"Bearer {self.settings.jev_api_key}"}, json=payload)
                    response.raise_for_status()
                    data = response.json()
            answers = data["answers"]
            values = {}
            confidences = {}
            for key, question in payload["questions"].items():
                answer = answers[key]
                chosen, confidence = answer["choice"], answer["confidence"]
                if answer["type"] != "choice" or chosen not in question["criteria"] or type(confidence) not in (int, float):
                    return None
                if not math.isfinite(confidence) or not 0 <= confidence <= 1:
                    return None
                values[key] = chosen
                confidences[key] = confidence
            run.decision = {"provider": self.settings.jev_provider, "mode": self.settings.jev_decision_mode,
                            "model": data.get("model"), "revision": data.get("model_revision"),
                            "choices": values, "confidences": confidences,
                            "minimum_confidence": min(confidences.values())}
            options = data.get("usage", {}).get("options", {})
            lost_options = not isinstance(options, dict) or any(
                not isinstance(v, dict) or v.get("distinct") != v.get("total") for v in options.values())
            if data.get("usage", {}).get("truncated") or lost_options:
                run.decision["fallback_reason"] = "input_truncated"
                return None
            if min(confidences.values()) < self.settings.jev_confidence_threshold:
                run.decision["fallback_reason"] = "low_confidence"
                return None
            if self.settings.jev_provider == "laya" and not data.get("calibrated", False):
                run.decision["fallback_reason"] = "domain_calibration_required"
                return None
            if "FALLBACK" in (values["action"], values["time"], values["company"]):
                return None
            if values["action"] == "NEXT":
                return QuerySpec(action="NEXT")
            old = run.state.get("filters", {})
            fields = {}
            for field in ("company", "topic_l1", "round", "response_form", "coding_focus"):
                selected = values[field]
                fields[field] = old.get(field) if selected == "INHERIT" else None if selected == "NONE" else selected
            # Do not discard unsupported filters already present in an inherited scope.
            if any(old.get(f) for f in ("position", "job_family", "language", "topic_l2", "question_type")) and any(v == "INHERIT" for v in values.values()):
                return None
            if values["time"] == "RECENT_3_MONTHS":
                today = date.fromisoformat(context["today"])
                fields.update(start_date=subtract_calendar_months(today, 3), end_date=today + timedelta(days=1))
            elif values["time"] == "INHERIT":
                fields.update({f: old.get(f) for f in ("start_date", "end_date", "date_basis") if old.get(f)})
            fields.update(context["explicit_filters"])
            count = values["top_n"]
            limit = (run.state.get("last_plan", {}).get("top_n") if count == "INHERIT" else None if count == "NONE" else
                     next(q["value"] for q in quantities if q["id"] == count))
            if limit is not None and not 1 <= limit <= 1000:
                return None
            sort = values["sort"] if values["sort"] != "INHERIT" else run.state.get("sort", "frequency")
            return QuerySpec(action=values["action"], filters=FilterSpec(**fields), top_n=limit,
                             page_size=min(100, limit or context["default_page_size"]), sort=sort,
                             group_by=values["group_by"])
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as failure:
            error = failure
            run.decision = {"provider": self.settings.jev_provider, "mode": self.settings.jev_decision_mode,
                            "fallback_reason": type(failure).__name__}
            run.limits.check()
            return None
        finally:
            usage=data.get("usage",{})
            actual=(usage["input_tokens"]+(usage.get("output_tokens") or 0)) if type(usage.get("input_tokens")) is int else None
            run.limits.settle_tokens(reserved,actual if provider_started else 0)
            measured = {**phase, "provider_ms": int((time.perf_counter() - provider_started) * 1000) if provider_started else 0}
            for key, value in measured.items():
                run.call_timings[key] = run.call_timings.get(key, 0) + value
            if self.on_call:
                usage = data.get("usage", {})
                self.on_call({"request_id": run.request.request_id,"query_run_id":run.id,
                    "token_budget_charge":actual if actual is not None else reserved if provider_started else 0,
                    "operation_type": "QUERY_ROUTE", "model": self.settings.jev_model,
                    "model_revision": data.get("model_revision") or data.get("model"), "prompt_version": self.version,
                    "input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens"),
                    "latency_ms": int((time.perf_counter() - started) * 1000), **measured,
                    "status": "FAILED" if error else "SUCCEEDED", "retry_count": 0,
                    "error_code": type(error).__name__ if error else None})
