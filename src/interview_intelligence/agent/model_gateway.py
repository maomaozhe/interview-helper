"""Private model gateway: Pi never receives upstream credentials or bypasses the gate."""
from __future__ import annotations

import asyncio
import json
import time

import httpx

from interview_intelligence.agent.query_contract import QuerySpec, QUERY_AGENT_VERSION, query_model_schema, validate_model_plan
from interview_intelligence.providers.runtime import current_limits
from interview_intelligence.resources import resource_path


class ModelGateway:
    def __init__(self, settings, gate, on_call=None, on_event=None):
        self.settings, self.gate, self.on_call = settings, gate, on_call
        self.on_event=on_event
        self.prompt_version = getattr(settings, "query_prompt_version", QUERY_AGENT_VERSION)

    async def complete(self, run, payload, emit=None):
        settings = getattr(run, "provider_settings", None) or self.settings
        if not settings.model_api_key or not settings.model_base_url:
            raise ValueError("MODEL_CONFIGURATION_INCOMPLETE")
        run.limits.check()
        if run.model_calls >= self.settings.query_max_model_calls:
            raise ValueError("QUERY_MODEL_BUDGET_EXCEEDED")
        run.model_calls += 1
        model = settings.query_model or settings.judge_model
        # Hosts select endpoint/model/budget; clients cannot change them.
        body = {k: v for k, v in payload.items() if k in {
            "messages", "tools", "tool_choice", "response_format", "max_tokens", "max_completion_tokens"}}
        body.update(model=model, stream=False, temperature=0)
        if body.get("tools"):
            # Clarification is a domain action too. A free-text reply cannot
            # establish a checked terminal result or a recoverable state.
            body["tool_choice"] = "required"
        if emit:
            body.update(stream=True,stream_options={"include_usage":True})
        body.setdefault("max_tokens", 4096)
        body["max_tokens"] = min(4096, max(1, int(body["max_tokens"])))
        if "max_completion_tokens" in body:
            body["max_completion_tokens"] = min(4096, max(1, int(body["max_completion_tokens"])))
        if "ark.cn-" in settings.model_base_url:
            body["thinking"] = {"type": "disabled"}
        if len(json.dumps(body, ensure_ascii=False)) > 100_000:
            raise ValueError("QUERY_CONTEXT_TOO_LARGE")
        upper=len(json.dumps(body,ensure_ascii=False).encode("utf-8"))+body["max_tokens"]
        reserved=run.limits.reserve_tokens(upper)
        started, phase, provider_started, response, error = time.perf_counter(), {}, None, {}, None
        ttft=None
        context_token = current_limits.set(run.limits)
        gate_context = self.gate.call()
        entered = False
        try:
            if self.on_event:
                await asyncio.to_thread(self.on_event,run.id,"stage",{
                    "stage":"waiting_for_model", "attempt":run.model_calls})
                await asyncio.to_thread(self.on_event,run.id,"model_request",body)
            acquisition = asyncio.create_task(asyncio.to_thread(gate_context.__enter__))
            try:
                phase = await asyncio.shield(acquisition)
            except asyncio.CancelledError:
                run.limits.cancelled.set()
                try:
                    await acquisition
                except ValueError:
                    pass
                else:
                    await asyncio.to_thread(gate_context.__exit__, None, None, None)
                raise
            entered = True
            if self.on_event:
                await asyncio.to_thread(self.on_event,run.id,"stage",{
                    "stage":"planning", "attempt":run.model_calls})
            # The yielded dictionary is shared and holds measured queue/interval time.
            # Enter is performed only once so the file lock spans this actual HTTP call.
            provider_started = time.perf_counter()
            timeout = max(0.01, run.limits.deadline - time.monotonic())
            client_options = {"trust_env": False, "timeout": timeout}
            if getattr(run, "provider_source", None) == "personal":
                from interview_intelligence.providers.tenant_transport import public_async_transport
                client_options["transport"] = public_async_transport()
            async with httpx.AsyncClient(**client_options) as client:
                url=settings.model_base_url.rstrip("/")+"/chat/completions"
                headers={"Authorization":f"Bearer {settings.model_api_key}"}
                if emit:
                    message={"role":"assistant","content":""}
                    calls={}
                    finished,done=False,False
                    async with client.stream("POST",url,headers=headers,json=body) as upstream:
                        upstream.raise_for_status()
                        async for line in upstream.aiter_lines():
                            run.limits.check()
                            if not line.startswith("data:"): continue
                            raw=line[5:].strip()
                            if raw=="[DONE]":
                                done=True; break
                            chunk=json.loads(raw)
                            if chunk.get("error"): raise ValueError("MODEL_PROVIDER_UNAVAILABLE")
                            if chunk.get("usage"): response["usage"]=chunk["usage"]
                            for key in ("id","model","created"):
                                if key in chunk: response[key]=chunk[key]
                            for choice in chunk.get("choices",[]):
                                delta=choice.get("delta",{})
                                if delta.get("content") or delta.get("tool_calls"):
                                    if ttft is None: ttft=int((time.perf_counter()-provider_started)*1000)
                                message["content"]+=delta.get("content") or ""
                                for call in delta.get("tool_calls",[]):
                                    c=calls.setdefault(call["index"],{"id":"","type":"function","function":{"name":"","arguments":""}})
                                    if call.get("id"): c["id"]=call["id"]
                                    fn=call.get("function",{})
                                    c["function"]["name"]+=fn.get("name") or ""
                                    c["function"]["arguments"]+=fn.get("arguments") or ""
                                if choice.get("finish_reason"):
                                    finished=True;response["finish_reason"]=choice["finish_reason"]
                                    choice["finish_reason"]=None
                            # Only complete, checked streams receive a terminal chunk.
                            await emit(chunk)
                    if not done or not finished: raise ValueError("STREAM_INCOMPLETE")
                    if calls: message["tool_calls"]=[calls[i] for i in sorted(calls)]
                    response["choices"]=[{"index":0,"message":message,"finish_reason":response.pop("finish_reason")}]
                else:
                    result=await client.post(url,headers=headers,json=body)
                    result.raise_for_status()
                    response=result.json()
            run.limits.check()
            if not response.get("choices") or response["choices"][0].get("finish_reason") == "length":
                raise ValueError("MODEL_OUTPUT_TRUNCATED")
            return response
        except BaseException as failure:
            error = failure
            if getattr(run, "provider_source", None) == "personal" and isinstance(failure, (httpx.HTTPError, ValueError)):
                # Client transport failures can include URLs or header values.
                # Persist/return a fixed code, while model calls record only type.
                safe_codes = {"QUERY_MODEL_BUDGET_EXCEEDED", "QUERY_TOKEN_BUDGET_EXCEEDED", "QUERY_CANCELLED",
                              "QUERY_DEADLINE_EXCEEDED", "STREAM_INCOMPLETE", "MODEL_OUTPUT_TRUNCATED"}
                if str(failure) not in safe_codes:
                    raise ValueError("MODEL_PROVIDER_UNAVAILABLE") from None
            raise
        finally:
            if entered:
                await asyncio.to_thread(gate_context.__exit__, None, None, None)
            current_limits.reset(context_token)
            measured = {**phase, "provider_ms": int((time.perf_counter() - provider_started) * 1000) if provider_started else 0}
            usage=response.get("usage",{})
            actual=usage.get("total_tokens")
            if type(actual) is not int: actual=None
            if actual is None and type(usage.get("prompt_tokens")) is int and type(usage.get("completion_tokens")) is int:
                actual=usage["prompt_tokens"]+usage["completion_tokens"]
            budget_error=None
            try: run.limits.settle_tokens(reserved,actual if provider_started else 0)
            except ValueError as failure:
                budget_error=failure
                error=error or failure
            for key, value in measured.items():
                run.call_timings[key] = run.call_timings.get(key, 0) + value
            if self.on_call:
                usage = response.get("usage", {})
                await asyncio.to_thread(self.on_call, {"request_id": run.request.request_id,
                    "query_run_id":run.id,"token_budget_charge":actual if actual is not None else reserved if provider_started else 0,
                    "operation_type": "QUERY_PLAN", "model": model, "model_revision": response.get("model"),
                    "prompt_version": self.prompt_version, "input_tokens": usage.get("prompt_tokens"),
                    "output_tokens": usage.get("completion_tokens"), "latency_ms": int((time.perf_counter() - started) * 1000),
                    **measured, "ttft_ms":ttft,
                    "status": "FAILED" if error else "SUCCEEDED", "retry_count": 0,
                    "error_code": type(error).__name__ if error else None})
            if budget_error: raise budget_error

    async def plan(self, run, context):
        from interview_intelligence.agent.presentation import AnswerStream
        answers = AnswerStream(content_json=True)
        async def emit(chunk):
            for answer in answers.update(chunk):
                await asyncio.to_thread(self.on_event,run.id,"answer_delta",{
                    **answer,"run_id":run.id,"model_call":run.model_calls})
        response = await self.complete(run, {"messages": [
            {"role": "system", "content": resource_path(f"prompts/{self.prompt_version}.md").read_text(encoding="utf-8") +
             "\n当前模式直接输出 QuerySpec JSON，不调用工具。"},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)}],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "QuerySpec", "strict": False, "schema": query_model_schema(context.get("tool_policy"))}}},
            emit=emit if self.on_event else None)
        return validate_model_plan(json.loads(response["choices"][0]["message"]["content"]))


def completion_sse(response):
    """Expose an OpenAI SSE shape after a complete, checked upstream response."""
    choice = response["choices"][0]
    base = {"id": response.get("id", "query"), "object": "chat.completion.chunk",
            "created": response.get("created", int(time.time())), "model": response.get("model", "query")}
    message = choice["message"]
    delta = {"role": "assistant", "content": message.get("content")}
    if message.get("tool_calls"):
        delta["tool_calls"] = [{"index": i, **call} for i, call in enumerate(message["tool_calls"])]
    yield "data: " + json.dumps({**base, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}) + "\n\n"
    yield "data: " + json.dumps({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": choice.get("finish_reason", "stop")}],
                                "usage": response.get("usage")}) + "\n\n"
    yield "data: [DONE]\n\n"
