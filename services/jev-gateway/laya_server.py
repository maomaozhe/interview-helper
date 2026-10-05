"""Private, fixed-model System One adapter. Apache-2.0 Laya; not official Jev."""
import hmac
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from laya import Agent

ROOT = Path(__file__).resolve().parent
MODEL = "convaiinnovations/laya-multilingual"
REVISION = "e4e9ddf21a7b1903b7acffd8814ad4307bf63a67"
TOKEN = os.environ.get("JEV_PROXY_TOKEN", "")
LOCK = threading.Lock()
AGENT = None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send_json(self, status, body):
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        try:
            self.wfile.write(encoded)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        if self.path != "/health":
            return self.send_json(404, {"error": "not_found"})
        return self.send_json(200, {"service": "interview-system-one", "ready": AGENT is not None,
            "provider": "laya", "model": MODEL, "revision": REVISION,
            "device": str(AGENT.device), "backend": "eager", "domain_calibrated": False})

    def do_POST(self):
        if self.path != "/v1/systemone":
            return self.send_json(404, {"error": "not_found"})
        if not TOKEN or not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + TOKEN):
            return self.send_json(401, {"error": "unauthorized"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 256_000:
                return self.send_json(413, {"error": "state_too_large"})
            body = json.loads(self.rfile.read(length))
            questions = body.get("questions")
            if not isinstance(questions, dict) or not 1 <= len(questions) <= 16:
                return self.send_json(400, {"error": "invalid_questions"})
            if body.get("model", MODEL) not in {MODEL, "laya-multilingual"}:
                return self.send_json(400, {"error": "unsupported_model"})
            if any(q.get("type") != "choice" or not isinstance(q.get("criteria"), dict)
                   or not 1 <= len(q["criteria"]) <= 200 for q in questions.values()):
                return self.send_json(400, {"error": "invalid_choice_schema"})
            queued = time.perf_counter()
            if not LOCK.acquire(timeout=2):
                return self.send_json(429, {"error": "service_busy"})
            try:
                started = time.perf_counter()
                result = AGENT.system_one(body["state"], questions, lang="zh", max_len=1024, head_max_len=256)
                elapsed = round((time.perf_counter() - started) * 1000, 2)
                # Never silently route on a partially visible state or collapsed option set.
                usage = result.get("usage", {})
                result.update(provider="laya", model=MODEL, model_revision=REVISION,
                    calibrated=False, timings={"provider_ms": elapsed,
                                              "queue_ms": round((started - queued) * 1000, 2)})
                if usage.get("truncated") or any(v["distinct"] < v["total"] for v in usage.get("options", {}).values()):
                    return self.send_json(422, {"error": "decision_input_truncated", "usage": usage})
                return self.send_json(200, result)
            finally:
                LOCK.release()
        except (ValueError, TypeError, KeyError):
            return self.send_json(400, {"error": "invalid_request"})
        except Exception as error:
            print(json.dumps({"event": "inference_failed", "error_type": type(error).__name__}), flush=True)
            return self.send_json(503, {"error": "inference_failed"})


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("JEV_PROXY_TOKEN must be configured")
    model_path = Path((ROOT / "model-path.txt").read_text().strip()).resolve()
    if not model_path.is_relative_to((ROOT / "model-cache").resolve()):
        raise SystemExit("model must live in the managed model cache")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    AGENT = Agent(str(model_path), device="cuda:0", compile=False, fast=False)
    AGENT.system_one("手写线程池", {"warmup": {"type": "choice", "instructions": "What kind of task is this?",
        "criteria": {"ENGINEERING": "Engineering implementation", "ALGORITHM": "Algorithm problem"}}}, lang="zh")
    print(json.dumps({"event": "ready", "provider": "laya", "model": MODEL, "revision": REVISION}), flush=True)
    ThreadingHTTPServer((os.environ.get("HOST", "127.0.0.1"), int(os.environ.get("PORT", "18788"))), Handler).serve_forever()
