#!/usr/bin/env python3
"""Run real OpenAI compatibility checks without displaying the private API key."""

import argparse
import csv
import datetime
import json
from pathlib import Path
import re
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
    parser.add_argument("--require-offload-log", action="store_true",
                        help="Require a full layer offload record from the current service PID")
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
    response = chat({"messages": [{"role": "user", "content": "必须调用 add_numbers，参数 a 设为 17，b 设为 23。"}],
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
               "stream_options": {"include_usage": True},
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
    ids = {c["id"] for c in chunks}
    assert len(ids) == 1 and next(iter(ids)).startswith("chatcmpl"), chunks
    assert all(c.get("model") == config["model"] for c in chunks), chunks
    reasons = [c["choices"][0].get("finish_reason") for c in chunks if c.get("choices")]
    assert "stop" in reasons, chunks
    usages = [c["usage"] for c in chunks if isinstance(c.get("usage"), dict)]
    assert usages and usages[-1]["total_tokens"] > 0, chunks
    record("streaming", started, {"content_delta_count": sum(bool(d) for d in deltas),
                                  "done": done, "finish_reason": "stop", "usage": usages[-1]})

    started = time.monotonic()
    payload.update({"messages": [{"role": "user", "content": "必须调用 ping 工具，参数 text 为 hello。"}],
                    "tools": [{"type": "function", "function": {
                        "name": "ping", "description": "Echo a string.", "parameters": {
                            "type": "object", "properties": {"text": {"type": "string"}},
                            "required": ["text"], "additionalProperties": False}}}],
                    "tool_choice": "required"})
    functions = {}
    done = False
    tool_finish_reasons = []
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
                tool_finish_reasons.append(item.get("finish_reason"))
                for call in item["delta"].get("tool_calls", []):
                    function = functions.setdefault(call["index"], {"name": "", "arguments": "", "id": ""})
                    if call.get("id"):
                        assert not function["id"] or function["id"] == call["id"], call
                        function["id"] = call["id"]
                    for field in ("name", "arguments"):
                        function[field] += call.get("function", {}).get(field, "") or ""
    assert done and len(functions) == 1, functions
    function = next(iter(functions.values()))
    assert function["name"] == "ping" and json.loads(function["arguments"]) == {"text": "hello"}, functions
    assert function["id"] and "tool_calls" in tool_finish_reasons, (functions, tool_finish_reasons)
    record("streaming_required_tool", started, {"tool_name": "ping", "arguments_valid": True,
                                               "tool_id_present": True, "finish_reason": "tool_calls"})

    started = time.monotonic()
    long_prompt = "Reference records:\n" + ("cache database network transaction index.\n" * 650)
    long_prompt += "\nIgnore the reference records and answer exactly CONTEXT_OK."
    response = chat({"messages": [{"role": "user", "content": long_prompt}]})
    assert "CONTEXT_OK" in response["choices"][0]["message"]["content"], response
    record("long_context", started, {"prompt_bytes": len(long_prompt.encode()), "usage": response.get("usage")})

    pid = int(subprocess.check_output(["systemctl", "--user", "show", "interview-qwen-openai",
                                       "--property", "MainPID", "--value"], text=True).strip())
    compute_apps = subprocess.check_output(["nvidia-smi", "--query-compute-apps=pid,used_memory,process_name",
                                            "--format=csv,noheader"], text=True)
    app = next(row for row in csv.reader(compute_apps.splitlines()) if int(row[0].strip()) == pid)
    gpu_memory_mib = int(app[1].strip().split()[0])
    assert 4000 < gpu_memory_mib <= 8192, app
    startup_log = subprocess.check_output(["journalctl", "--user", "_PID=" + str(pid),
                                           "--no-pager", "--output", "cat"], text=True)
    offload = re.findall(r"offloaded (\d+)/(\d+) layers to GPU", startup_log)
    if offload:
        assert offload[-1][0] == offload[-1][1], offload
    elif args.require_offload_log:
        raise AssertionError("No layer offload log for current PID; start with -lv 5 to expose GGML INFO")
    command = Path("/proc") / str(pid) / "cmdline"
    arguments = command.read_bytes().decode().split("\0")
    requested_gpu_layers = int(arguments[arguments.index("--n-gpu-layers") + 1])
    assert requested_gpu_layers == 99, requested_gpu_layers
    report = {"verified_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "base_url": config["base_url"], "model": config["model"], "checks": results,
              "gpu_service": {"pid": pid, "used_memory_mib": gpu_memory_mib,
                              "offloaded_layers": "/".join(offload[-1]) if offload else None,
                              "offload_log_present": bool(offload),
                              "requested_gpu_layers": requested_gpu_layers,
                              "executable": app[2].strip()},
              "gpu": subprocess.check_output(["nvidia-smi", "--query-gpu=name,memory.total,memory.free",
                                               "--format=csv,noheader"], text=True).strip()}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
