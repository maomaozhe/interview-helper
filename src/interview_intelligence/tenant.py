"""Authenticated account scope and atomic, lifetime system-provider trial use."""
from __future__ import annotations

import asyncio
import base64
from contextvars import ContextVar
from datetime import timedelta, timezone
import hashlib
import json
import secrets
import time
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from interview_intelligence.access import access_error, sign_cookie, verify_cookie
from interview_intelligence.contracts import StrictModel
from interview_intelligence.domain.models import AgentConversation, AgentTurn, now_utc
from interview_intelligence.tenant_models import TenantAccount, TenantAdmission, TenantSession
from interview_intelligence.providers.tenant_transport import validate_tenant_endpoint

TENANT_COOKIE = "ii_tenant_session"
current_tenant = ContextVar("current_tenant", default=None)
current_provider = ContextVar("current_tenant_provider", default=None)


def tenant_user_id(settings):
    return current_tenant.get() or settings.local_user_id


class AccountCredentials(StrictModel):
    username: str = Field(min_length=3, max_length=80, pattern=r"^[\w.@+-]+$")
    password: str = Field(min_length=8, max_length=256)

    @field_validator("username")
    @classmethod
    def normalized_username(cls, value):
        return value.casefold()


class ProviderUpdate(StrictModel):
    expected_version: int = Field(ge=0)
    base_url: str = Field(min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=160)
    reranker_model: str | None = Field(default=None, min_length=1, max_length=160)
    api_key: str | None = Field(default=None, min_length=1, max_length=4096)

    @field_validator("api_key")
    @classmethod
    def safe_header_key(cls, value):
        if value is not None and any(ord(character) < 33 or ord(character) > 126 for character in value):
            raise ValueError("PROVIDER_API_KEY_INVALID")
        return value


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 600_000).hex()
    return f"pbkdf2-sha256:600000:{salt}:{digest}"


def verify_password(password, stored):
    try:
        algorithm, iterations, salt, expected = stored.split(":")
        if algorithm != "pbkdf2-sha256" or iterations != "600000":
            return False
        return secrets.compare_digest(password_hash(password, salt).rsplit(":", 1)[1], expected)
    except ValueError:
        return False


class ScopedService:
    """Resolve a per-account service from request/task context, including threads."""
    def __init__(self, settings, factory):
        self.settings, self.factory, self.services = settings, factory, {}
        from threading import RLock
        self.lock = RLock()

    def for_user(self, identifier):
        with self.lock:
            if identifier not in self.services:
                self.services[identifier] = self.factory(identifier)
            return self.services[identifier]

    def __getattr__(self, name):
        return getattr(self.for_user(tenant_user_id(self.settings)), name)

    def __setattr__(self, name, value):
        if name in {"settings", "factory", "services", "lock"}:
            object.__setattr__(self, name, value)
        else:
            setattr(self.for_user(tenant_user_id(self.settings)), name, value)


class TenantService:
    def __init__(self, database, settings, access):
        self.database, self.settings, self.access = database, settings, access
        self.cookie_key = access.cookie_key
        credential_key = settings.model_credential_key
        if credential_key:
            try:
                self.cipher = Fernet(credential_key.encode())
            except (ValueError, TypeError) as error:
                raise ValueError("MODEL_CREDENTIAL_KEY_INVALID") from error
        else:
            # Durable fallback for local use. Production supplies a separate key.
            key = hashlib.sha256(("tenant-provider:" + self.cookie_key).encode()).digest()
            self.cipher = Fernet(base64.urlsafe_b64encode(key))
        self.dummy_hash = password_hash("unregistered-account-password")

    def seal(self, value):
        return self.cipher.encrypt(value.encode()).decode()

    def unseal(self, value):
        try:
            return self.cipher.decrypt(value.encode()).decode()
        except InvalidToken as error:
            raise HTTPException(503, "PROVIDER_CREDENTIAL_UNAVAILABLE") from error

    def require(self, request):
        identifier = getattr(request.state, "tenant_id", None)
        if not identifier:
            raise HTTPException(401, "TENANT_AUTH_REQUIRED")
        return identifier

    def authenticate(self, request):
        token = verify_cookie(self.cookie_key, "tenant", request.cookies.get(TENANT_COOKIE, ""))
        if not token:
            return None
        with self.database.session() as session:
            row = session.get(TenantSession, hashlib.sha256(token.encode()).hexdigest())
            if not row:
                return None
            expires = row.expires_at.replace(tzinfo=timezone.utc) if row.expires_at.tzinfo is None else row.expires_at
            return row.tenant_id if expires > now_utc() else None

    def check_origin(self, request, *, header=False):
        if header and request.headers.get("x-tenant-action") != "1":
            raise HTTPException(403, "TENANT_ACTION_HEADER_REQUIRED")
        origin = request.headers.get("origin")
        if origin:
            parsed = urlsplit(origin)
            expected = ("https" if self.access.secure(request) else request.url.scheme, request.url.netloc)
            if (parsed.scheme, parsed.netloc) != expected or parsed.path not in ("", "/"):
                raise HTTPException(403, "TENANT_ORIGIN_REJECTED")
        if request.headers.get("sec-fetch-site") in ("cross-site", "same-site"):
            raise HTTPException(403, "TENANT_ORIGIN_REJECTED")

    def status(self, identifier=None):
        value = {"enabled": self.settings.multi_tenant_enabled, "authenticated": bool(identifier)}
        if identifier:
            with self.database.session() as session:
                account = session.get(TenantAccount, identifier)
                value.update(tenant_id=account.id, username=account.username)
        return value

    def throttle(self, request):
        # Global per-IP signup/login budget persists across workers and restarts.
        with self.access.transaction() as session:
            self.access.locked_policy(session)
            minute = int(time.time()) // 60
            counter = self.access.counter(session, f"tenant-login:{self.access.client_ip(request)}:{minute}")
            if counter.used >= 10:
                raise HTTPException(429, "TENANT_LOGIN_RATE_LIMITED", headers={"Retry-After": "60"})
            counter.used += 1
            counter.updated_at = now_utc()

    def login(self, request, payload, *, register=False):
        self.check_origin(request, header=True)
        self.throttle(request)
        token = secrets.token_hex(32)
        expires = now_utc() + timedelta(days=7)
        with self.access.transaction() as session:
            account = session.scalar(select(TenantAccount).where(TenantAccount.username == payload.username))
            if register:
                if account:
                    raise HTTPException(409, "TENANT_USERNAME_TAKEN")
                account = TenantAccount(username=payload.username, password_hash=password_hash(payload.password))
                session.add(account)
                session.flush()
            elif not verify_password(payload.password, account.password_hash if account else self.dummy_hash) or not account:
                raise HTTPException(401, "TENANT_LOGIN_INVALID")
            session.execute(delete(TenantSession).where(TenantSession.expires_at < now_utc()))
            session.add(TenantSession(token_hash=hashlib.sha256(token.encode()).hexdigest(),
                tenant_id=account.id, expires_at=expires))
            identifier = account.id
        response = JSONResponse({"data": self.status(identifier)}, headers={"Cache-Control": "no-store"})
        response.set_cookie(TENANT_COOKIE, sign_cookie(self.cookie_key, "tenant", token, int(expires.timestamp())),
            max_age=7 * 86400, httponly=True, secure=self.access.secure(request), samesite="strict",
            path=request.scope.get("root_path", "").rstrip("/") or "/")
        return response

    def provider_values(self, account):
        if account.provider_key:
            return {"source": "personal", "base_url": account.provider_base_url,
                "api_key": self.unseal(account.provider_key), "model": account.provider_model,
                "reranker_model": account.provider_reranker_model or account.provider_model}
        return {"source": "system", "base_url": self.settings.tenant_default_base_url,
            "api_key": self.settings.tenant_default_api_key, "model": self.settings.tenant_default_query_model,
            "reranker_model": self.settings.tenant_default_reranker_model or self.settings.tenant_default_query_model}

    def runtime_settings(self, values):
        return self.settings.model_copy(update={"model_base_url": values["base_url"], "model_api_key": values["api_key"],
            "query_model": values["model"], "judge_model": values["model"], "reranker_model": values["reranker_model"]})

    def model_status(self, identifier):
        with self.database.session() as session:
            account = session.get(TenantAccount, identifier)
            values = self.provider_values(account)
            limit = self.settings.tenant_trial_limit
            return {"source": values["source"], "base_url": values["base_url"], "model": values["model"],
                "reranker_model": values["reranker_model"], "api_key_configured": bool(values["api_key"]),
                "api_key_hint": account.provider_key_hint if values["source"] == "personal" else None,
                "version": account.provider_version,
                "system_configured": bool(self.settings.tenant_default_base_url and self.settings.tenant_default_api_key
                    and self.settings.tenant_default_query_model),
                "trial": {"limit": limit, "used": account.trial_used, "remaining": max(0, limit - account.trial_used)}}

    def update_model(self, identifier, payload):
        base_url = validate_tenant_endpoint(payload.base_url)
        if not payload.model.strip() or (payload.reranker_model is not None and not payload.reranker_model.strip()):
            raise HTTPException(422, "PROVIDER_MODEL_REQUIRED")
        with self.access.transaction() as session:
            account = session.scalar(select(TenantAccount).where(TenantAccount.id == identifier).with_for_update())
            if account.provider_version != payload.expected_version:
                raise HTTPException(409, "PROVIDER_VERSION_CONFLICT")
            if payload.api_key is not None:
                if not payload.api_key.strip():
                    raise HTTPException(422, "PROVIDER_API_KEY_REQUIRED")
                account.provider_key = self.seal(payload.api_key.strip())
                account.provider_key_hint = "••••" + (payload.api_key[-4:] if len(payload.api_key) >= 8 else "")
            elif not account.provider_key:
                raise HTTPException(422, "PROVIDER_API_KEY_REQUIRED")
            account.provider_base_url, account.provider_model = base_url, payload.model.strip()
            account.provider_reranker_model = (payload.reranker_model or payload.model).strip()
            account.provider_version += 1
        return self.model_status(identifier)

    def admit_in(self, session, identifier, request_id, payload):
        """Runs inside access admission's transaction, so both quota checks commit together."""
        account = session.scalar(select(TenantAccount).where(TenantAccount.id == identifier).with_for_update())
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
            separators=(",", ":"), default=str).encode()).hexdigest()
        previous = session.scalar(select(TenantAdmission).where(TenantAdmission.tenant_id == identifier,
            TenantAdmission.request_id == request_id))
        if previous:
            if previous.payload_hash != digest:
                raise HTTPException(409, "IDEMPOTENCY_CONFLICT")
            values = json.loads(self.unseal(previous.provider_snapshot))
        else:
            values = self.provider_values(account)
            if values["source"] == "system":
                if not values["api_key"] or not values["base_url"] or not values["model"]:
                    raise HTTPException(503, "MODEL_CONFIGURATION_INCOMPLETE")
                if account.trial_used >= self.settings.tenant_trial_limit:
                    raise HTTPException(429, "SYSTEM_TRIAL_EXHAUSTED")
                account.trial_used += 1
            session.add(TenantAdmission(tenant_id=identifier, request_id=request_id, payload_hash=digest,
                source=values["source"], provider_snapshot=self.seal(json.dumps(values))))
        return {"source": values["source"], "settings": self.runtime_settings(values)}

    def admit(self, request, request_id, payload):
        hook = None
        access_request_id = request_id
        if self.settings.multi_tenant_enabled:
            identifier = self.require(request)
            # Reject cross-account conversations/requery sources before charging.
            with self.database.session() as session:
                receipt = session.scalar(select(AgentTurn).where(AgentTurn.user_id == identifier,
                    AgentTurn.request_id == request_id))
                conversation_id = payload.get("conversation_id")
                if conversation_id:
                    row = session.get(AgentConversation, conversation_id)
                    if not row or row.user_id != identifier:
                        raise KeyError("CONVERSATION_NOT_FOUND")
                    if not receipt and payload.get("expected_version") is not None and row.version != payload["expected_version"]:
                        raise ValueError("CONVERSATION_VERSION_CONFLICT")
                    if not receipt:
                        running = session.scalar(select(AgentTurn.id).where(AgentTurn.conversation_id == row.id,
                            AgentTurn.user_id == identifier, AgentTurn.status == "RUNNING",
                            AgentTurn.created_at >= now_utc() - timedelta(seconds=self.settings.query_deadline_seconds + 10)))
                        if running:
                            raise ValueError("QUERY_IN_PROGRESS")
                elif not receipt and payload.get("expected_version") not in {None, 0}:
                    raise ValueError("CONVERSATION_VERSION_CONFLICT")
                source_id = payload.get("requery_of_run_id")
                if source_id:
                    row = session.get(AgentTurn, source_id)
                    if not row or row.user_id != identifier:
                        raise KeyError("RUN_NOT_FOUND")
            hook = lambda session: self.admit_in(session, identifier, request_id, payload)
            # The device admission scope outlives a browser account switch.
            # Scope its receipt to the authenticated account as well, so another
            # account's request ID cannot bypass device/IP policy or telemetry.
            access_request_id = hashlib.sha256((identifier + ":" + request_id).encode()).hexdigest()
        return self.access.admit(request, access_request_id, payload, admission_hook=hook)


class TenantMiddleware:
    def __init__(self, app, service):
        self.app, self.service = app, service

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope, receive)
        path = scope.get("path", "").removeprefix(scope.get("root_path", "").rstrip("/"))
        identifier = await asyncio.to_thread(self.service.authenticate, request)
        request.state.tenant_id = identifier
        token = current_tenant.set(identifier if self.service.settings.multi_tenant_enabled else None)
        provider_token = current_provider.set(None)
        try:
            if self.service.settings.multi_tenant_enabled and path.startswith("/api/"):
                public = path.startswith("/api/account") or path.startswith("/api/admin/") or path in {
                    "/api/health", "/api/topics", "/api/workspace/version"}
                privileged = path.startswith(("/api/ingest", "/api/task-annotations", "/api/corpus/")) and (
                        request.method not in {"GET", "HEAD"} or path.startswith("/api/ingest"))
                if privileged:
                    self.service.access.authenticate(request, mutate=request.method not in {"GET", "HEAD"})
                if not public and not identifier and not privileged:
                    return await access_error(401, "TENANT_AUTH_REQUIRED")(scope, receive, send)
                if not public and request.method not in {"GET", "HEAD", "OPTIONS"}:
                    self.service.check_origin(request)
            async def private_send(message):
                if identifier and path.startswith("/api/") and message["type"] == "http.response.start":
                    headers = list(message["headers"])
                    cache = ", ".join(value.decode("latin-1") for name, value in headers if name.lower() == b"cache-control")
                    headers = [(name, value) for name, value in headers if name.lower() != b"cache-control"]
                    directives = "private, no-store" + (", no-transform" if "no-transform" in cache.lower() else "")
                    message["headers"] = headers + [(b"cache-control", directives.encode())]
                await send(message)
            await self.app(scope, receive, private_send)
        except HTTPException as error:
            response = access_error(error.status_code, error.detail, message=getattr(error, "message", None),
                retryable=False if error.detail == "SYSTEM_TRIAL_EXHAUSTED" else None)
            response.headers.update(error.headers or {})
            await response(scope, receive, send)
        finally:
            current_tenant.reset(token)
            current_provider.reset(provider_token)


def register_tenants(app, database, settings, access):
    service = TenantService(database, settings, access)
    app.state.tenant_service = service
    app.add_middleware(TenantMiddleware, service=service)

    @app.get("/api/account")
    def account_status(request: Request):
        return JSONResponse({"data": service.status(getattr(request.state, "tenant_id", None))}, headers={"Cache-Control": "no-store"})

    @app.post("/api/account/register")
    def register_account(request: Request, payload: AccountCredentials):
        try:
            return service.login(request, payload, register=True)
        except IntegrityError as error:
            raise HTTPException(409, "TENANT_USERNAME_TAKEN") from error

    @app.post("/api/account/login")
    def login_account(request: Request, payload: AccountCredentials):
        return service.login(request, payload)

    @app.post("/api/account/logout")
    def logout_account(request: Request):
        service.check_origin(request, header=True)
        raw = verify_cookie(service.cookie_key, "tenant", request.cookies.get(TENANT_COOKIE, ""))
        if raw:
            with access.transaction() as session:
                session.execute(delete(TenantSession).where(TenantSession.token_hash == hashlib.sha256(raw.encode()).hexdigest()))
        response = JSONResponse({"data": service.status()}, headers={"Cache-Control": "no-store"})
        response.delete_cookie(TENANT_COOKIE, path=request.scope.get("root_path", "").rstrip("/") or "/")
        return response

    @app.get("/api/account/model")
    def model_config(request: Request):
        return JSONResponse({"data": service.model_status(service.require(request))}, headers={"Cache-Control": "no-store"})

    @app.patch("/api/account/model")
    def model_update(request: Request, payload: ProviderUpdate):
        service.check_origin(request, header=True)
        return JSONResponse({"data": service.update_model(service.require(request), payload)}, headers={"Cache-Control": "no-store"})

    @app.delete("/api/account/model")
    def model_reset(request: Request, expected_version: int = Query(ge=0)):
        service.check_origin(request, header=True)
        identifier = service.require(request)
        with access.transaction() as session:
            account = session.scalar(select(TenantAccount).where(TenantAccount.id == identifier).with_for_update())
            if account.provider_version != expected_version:
                raise HTTPException(409, "PROVIDER_VERSION_CONFLICT")
            account.provider_key = account.provider_key_hint = account.provider_base_url = None
            account.provider_model = account.provider_reranker_model = None
            account.provider_version += 1
        return JSONResponse({"data": service.model_status(identifier)}, headers={"Cache-Control": "no-store"})

    return service
