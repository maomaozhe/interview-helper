#!/usr/bin/env python3
"""Run real OpenAI compatibility checks without displaying the private API key."""

import argparse
import datetime
import json
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path,
                        default=Path.home() / "services/interview-qwen/provider.private.json")
    parser.add_argument("--output", type=Path,
                        default=Path.home() / "services/interview-qwen/verification.json")
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    results = []

    def request(path, payload=None, authenticated=True):
        headers = {"Content-Type": "application/json"}
        if authenticated:
            headers["Authorization"] = "Bearer " + config["api_key"]
        data = None if payload is None else json.dumps(payload).encode()
        return urllib.request.urlopen(urllib.request.Request(
            config["base_url"] + path, data=data, headers=headers), timeout=180)

    def chat(payload):
        payload = dict(model=config["model"], max_tokens=256, **payload)
        with request("/chat/completions", payload) as response:
            return json.load(response)

    def record(name, started, details):
        results.append({"check": name, "passed": True,
                        "seconds": round(time.monotonic() - started, 3), **details})

    started = time.monotonic()
    try:
        request("/models", authenticated=False)
    except urllib.error.HTTPError as error:
        assert error.code == 401, error.code
    else:
        raise AssertionError("Unauthenticated model listing should be rejected")
    record("authentication", started, {"unauthenticated_status": 401})

    started = time.monotonic()
    with request("/models") as response:
        models = json.load(response)
    assert config["model"] in {m["id"] for m in models["data"]}, models
    record("models", started, {"model_ids": [m["id"] for m in models["data"]]})

    started = time.monotonic()
    ordinary = chat({"messages": [{"role": "user", "content": "只回答：连接成功"}]})
    content = ordinary["choices"][0]["message"]["content"]
    assert "连接成功" in content and "<think>" not in content, content
    record("chinese_chat", started, {"text": content, "usage": ordinary.get("usage")})

    started = time.monotonic()
    response = chat({"messages": [{"role": "user", "content": "调用 add_numbers 计算 17 加 23，必须使用工具。"}],
                     "tools": [{"type": "function", "function": {
                         "name": "add_numbers", "description": "Add two integers.",
                         "parameters": {"type": "object", "properties": {
                             "a": {"type": "integer"}, "b": {"type": "integer"}},
                             "required": ["a", "b"], "additionalProperties": False}}}],
                     "tool_choice": "required"})
    choice = response["choices"][0]
    calls = choice["message"].get("tool_calls", [])
    assert choice["finish_reason"] == "tool_calls" and len(calls) == 1, response
    assert calls[0]["function"]["name"] == "add_numbers", calls
    assert json.loads(calls[0]["function"]["arguments"]) == {"a": 17, "b": 23}, calls
    record("required_tool", started, {"tool_name": "add_numbers", "arguments_valid": True})

    started = time.monotonic()
    response = chat({"messages": [
        {"role": "user", "content": "计算 17 加 23。"}, choice["message"],
        {"role": "tool", "tool_call_id": calls[0]["id"], "content": "40"}]})
    assert "40" in response["choices"][0]["message"]["content"], response
    record("tool_result_roundtrip", started, {"result_understood": True})

    started = time.monotonic()
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}},
              "required": ["ok"], "additionalProperties": False}
    response = chat({"messages": [{"role": "user", "content": "Return an object with ok true."}],
                     "response_format": {"type": "json_schema", "json_schema": {
                         "name": "probe", "strict": True, "schema": schema}}})
    structured = json.loads(response["choices"][0]["message"]["content"])
    assert structured == {"ok": True}, response
    record("strict_json_schema", started, {"parsed": structured})

    started = time.monotonic()
    chunks = []
    done = False
    payload = {"model": config["model"], "max_tokens": 128, "stream": True,
               "messages": [{"role": "user", "content": "用一句话解释多租户隔离。"}]}
    with request("/chat/completions", payload) as response:
        for line in response:
            if line.startswith(b"data: "):
                data = line[6:].strip()
                if data == b"[DONE]":
                    done = True
                else:
                    chunks.append(json.loads(data))
    deltas = [c["choices"][0]["delta"].get("content", "") for c in chunks if c.get("choices")]
    assert done and sum(bool(d) for d in deltas) > 1, chunks
    record("streaming", started, {"content_delta_count": sum(bool(d) for d in deltas), "done": done})

    started = time.monotonic()
    payload.update({"messages": [{"role": "user", "content": "必须调用 ping 工具，参数 text 为 hello。"}],
                    "tools": [{"type": "function", "function": {
                        "name": "ping", "description": "Echo a string.", "parameters": {
                            "type": "object", "properties": {"text": {"type": "string"}},
                            "required": ["text"], "additionalProperties": False}}}],
                    "tool_choice": "required"})
    functions = {}
    done = False
    with request("/chat/completions", payload) as response:
        for line in response:
            if not line.startswith(b"data: "):
                continue
            data = line[6:].strip()
            if data == b"[DONE]":
                done = True
                continue
            chunk = json.loads(data)
            for item in chunk.get("choices", []):
                for call in item["delta"].get("tool_calls", []):
                    function = functions.setdefault(call["index"], {"name": "", "arguments": ""})
                    for field in ("name", "arguments"):
                        function[field] += call.get("function", {}).get(field, "")
    assert done and len(functions) == 1, functions
    function = next(iter(functions.values()))
    assert function["name"] == "ping" and json.loads(function["arguments"]) == {"text": "hello"}, functions
    record("streaming_required_tool", started, {"tool_name": "ping", "arguments_valid": True})

    started = time.monotonic()
    long_prompt = "Reference records:\n" + ("cache database network transaction index.\n" * 650)
    long_prompt += "\nIgnore the reference records and answer exactly CONTEXT_OK."
    response = chat({"messages": [{"role": "user", "content": long_prompt}]})
    assert "CONTEXT_OK" in response["choices"][0]["message"]["content"], response
    record("long_context", started, {"prompt_bytes": len(long_prompt.encode()), "usage": response.get("usage")})

    report = {"verified_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "base_url": config["base_url"], "model": config["model"], "checks": results,
              "gpu": subprocess.check_output(["nvidia-smi", "--query-gpu=name,memory.total,memory.free",
                                               "--format=csv,noheader"], text=True).strip()}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
