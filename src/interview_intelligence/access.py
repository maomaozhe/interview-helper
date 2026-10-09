"""Anonymous access admission and a separately authenticated operations API.

Admission transactions lock the singleton policy row (PostgreSQL), or obtain an
IMMEDIATE SQLite transaction. Policy changes, bans, quota resets and admissions
therefore have a single durable serialization point, including across workers.
No messages, query bodies, administrator tokens or raw cookies are persisted.
"""
from __future__ import annotations

import asyncio
import base64
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import ipaddress
import json
import math
from pathlib import Path
import secrets
import time
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import Field
from sqlalchemy import Integer, case, cast, delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from interview_intelligence.access_models import (
    AccessAdmission, AccessAudit, AccessCounter, AccessIPBlock,
    AccessPolicy, AccessRequest, AccessVisitor,
)
from interview_intelligence.contracts import StrictModel
from interview_intelligence.domain.models import now_utc

ADMIN_COOKIE = "ii_admin_session"
DEVICE_COOKIE = "ii_device"
BEIJING = timezone(timedelta(hours=8))
POLICY_FIELDS = ("version", "quota_enabled", "quota_limit", "quota_period", "quota_scope",
                 "rate_enabled", "rate_per_minute")


def iso(value):
    return value.replace(tzinfo=timezone.utc).isoformat() if value.tzinfo is None else value.isoformat()


ERROR_MESSAGES = {
    "ACCESS_BLOCKED": "当前设备或 IP 已被禁用，请联系管理员。",
    "QUOTA_EXCEEDED": "对话额度已用完，请联系管理员调整或重置额度。",
    "RATE_LIMITED": "请求过于频繁，请稍后再试。",
    "IDEMPOTENCY_CONFLICT": "请求编号已用于不同的内容，请重新提交。",
    "ADMIN_NOT_CONFIGURED": "管理面板尚未配置访问凭证。",
    "ADMIN_AUTH_REQUIRED": "管理员登录已过期或凭证无效，请重新登录。",
    "ADMIN_LOGIN_RATE_LIMITED": "登录尝试过于频繁，请稍后再试。",
    "ADMIN_ACTION_HEADER_REQUIRED": "管理操作缺少必要的校验信息。",
    "ADMIN_ORIGIN_REJECTED": "管理操作必须从本站页面发起。",
    "POLICY_VERSION_CONFLICT": "策略已被其他管理员修改，请刷新后重试。",
    "POLICY_NULL_NOT_ALLOWED": "策略配置不能留空。",
    "VISITOR_NOT_FOUND": "未找到该访客设备。",
    "INVALID_IP": "请输入有效的 IPv4 或 IPv6 地址。",
    "TENANT_AUTH_REQUIRED": "请先登录账号。",
    "TENANT_LOGIN_INVALID": "账号或密码不正确。",
    "TENANT_USERNAME_TAKEN": "账号名称已被使用。",
    "TENANT_LOGIN_RATE_LIMITED": "登录或注册过于频繁，请稍后再试。",
    "TENANT_ACTION_HEADER_REQUIRED": "账号操作缺少必要的校验信息。",
    "TENANT_ORIGIN_REJECTED": "账号操作必须从本站页面发起。",
    "SYSTEM_TRIAL_EXHAUSTED": "系统模型试用次数已用完，请配置自己的 API Key、Base URL 和模型。",
    "PROVIDER_VERSION_CONFLICT": "模型配置已更新，请刷新后重试。",
    "PROVIDER_API_KEY_REQUIRED": "请输入自己的 API Key。",
    "PROVIDER_CREDENTIAL_UNAVAILABLE": "模型凭证暂时不可用，请联系管理员。",
}


class AccessDenied(HTTPException):
    def __init__(self, status, code, *, message=None, retryable=None, retry_after=None):
        super().__init__(status, code, headers={"Retry-After": str(retry_after)} if retry_after else None)
        self.message, self.retryable = message, retryable


def access_error(status, code, retry_after=None, *, message=None, retryable=None):
    return JSONResponse(status_code=status, content={"error": {"code": code,
        "message": message or ERROR_MESSAGES.get(code, code),
        "retryable": status == 429 and code != "SYSTEM_TRIAL_EXHAUSTED" if retryable is None else retryable}, "detail": code},
        headers={"Retry-After": str(retry_after)} if retry_after else None)


def sign_cookie(key, purpose, identifier, expires):
    payload = f"{purpose}:{identifier}:{expires}"
    signature = hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}:{signature}".encode()).decode().rstrip("=")


def verify_cookie(key, purpose, value):
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode()
        kind, identifier, expires, signature = raw.split(":")
        payload = f"{kind}:{identifier}:{expires}"
        expected = hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if kind == purpose and int(expires) > time.time() and hmac.compare_digest(signature, expected):
            return identifier
    except (ValueError, UnicodeError, AttributeError):
        pass
    return None


class PolicyUpdate(StrictModel):
    expected_version: int = Field(ge=1)
    quota_enabled: bool | None = None
    quota_limit: int | None = Field(default=None, ge=1, le=1_000_000)
    quota_period: Literal["DAY", "LIFETIME"] | None = None
    quota_scope: Literal["DEVICE", "IP"] | None = None
    rate_enabled: bool | None = None
    rate_per_minute: int | None = Field(default=None, ge=1, le=100_000)


class BlockUpdate(StrictModel):
    blocked: bool
    reason: str = Field(default="", max_length=500)


class IPBlockUpdate(BlockUpdate):
    ip: str = Field(min_length=2, max_length=64)


class Login(StrictModel):
    token: str = Field(min_length=1, max_length=4096)


class ResetQuota(StrictModel):
    reason: str = Field(default="", max_length=500)
    expected_version: int | None = Field(default=None, ge=1)


class AccessService:
    def __init__(self, database, settings):
        self.database, self.settings = database, settings
        self.proxies = [ipaddress.ip_network(cidr) for cidr in settings.trusted_proxy_cidrs]
        with self.transaction() as session:
            insert = pg_insert if database.engine.dialect.name == "postgresql" else sqlite_insert
            session.execute(insert(AccessPolicy).values(id=1, cookie_key=secrets.token_hex(32))
                            .on_conflict_do_nothing(index_elements=["id"]))
            session.execute(insert(AccessCounter).values(key="maintenance", used=0)
                            .on_conflict_do_nothing(index_elements=["key"]))
            self.cookie_key = settings.app_signing_key or session.get(AccessPolicy, 1).cookie_key

    @contextmanager
    def transaction(self):
        with self.database.session() as session:
            if self.database.engine.dialect.name == "sqlite":
                session.execute(text("BEGIN IMMEDIATE"))
            else:
                session.begin()
            try:
                yield session
                session.commit()
            except BaseException:
                session.rollback()
                raise

    def locked_policy(self, session):
        return session.scalar(select(AccessPolicy).where(AccessPolicy.id == 1).with_for_update())

    def trusted(self, address):
        try:
            ip = ipaddress.ip_address(address)
            return any(ip in network for network in self.proxies)
        except ValueError:
            return False

    def client_ip(self, request):
        peer = request.client.host if request.client else "unknown"
        if self.trusted(peer):
            # Walk right to left; attacker-controlled entries left of the first
            # untrusted hop cannot replace the client address.
            forwarded = request.headers.get("x-forwarded-for", "")
            chain = [part.strip() for part in forwarded.split(",") if part.strip()]
            if len(chain) > 32:
                return "unknown"
            for candidate in reversed(chain):
                if not self.trusted(peer):
                    break
                try:
                    peer = str(ipaddress.ip_address(candidate))
                except ValueError:
                    return "unknown"
        try:
            return str(ipaddress.ip_address(peer))
        except ValueError:
            return "unknown"

    def secure(self, request):
        return request.url.scheme == "https" or bool(request.client and
            self.trusted(request.client.host) and request.headers.get("x-forwarded-proto") == "https")

    def observe(self, request):
        identifier = verify_cookie(self.cookie_key, "device", request.cookies.get(DEVICE_COOKIE, ""))
        try:
            identifier = str(UUID(identifier)) if identifier else None
        except ValueError:
            identifier = None
        fresh = not identifier
        identifier = identifier or str(uuid4())
        ip, agent, now = self.client_ip(request), request.headers.get("user-agent", "")[:512], now_utc()
        device = "Mobile" if any(v in agent.lower() for v in ("mobile", "android", "iphone")) else "Desktop"
        browser = next((label for marker, label in (("Edg/", "Edge"), ("Firefox/", "Firefox"),
            ("Chrome/", "Chrome"), ("Safari/", "Safari")) if marker in agent), "Other")
        with self.transaction() as session:
            insert = pg_insert if self.database.engine.dialect.name == "postgresql" else sqlite_insert
            session.execute(insert(AccessVisitor).values(id=identifier, ip=ip, user_agent=agent,
                device=device, browser=browser, first_seen=now, last_seen=now)
                .on_conflict_do_update(index_elements=["id"], set_={"ip": ip,
                    "user_agent": agent, "device": device, "browser": browser, "last_seen": now}))
            visitor = session.get(AccessVisitor, identifier)
            ban = session.get(AccessIPBlock, ip)
            blocked = visitor.blocked or bool(ban and ban.blocked)
        request.state.access_visitor_id, request.state.access_ip = identifier, ip
        return identifier, fresh, blocked

    def quota_key(self, policy, identifier, ip, now=None):
        now = now or now_utc()
        period = now.astimezone(BEIJING).date().isoformat() if policy.quota_period == "DAY" else "LIFETIME"
        principal = identifier if policy.quota_scope == "DEVICE" else ip
        return f"quota:{policy.quota_scope}:{principal}:{period}"

    def counter(self, session, key):
        row = session.get(AccessCounter, key)
        if row is None:
            row = AccessCounter(key=key, used=0)
            session.add(row)
        return row

    def admit(self, request, request_id, payload, *, admission_hook=None):
        """Called only after Pydantic validation and before a model/search run."""
        identifier, ip = request.state.access_visitor_id, request.state.access_ip
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
            separators=(",", ":"), default=str).encode()).hexdigest()
        now = now_utc()
        with self.transaction() as session:
            policy = self.locked_policy(session)
            visitor, ban = session.get(AccessVisitor, identifier), session.get(AccessIPBlock, ip)
            if visitor.blocked or (ban and ban.blocked):
                raise HTTPException(403, "ACCESS_BLOCKED")
            previous = session.scalar(select(AccessAdmission).where(
                AccessAdmission.visitor_id == identifier, AccessAdmission.request_id == request_id))
            if previous:
                if previous.payload_hash != digest:
                    raise HTTPException(409, "IDEMPOTENCY_CONFLICT")
                return admission_hook(session) if admission_hook else None
            quota = self.counter(session, self.quota_key(policy, identifier, ip, now))
            minute = int(now.timestamp()) // 60
            rate = self.counter(session, f"rate:{ip}:{minute}")
            if policy.quota_enabled and quota.used >= policy.quota_limit:
                until = (datetime.combine(now.astimezone(BEIJING).date() + timedelta(days=1),
                    datetime.min.time(), tzinfo=BEIJING) - now).total_seconds()
                if policy.quota_period == "DAY":
                    raise AccessDenied(429, "QUOTA_EXCEEDED", message="已达到今日对话额度，请明天再试，或联系管理员调整额度。",
                        retry_after=max(1, int(until)))
                raise AccessDenied(429, "QUOTA_EXCEEDED", message="已达到累计对话额度，请联系管理员重置额度。", retryable=False)
            if policy.rate_enabled and rate.used >= policy.rate_per_minute:
                raise HTTPException(429, "RATE_LIMITED", headers={"Retry-After": str(60 - int(now.timestamp()) % 60)})
            admission = admission_hook(session) if admission_hook else None
            # Observe every combination while controls are off. A later switch
            # of scope or period uses actual admissions instead of starting at 0.
            day = now.astimezone(BEIJING).date().isoformat()
            for scope, principal in (("DEVICE", identifier), ("IP", ip)):
                for period in (day, "LIFETIME"):
                    counter = self.counter(session, f"quota:{scope}:{principal}:{period}")
                    counter.used += 1
                    counter.updated_at = now
            rate.used += 1
            rate.updated_at = now
            session.add(AccessAdmission(visitor_id=identifier, request_id=request_id[:128], payload_hash=digest))
        request.state.access_chat = True
        return admission

    def record(self, request, status, duration, started):
        identifier, ip = request.state.access_visitor_id, request.state.access_ip
        page = request.url.path.rstrip("/") == request.scope.get("root_path", "").rstrip("/") and request.method == "GET" and status < 400
        chat = bool(getattr(request.state, "access_chat", False))
        with self.transaction() as session:
            session.execute(update(AccessVisitor).where(AccessVisitor.id == identifier).values(
                requests=AccessVisitor.requests + 1, page_views=AccessVisitor.page_views + int(page),
                chat_requests=AccessVisitor.chat_requests + int(chat)))
            route = request.scope.get("route")
            safe_path = getattr(route, "path", "/unknown")
            if page:
                safe_path = "/"
            session.add(AccessRequest(visitor_id=identifier, ip=ip, path=safe_path[:200],
                status=status, duration_ms=duration, page_view=page, chat=chat,
                blocked=status in (403, 429), created_at=started))
            # One cleanup lease shared across workers; bounded batches keep traffic
            # independent of how much data was produced while the app was offline.
            cleanup = session.scalar(select(AccessCounter).where(AccessCounter.key == "maintenance").with_for_update())
            if not cleanup.used or started.timestamp() - cleanup.used >= 60:
                cleanup.used = int(started.timestamp())
                for model, cutoff in ((AccessRequest, started - timedelta(days=7)),
                                      (AccessAudit, started - timedelta(days=30))):
                    ids = select(model.id).where(model.created_at < cutoff).limit(1000)
                    session.execute(delete(model).where(model.id.in_(ids)))
                old = select(AccessCounter.key).where(AccessCounter.updated_at < started - timedelta(days=8),
                    ~AccessCounter.key.like("%:LIFETIME"), AccessCounter.key != "maintenance").limit(1000)
                session.execute(delete(AccessCounter).where(AccessCounter.key.in_(old)))

    def authenticate(self, request, *, mutate=False):
        secret = self.settings.admin_access_token
        if not secret:
            raise HTTPException(503, "ADMIN_NOT_CONFIGURED")
        provided = request.headers.get("authorization", "")
        bearer = bool(provided.startswith("Bearer ") and secrets.compare_digest(provided[7:], secret))
        cookie = verify_cookie(secret, "admin", request.cookies.get(ADMIN_COOKIE, ""))
        if not bearer and not cookie:
            raise HTTPException(401, "ADMIN_AUTH_REQUIRED")
        if mutate:
            self.check_action(request)

    def check_action(self, request):
        if request.headers.get("x-admin-action") != "1":
            raise HTTPException(403, "ADMIN_ACTION_HEADER_REQUIRED")
        origin = request.headers.get("origin")
        if origin:
            expected = ("https" if self.secure(request) else request.url.scheme, request.url.netloc)
            parsed = urlsplit(origin)
            if (parsed.scheme, parsed.netloc) != expected or parsed.path not in ("", "/"):
                raise HTTPException(403, "ADMIN_ORIGIN_REJECTED")
        if request.headers.get("sec-fetch-site") in ("cross-site", "same-site"):
            raise HTTPException(403, "ADMIN_ORIGIN_REJECTED")

    def audit(self, session, action, target, details=None):
        session.add(AccessAudit(action=action, target=target, details=details or {}))

    def policy_dict(self, policy):
        return {field: getattr(policy, field) for field in POLICY_FIELDS}

    def login(self, request, token):
        secret = self.settings.admin_access_token
        if not secret:
            raise HTTPException(503, "ADMIN_NOT_CONFIGURED")
        self.check_action(request)
        ip, now = self.client_ip(request), now_utc()
        with self.transaction() as session:
            self.locked_policy(session)
            counter = self.counter(session, f"login:{ip}:{int(now.timestamp()) // 60}")
            if counter.used >= 5:
                self.audit(session, "LOGIN_THROTTLED", ip)
                status = 429
            else:
                counter.used += 1
                counter.updated_at = now
                valid = secrets.compare_digest(token, secret)
                self.audit(session, "LOGIN" if valid else "LOGIN_FAILED", ip)
                status = 200 if valid else 401
        if status != 200:
            raise HTTPException(status, "ADMIN_LOGIN_RATE_LIMITED" if status == 429 else "ADMIN_AUTH_REQUIRED",
                headers={"Retry-After": str(60 - int(now.timestamp()) % 60)} if status == 429 else None)
        return sign_cookie(secret, "admin", secrets.token_hex(16), int(time.time()) + 8 * 3600)

    def overview(self, window):
        end = now_utc()
        start = end - {"1h": timedelta(hours=1), "24h": timedelta(hours=24), "7d": timedelta(days=7)}[window]
        width = {"1h": 60, "24h": 900, "7d": 3600}[window]
        with self.database.session() as session:
            conditions = (AccessRequest.created_at >= start, AccessRequest.created_at <= end)
            values = session.execute(select(func.count(), func.count(func.distinct(AccessRequest.visitor_id)),
                func.count(func.distinct(case((AccessRequest.ip != "unknown", AccessRequest.ip)))),
                func.sum(cast(AccessRequest.page_view, Integer)), func.sum(cast(AccessRequest.chat, Integer)),
                func.sum(cast(AccessRequest.blocked, Integer)),
                func.sum(case((AccessRequest.status >= 500, 1), else_=0))).where(*conditions)).one()
            total, visitors, ips, pages, chats, blocked, errors = (value or 0 for value in values)
            p95 = session.scalar(select(AccessRequest.duration_ms).where(*conditions)
                .order_by(AccessRequest.duration_ms).offset(max(0, math.ceil(total * .95) - 1)).limit(1)) or 0
            epoch = (func.extract("epoch", AccessRequest.created_at) if session.bind.dialect.name == "postgresql"
                else cast(func.strftime("%s", AccessRequest.created_at), Integer))
            bucket = cast(func.floor(epoch / width), Integer)
            groups = session.execute(select(bucket, func.count(),
                func.sum(case((AccessRequest.status >= 500, 1), else_=0))).where(*conditions).group_by(bucket)).all()
            seconds = select(cast(func.floor(epoch), Integer).label("second"), func.count().label("n"))\
                .where(*conditions).group_by(cast(func.floor(epoch), Integer)).subquery()
            peak = session.scalar(select(func.max(seconds.c.n))) or 0
            current = session.scalar(select(func.count()).select_from(AccessRequest).where(
                AccessRequest.created_at >= end - timedelta(seconds=60), AccessRequest.created_at <= end)) or 0
        counts = {int(group[0]): (group[1], group[2]) for group in groups}
        # Include empty bins. First and last partial bins use their actual width.
        series = []
        for slot in range(int(start.timestamp()) // width, int(end.timestamp()) // width + 1):
            requests, fails = counts.get(slot, (0, 0))
            seconds_in_window = min(end.timestamp(), (slot + 1) * width) - max(start.timestamp(), slot * width)
            series.append({"timestamp": iso(datetime.fromtimestamp(slot * width, timezone.utc)),
                "requests": requests, "qps": round(requests / max(1, seconds_in_window), 4), "errors": fails})
        return {"window": window, "from": iso(start), "to": iso(end), "bin_seconds": width,
            "summary": {"visitors": visitors, "ips": ips, "page_views": pages, "requests": total,
                "chat_requests": chats, "blocked_requests": blocked, "error_rate": errors / total if total else 0,
                "p95_ms": round(p95, 2), "current_qps": round(current / 60, 4), "peak_qps": peak}, "series": series}

    def visitors(self, q, page, page_size):
        with self.database.session() as session:
            policy = session.get(AccessPolicy, 1)
            conditions = []
            if q:
                literal = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                conditions.append(AccessVisitor.id.ilike(f"%{literal}%", escape="\\") |
                    AccessVisitor.ip.ilike(f"%{literal}%", escape="\\") |
                    AccessVisitor.user_agent.ilike(f"%{literal}%", escape="\\"))
            total = session.scalar(select(func.count()).select_from(AccessVisitor).where(*conditions))
            rows = session.scalars(select(AccessVisitor).where(*conditions).order_by(AccessVisitor.last_seen.desc())
                .offset((page - 1) * page_size).limit(page_size)).all()
            items = []
            for visitor in rows:
                ban = session.get(AccessIPBlock, visitor.ip)
                counter = session.get(AccessCounter, self.quota_key(policy, visitor.id, visitor.ip))
                items.append({field: getattr(visitor, field) for field in ("id", "ip", "user_agent", "device", "browser",
                    "page_views", "requests", "chat_requests", "blocked")} | {
                    "first_seen": iso(visitor.first_seen), "last_seen": iso(visitor.last_seen),
                    "ip_blocked": bool(ban and ban.blocked), "quota_used": counter.used if counter else 0,
                    "quota_limit": policy.quota_limit})
        return {"items": items, "total": total, "page": page, "page_size": page_size}


class AccessMiddleware:
    def __init__(self, app, service):
        self.app, self.service = app, service

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        prefix = scope.get("root_path", "").rstrip("/")
        local_path = path.removeprefix(prefix) if prefix and path.startswith(prefix + "/") else path
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if local_path.startswith("/api/admin/"):
            # Authenticate before request body validation so a disabled panel
            # always returns 503 and malformed unauthenticated payloads leak no input.
            request = Request(scope, receive)
            try:
                if not self.service.settings.admin_access_token:
                    raise HTTPException(503, "ADMIN_NOT_CONFIGURED")
                if local_path == "/api/admin/session" and request.method == "POST":
                    self.service.check_action(request)
                else:
                    self.service.authenticate(request, mutate=request.method not in ("GET", "HEAD", "OPTIONS"))
            except HTTPException as error:
                response = access_error(error.status_code, error.detail)
                response.headers.update(error.headers or {})
                response.headers["Cache-Control"] = "no-store"
                return await response(scope, receive, send)
            async def admin_send(message):
                if message["type"] == "http.response.start":
                    message["headers"] = list(message["headers"]) + [(b"cache-control", b"no-store")]
                await send(message)
            return await self.app(scope, receive, admin_send)
        if local_path.startswith(("/assets/", "/api/internal/", "/internal/")) or local_path in ("/admin", "/admin/", "/api/health", "/favicon.ico"):
            return await self.app(scope, receive, send)
        request, started, clock = Request(scope, receive), now_utc(), time.perf_counter()
        identifier, fresh, blocked = await asyncio.to_thread(self.service.observe, request)
        status = 500
        recorded = False
        async def capture(message):
            nonlocal status, recorded
            if message["type"] == "http.response.start":
                status = message["status"]
                # Count established responses immediately; an active SSE stream
                # must appear on QPS while generation is still running.
                await asyncio.to_thread(self.service.record, request, status, (time.perf_counter() - clock) * 1000, started)
                recorded = True
                if fresh:
                    response = JSONResponse(None)
                    response.set_cookie(DEVICE_COOKIE, sign_cookie(self.service.cookie_key, "device", identifier,
                        int(time.time()) + 365 * 86400), max_age=365 * 86400, httponly=True,
                        secure=self.service.secure(request), samesite="lax", path=prefix or "/")
                    message["headers"] = list(message["headers"]) + [header for header in response.raw_headers if header[0] == b"set-cookie"]
            await send(message)
        try:
            if blocked:
                await access_error(403, "ACCESS_BLOCKED")(scope, receive, capture)
            else:
                await self.app(scope, receive, capture)
        finally:
            if not recorded:
                await asyncio.to_thread(self.service.record, request, status, (time.perf_counter() - clock) * 1000, started)


def register_access(app, database, settings):
    service = AccessService(database, settings)
    app.state.access_service = service
    app.add_middleware(AccessMiddleware, service=service)

    @app.get("/admin", response_class=HTMLResponse)
    @app.get("/admin/", response_class=HTMLResponse)
    def admin_page(request: Request):
        path = Path(__file__).with_name("web") / "admin.html"
        prefix = request.scope.get("root_path", "").rstrip("/")
        return HTMLResponse(path.read_text(encoding="utf-8").replace("__BASE_PATH__", prefix + "/"), headers={
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'self'; frame-ancestors 'none'"})

    @app.post("/api/admin/session")
    def admin_login(request: Request, payload: Login):
        cookie = service.login(request, payload.token)
        response = JSONResponse({"data": {"authenticated": True}})
        response.set_cookie(ADMIN_COOKIE, cookie, max_age=8 * 3600, httponly=True,
            secure=service.secure(request), samesite="strict", path=request.scope.get("root_path", "").rstrip("/") or "/")
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/admin/session")
    def admin_session(request: Request):
        service.authenticate(request)
        return JSONResponse({"data": {"authenticated": True}}, headers={"Cache-Control": "no-store"})

    @app.delete("/api/admin/session")
    def admin_logout(request: Request):
        service.authenticate(request, mutate=True)
        response = JSONResponse({"data": {"authenticated": False}})
        response.delete_cookie(ADMIN_COOKIE, path=request.scope.get("root_path", "").rstrip("/") or "/")
        return response

    @app.get("/api/admin/overview")
    def admin_overview(request: Request, window: Literal["1h", "24h", "7d"] = "24h"):
        service.authenticate(request)
        return {"data": service.overview(window)}

    @app.get("/api/admin/visitors")
    def admin_visitors(request: Request, q: str = Query(default="", max_length=200),
                       page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100)):
        service.authenticate(request)
        return {"data": service.visitors(q, page, page_size)}

    @app.get("/api/admin/policy")
    def admin_policy(request: Request):
        service.authenticate(request)
        with database.session() as session:
            return {"data": service.policy_dict(session.get(AccessPolicy, 1))}

    @app.patch("/api/admin/policy")
    def update_policy(request: Request, payload: PolicyUpdate):
        service.authenticate(request, mutate=True)
        with service.transaction() as session:
            policy = service.locked_policy(session)
            if policy.version != payload.expected_version:
                raise HTTPException(409, "POLICY_VERSION_CONFLICT")
            before = service.policy_dict(policy)
            changes = payload.model_dump(exclude_unset=True, exclude={"expected_version"})
            if any(value is None for value in changes.values()):
                raise HTTPException(422, "POLICY_NULL_NOT_ALLOWED")
            for field, value in changes.items():
                setattr(policy, field, value)
            policy.version += 1
            after = service.policy_dict(policy)
            service.audit(session, "POLICY_UPDATE", "policy", {"before": before, "after": after})
            return {"data": after}

    @app.patch("/api/admin/visitors/{visitor_id}")
    def visitor_block(request: Request, visitor_id: str, payload: BlockUpdate):
        service.authenticate(request, mutate=True)
        with service.transaction() as session:
            service.locked_policy(session)
            visitor = session.get(AccessVisitor, visitor_id)
            if not visitor:
                raise HTTPException(404, "VISITOR_NOT_FOUND")
            visitor.blocked = payload.blocked
            service.audit(session, "DEVICE_BLOCK" if payload.blocked else "DEVICE_UNBLOCK", visitor_id, {"reason": payload.reason})
        return {"data": {"id": visitor_id, "blocked": payload.blocked}}

    @app.patch("/api/admin/ips")
    def ip_block(request: Request, payload: IPBlockUpdate):
        service.authenticate(request, mutate=True)
        try:
            ip = str(ipaddress.ip_address(payload.ip))
        except ValueError:
            raise HTTPException(422, "INVALID_IP")
        with service.transaction() as session:
            service.locked_policy(session)
            ban = session.get(AccessIPBlock, ip)
            if not ban:
                ban = AccessIPBlock(ip=ip)
                session.add(ban)
            ban.blocked, ban.reason = payload.blocked, payload.reason
            service.audit(session, "IP_BLOCK" if payload.blocked else "IP_UNBLOCK", ip, {"reason": payload.reason})
        return {"data": {"ip": ip, "blocked": payload.blocked}}

    @app.post("/api/admin/visitors/{visitor_id}/reset-quota")
    def reset_quota(request: Request, visitor_id: str, payload: ResetQuota | None = None):
        service.authenticate(request, mutate=True)
        with service.transaction() as session:
            policy = service.locked_policy(session)
            if payload and payload.expected_version is not None and payload.expected_version != policy.version:
                raise HTTPException(409, "POLICY_VERSION_CONFLICT")
            visitor = session.get(AccessVisitor, visitor_id)
            if not visitor:
                raise HTTPException(404, "VISITOR_NOT_FOUND")
            key = service.quota_key(policy, visitor.id, visitor.ip)
            counter = service.counter(session, key)
            before, counter.used = counter.used, 0
            counter.updated_at = now_utc()
            service.audit(session, "QUOTA_RESET", visitor_id, {"scope": policy.quota_scope, "key": key,
                "previous_used": before, "reason": payload.reason if payload else ""})
        return {"data": {"id": visitor_id, "quota_used": 0}}

    @app.get("/api/admin/audit")
    def admin_audit(request: Request, limit: int = Query(default=50, ge=1, le=100)):
        service.authenticate(request)
        with database.session() as session:
            rows = session.scalars(select(AccessAudit).where(AccessAudit.created_at >= now_utc() - timedelta(days=30))
                .order_by(AccessAudit.created_at.desc()).limit(limit)).all()
            return {"data": {"items": [{"id": row.id, "created_at": iso(row.created_at), "actor": row.actor,
                "action": row.action, "target": row.target, "details": row.details} for row in rows]}}

    @app.exception_handler(HTTPException)
    async def controlled_error(request, error):
        if isinstance(error.detail, str):
            response = access_error(error.status_code, error.detail,
                message=getattr(error, "message", None), retryable=getattr(error, "retryable", None))
            response.headers.update(error.headers or {})
            return response
        return JSONResponse({"detail": error.detail}, status_code=error.status_code, headers=error.headers)

    return service
