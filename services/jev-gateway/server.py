"""Python 3.8+ Jev gateway. Private SSH tunnel; no weights or credentials in responses."""
import hmac
import json
import os
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = os.environ.get("JEV_PROXY_TOKEN", "")
KEY = os.environ.get("TYPESAFE_API_KEY", "")
UPSTREAM = os.environ.get("JEV_UPSTREAM_URL", "https://api.typesafe.ai/v1/systemone")
CALL_LOCK = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # Do not retain input states or headers in default access logs.

    def send_json(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def do_GET(self):
        if self.path != "/health":
            return self.send_json(404, {"error": "not_found"})
        return self.send_json(200, {"service": "interview-jev-gateway", "ready": bool(KEY and TOKEN),
            "api_key_configured": bool(KEY), "provider": "systemone-api", "model": "jev-1.13.0"})

    def do_POST(self):
        if self.path != "/v1/systemone":
            return self.send_json(404, {"error": "not_found"})
        if not TOKEN or not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + TOKEN):
            return self.send_json(401, {"error": "unauthorized"})
        if not KEY:
            return self.send_json(503, {"error": "TYPESAFE_API_KEY_NOT_CONFIGURED"})
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= 256_000:
            return self.send_json(413, {"error": "state_too_large"})
        try:
            body = json.loads(self.rfile.read(length))
            if not isinstance(body.get("questions"), dict) or len(body["questions"]) > 50:
                return self.send_json(400, {"error": "invalid_questions"})
            request = urllib.request.Request(UPSTREAM, data=json.dumps(body).encode(), headers={
                "Authorization": "Bearer " + KEY, "Content-Type": "application/json"})
            # Serialize active upstream requests even if an SSH client disconnects.
            if not CALL_LOCK.acquire(timeout=2):
                return self.send_json(429, {"error": "gateway_busy"})
            try:
                with urllib.request.urlopen(request, timeout=5) as response:
                    data = json.loads(response.read(2_000_000))
                return self.send_json(200, data)
            finally:
                CALL_LOCK.release()
        except urllib.error.HTTPError as error:
            return self.send_json(error.code, {"error": "upstream_rejected", "status": error.code})
        except (ValueError, urllib.error.URLError, TimeoutError):
            return self.send_json(503, {"error": "upstream_unavailable"})


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("JEV_PROXY_TOKEN must be configured")
    ThreadingHTTPServer((os.environ.get("HOST", "127.0.0.1"), int(os.environ.get("PORT", "18788"))), Handler).serve_forever()
