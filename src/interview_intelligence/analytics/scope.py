"""HMAC-bound occurrence scopes and pagination cursors."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time


def _encode(payload: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True,
                                               separators=(",", ":"), ensure_ascii=False).encode()).decode().rstrip("=")


def sign_scope(payload: dict, *, key: str, corpus_revision: int,
               issued_at: int | None = None) -> str:
    body = _encode({**payload, "corpus_revision": corpus_revision,
                    "issued_at": issued_at if issued_at is not None else int(time.time())})
    signature = hmac.new(key.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def verify_scope(token: str, *, key: str, corpus_revision: int,
                 now: int | None = None, max_age: int = 3600) -> dict:
    try:
        body, signature = token.rsplit(".", 1)
        expected = hmac.new(key.encode(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("INVALID_SCOPE")
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except (ValueError, UnicodeError, json.JSONDecodeError):
        raise ValueError("INVALID_SCOPE") from None
    current = now if now is not None else int(time.time())
    if payload.get("corpus_revision") != corpus_revision:
        raise ValueError("SNAPSHOT_CHANGED")
    if not isinstance(payload.get("issued_at"), int) or not 0 <= current - payload["issued_at"] <= max_age:
        raise ValueError("INVALID_SCOPE_EXPIRED")
    return payload
