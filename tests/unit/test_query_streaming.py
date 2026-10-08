import asyncio
import json
import time
from types import SimpleNamespace
import httpx
import pytest
from interview_intelligence.agent.model_gateway import ModelGateway
from interview_intelligence.providers.gate import ModelCallGate
from interview_intelligence.providers.runtime import RequestLimits

class Stream(httpx.AsyncByteStream):
    def __init__(self,frames): self.frames=frames
    async def __aiter__(self):
        for frame in self.frames:
            await asyncio.sleep(.002)
            yield ("data: "+(frame if isinstance(frame,str) else json.dumps(frame))+"\n\n").encode()

@pytest.mark.parametrize("terminal",["complete","incomplete","length"])
def test_gateway_streams_real_deltas_and_only_accepts_checked_completion(monkeypatch,tmp_path,terminal):
    async def scenario():
        frames=[{"id":"stream-1","choices":[{"index":0,"delta":{"tool_calls":[
            {"index":0,"id":"call-1","function":{"name":"list_questions","arguments":"{\"action\":"}}]},"finish_reason":None}]}]
        frames.append({"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\"LIST\"}"}}]},"finish_reason":None}]})
        frames.append({"choices":[{"index":0,"delta":{},"finish_reason":"length" if terminal=="length" else "tool_calls"}],
                       "usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}})
        if terminal!="incomplete": frames.append("[DONE]")
        original=httpx.AsyncClient
        def handle(request):
            body=json.loads(request.content)
            assert body["tool_choice"]=="required"
            return httpx.Response(200,stream=Stream(frames))
        monkeypatch.setattr(httpx,"AsyncClient",lambda **kwargs:original(transport=httpx.MockTransport(handle),**kwargs))
        settings=SimpleNamespace(model_api_key="test",model_base_url="https://fixture.invalid/v1",
                                 query_model="test",judge_model="test",query_max_model_calls=3)
        audit=[]
        run=SimpleNamespace(id="run",request=SimpleNamespace(request_id="request"),
            limits=RequestLimits(time.monotonic()+5),model_calls=0,call_timings={})
        gateway=ModelGateway(settings,ModelCallGate(tmp_path/"lock",minimum_interval_seconds=0),audit.append)
        emitted=[]
        async def emit(chunk):
            assert not audit, "Deltas must arrive before provider completion is logged"
            emitted.append(chunk)
        if terminal=="complete":
            result=await gateway.complete(run,{"messages":[],"tools":[{"type":"function","function":{"name":"list_questions"}}]},emit)
            assert result["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]=='{"action":"LIST"}'
        else:
            with pytest.raises(ValueError,match="STREAM_INCOMPLETE" if terminal=="incomplete" else "MODEL_OUTPUT_TRUNCATED"):
                await gateway.complete(run,{"messages":[],"tools":[{"type":"function","function":{"name":"list_questions"}}]},emit)
        assert emitted and all(c.get("finish_reason") is None for e in emitted for c in e.get("choices",[]))
        assert audit[0]["ttft_ms"] is not None and audit[0]["query_run_id"]=="run"
        assert run.limits.tokens==15
        with gateway.gate.call(): pass  # Completion and failure both release the shared gate.
    asyncio.run(scenario())

def test_token_budget_rejects_before_entering_provider(tmp_path):
    settings=SimpleNamespace(model_api_key="test",model_base_url="https://fixture.invalid/v1",
                             query_model="test",judge_model="test",query_max_model_calls=3)
    run=SimpleNamespace(limits=RequestLimits(time.monotonic()+5,max_tokens=20),model_calls=0)
    gateway=ModelGateway(settings,ModelCallGate(tmp_path/"lock",minimum_interval_seconds=0))
    with pytest.raises(ValueError,match="QUERY_TOKEN_BUDGET_EXCEEDED"):
        asyncio.run(gateway.complete(run,{"messages":[]}))
