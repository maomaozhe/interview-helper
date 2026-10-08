"""Fast closed-set Jev decisions. Unsupported or uncertain plans fall back to Pi."""
from __future__ import annotations

from datetime import date, timedelta
import math
import hashlib
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
    version = "jev_query_v5_state_bound_paging"

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
            "response_form": choice("Choose the requested answer form, independent of task topic. 手撕代码 or 手撕算法 explicitly means CODE. 算法题 or 工程实现类题 alone does not filter response form. 手写SQL means SQL.", {**base, "CODE": "Handwritten implementation code", "SQL": "Write SQL", "VERBAL": "Verbal explanation"}),
            "coding_focus": choice("Choose task focus. 手撕代码 without 算法, 工程实现类题 and 手写SQL mean ENGINEERING. 手撕算法/算法题/力扣 means ALGORITHM. Do not mix engineering implementation with algorithm puzzles.", {
                **base, "ALGORITHM": "Algorithm/data structure problem solving", "ENGINEERING": "Implement engineering code such as thread pool/singleton/LRU/concurrency", "MIXED": "Both are explicitly combined"}),
            "sort": choice("Choose final sorting for database rows; inherit sort only for a follow-up.", {"frequency": "Actual occurrence frequency/highest frequency", "importance": "Importance", "gap": "Review weakness/gap", "INHERIT": "Keep previous sort"}),
            "group_by": choice("Choose aggregation unit; question is the default for a list of questions.", {"question": "Question list", "company": "Compare companies", "topic": "Compare technical topics", "round": "Compare rounds"}),
            "top_n": choice("Select the candidate span that specifies the TOTAL number of requested ranked questions (前40个). Never choose year, LeetCode problem number, month count or page number. NONE means no requested Top N; INHERIT for follow-up keeping the old Top N.", {
                **base, **{q["id"]: {"span": q["text"], "start": q["start"], "end": q["end"]} for q in quantities}}),
            "time": choice("Choose the final date window. Use NONE if unrestricted; INHERIT only for follow-up. Specific dates/years or other windows require FALLBACK.", {
                **base, "RECENT_3_MONTHS": "Explicitly 最近三个月/近3个月 (three calendar months ending tomorrow)",
                "FALLBACK": "Any other date arithmetic or specific date/year interval"}),
        }
        if self.settings.jev_provider in {"laya", "local_qwen"}:
            # mmBERT's 256-token question head cannot represent the complete
            # company catalog. Defer new entities instead of losing options.
            questions["company"] = choice("Choose company scope. A new explicitly named company requires FALLBACK for generative entity resolution.", {
                **base, "FALLBACK": "A new company or company alias is explicitly requested."})
        # A small decision encoder does not need UUID pages or complete prior tool
        # output. It receives the structured scope and current language turn.
        state = {k: context[k] for k in ("message", "today", "explicit_filters", "default_page_size")}
        state["session"] = {k: context.get("session", {}).get(k) for k in ("filters", "sort", "last_plan", "recent_messages")}
        result={"state":state,"model":self.settings.jev_model,"questions":questions}
        if self.settings.jev_provider=="local_qwen":
            old=state["session"].get("filters") or {}
            for key in ("company","topic_l1","round","response_form","coding_focus"):
                if old.get(key) is None: questions[key]["criteria"].pop("INHERIT",None)
                elif old[key] in questions[key]["criteria"]: questions[key]["criteria"].pop(old[key])
            if not (state["session"].get("last_plan") or {}).get("top_n"):
                questions["top_n"]["criteria"].pop("INHERIT",None)
            if not old.get("start_date") and not old.get("end_date"):
                questions["time"]["criteria"].pop("INHERIT",None)
            if (state["session"].get("sort") or "frequency") in questions["sort"]["criteria"]:
                questions["sort"]["criteria"].pop("INHERIT",None)
            last=state["session"].get("last_plan") or {}
            state["session"]["last_plan"]={key:last.get(key) for key in ("action","top_n","page_size")}
            state["session"].pop("recent_messages",None)
            result["protocol"]="typed_json_v1"
        return result, quantities

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
                    parsed = response.json()
                    if not isinstance(parsed, dict):
                        raise ValueError("INVALID_DECISION_RESPONSE")
                    data = parsed
                    response.raise_for_status()
            values, confidences, raw_values = self.decision_values(payload,data,context,run.state,quantities)
            effective = (["action"] if values["action"]=="NEXT" else
                [key for key in confidences if key!="group_by" or values["action"]=="STATS"])
            effective = [key for key in effective if key not in context["explicit_filters"]]
            minimum = min(confidences[key] for key in effective)
            run.decision = {"provider": self.settings.jev_provider, "mode": self.settings.jev_decision_mode,
                            "model": data.get("model"), "revision": data.get("model_revision"),
                            "choices": values, "raw_choices":raw_values, "confidences": confidences,
                            "minimum_confidence": minimum, "provider_timings":data.get("timings",{}),
                            "confidence_semantics":data.get("confidence_semantics"),"provider_protocol":data.get("protocol"),
                            "service_sha256":data.get("service_sha256"),"grammar_sha256":data.get("grammar_sha256")}
            options = data.get("usage", {}).get("options", {})
            lost_options = not isinstance(options, dict) or any(
                not isinstance(v, dict) or v.get("distinct") != v.get("total") for v in options.values())
            if data.get("usage", {}).get("truncated") or lost_options:
                run.decision["fallback_reason"] = "input_truncated"
                return None
            if minimum < self.settings.jev_confidence_threshold:
                run.decision["fallback_reason"] = "low_confidence"
                return None
            if self.settings.jev_provider in {"laya", "local_qwen"} and not self._calibrated(data):
                run.decision["fallback_reason"] = "domain_calibration_required"
                return None
            return self.decode(values, quantities, context, run.state)
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as failure:
            error = failure
            run.decision = {"provider": self.settings.jev_provider, "mode": self.settings.jev_decision_mode,
                            "fallback_reason": type(failure).__name__, "provider_error_code":data.get("error"),
                            "raw_answer":data.get("raw_answer"), "provider_protocol":data.get("protocol"),
                            "service_sha256":data.get("service_sha256"),"grammar_sha256":data.get("grammar_sha256")}
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

    def decision_values(self,payload,data,context,state,quantities):
        """Combine probability only for options decoding to the same typed value.

        For an empty prior scope NONE and INHERIT both mean no filter. This is
        a finite option probability, never an empirical correctness estimate.
        """
        values,confidences,raw = {},{},{}
        old=state.get("filters",{}); counts={q["id"]:q["value"] for q in quantities}
        def decoded(key,selected):
            if key in {"company","topic_l1","round","response_form","coding_focus"}:
                return old.get(key) if selected=="INHERIT" else None if selected=="NONE" else selected
            if key=="sort": return state.get("sort","frequency") if selected=="INHERIT" else selected
            if key=="top_n": return state.get("last_plan",{}).get("top_n") if selected=="INHERIT" else None if selected=="NONE" else counts[selected]
            if key=="time" and selected=="INHERIT":
                previous=tuple(old.get(k) for k in ("start_date","end_date"))
                return "NONE" if previous==(None,None) else previous
            return selected
        for key,question in payload["questions"].items():
            answer=data["answers"][key]; chosen=answer["choice"]; confidence=answer["confidence"]
            if answer["type"]!="choice" or chosen not in question["criteria"] or type(confidence) not in (int,float) or not math.isfinite(confidence) or not 0<=confidence<=1:
                raise ValueError("INVALID_DECISION")
            raw[key]=chosen; values[key]=chosen; confidences[key]=confidence
            probabilities=answer.get("probabilities")
            if self.settings.jev_provider!="local_qwen" or probabilities is None: continue
            if (not isinstance(probabilities,dict) or set(probabilities)!=set(question["criteria"])
                or any(type(p) not in (int,float) or not math.isfinite(p) or not 0<=p<=1 for p in probabilities.values())
                or abs(sum(probabilities.values())-1)>1e-5 or abs(probabilities[chosen]-confidence)>1e-5):
                raise ValueError("INVALID_DECISION_PROBABILITIES")
            grouped={}; representatives={}
            for selected in question["criteria"]:
                value=decoded(key,selected)
                grouped[value]=grouped.get(value,0)+probabilities[selected]
                representatives.setdefault(value,selected)
            best=max(grouped,key=grouped.get)
            values[key]=representatives[best]; confidences[key]=min(1.0,grouped[best])
        return values,confidences,raw

    def _calibrated(self, data):
        # A service cannot authorize its own confidence. Host-side calibration
        # must match the exact deployed model and decision contract.
        from pathlib import Path
        path = self.settings.jev_calibration_path
        if path is None: return False
        try:
            artifact = json.loads(Path(path).read_text(encoding="utf-8"))
            return (artifact.get("schema") == "jev_domain_calibration_v1"
                and artifact.get("status") == "PASSED"
                and artifact.get("provider") == self.settings.jev_provider
                and artifact.get("model_revision") == data.get("model_revision")
                and artifact.get("decision_version") == self.version
                and artifact.get("decision_contract_sha256") == hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
                and artifact.get("provider_protocol") == data.get("protocol")
                and (self.settings.jev_provider != "local_qwen" or (
                    data.get("protocol") == "typed_json_v1"
                    and artifact.get("service_sha256") == data.get("service_sha256")
                    and artifact.get("grammar_sha256") == data.get("grammar_sha256")
                    and all(isinstance(artifact.get(key),str) and len(artifact[key])==64
                            for key in ("service_sha256","grammar_sha256"))))
                and artifact.get("independent_calibration") is True
                and type(artifact.get("threshold")) in (int,float)
                and math.isfinite(artifact["threshold"])
                and 0 <= artifact["threshold"] <= self.settings.jev_confidence_threshold <= 1
                and type(artifact.get("accepted_samples")) is int and artifact["accepted_samples"] >= 20
                and type(artifact.get("accepted_errors")) is int and artifact["accepted_errors"] == 0)
        except (OSError, ValueError, TypeError):
            return False

    def decode(self, values, quantities, context, state):
        """Decode finite decisions without a network call or acceptance gate."""
        if values["action"] == "NEXT":
            return QuerySpec(action="NEXT")
        if "FALLBACK" in (values["action"], values["time"], values["company"]):
            return None
        old = state.get("filters", {})
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
        limit = (state.get("last_plan", {}).get("top_n") if count == "INHERIT" else None if count == "NONE" else
                 next(q["value"] for q in quantities if q["id"] == count))
        if limit is not None and not 1 <= limit <= 1000:
            return None
        sort = values["sort"] if values["sort"] != "INHERIT" else state.get("sort", "frequency")
        continuation = count == "INHERIT" or any(values[key] == "INHERIT"
            for key in ("company", "topic_l1", "round", "response_form", "coding_focus", "time"))
        saved_page = (state.get("last_plan") or {}).get("page_size") if continuation else None
        page_size = saved_page if saved_page is not None else min(100, limit or context["default_page_size"])
        return QuerySpec(action=values["action"], filters=FilterSpec(**fields), top_n=limit,
                         page_size=page_size, sort=sort,
                         group_by=values["group_by"] if values["action"]=="STATS" else "question")
