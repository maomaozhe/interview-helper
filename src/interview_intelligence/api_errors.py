"""HTTP error responses shared by the API's domain and provider endpoints."""

from __future__ import annotations

from collections.abc import Callable

import httpx
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from openai import APIError, APITimeoutError


def register_error_handlers(app: FastAPI, request_id: Callable[[Request], str]) -> None:
    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        if "/api/admin/" in request.url.path or "/api/account" in request.url.path:
            # Pydantic's error input can contain the administrator login token.
            return JSONResponse(status_code=422, content={"error": {
                "code": "VALIDATION_ERROR", "message": "invalid credentials or configuration request", "retryable": False}})
        return JSONResponse(status_code=422, content={"error": {
            "code": "VALIDATION_ERROR", "message": "invalid request", "details": {"errors": jsonable_encoder(error.errors(), custom_encoder={ValueError: str})},
            "retryable": False}, "request_id": request_id(request)})

    @app.exception_handler(KeyError)
    async def missing_error(request: Request, error: KeyError):
        return JSONResponse(status_code=404, content={"error": {
            "code": "NOT_FOUND", "message": str(error), "retryable": False},
            "request_id": request_id(request)})

    @app.exception_handler(ValueError)
    async def semantic_error(request: Request, error: ValueError):
        message = str(error)
        code = message.split(":", 1)[0] if message.isupper() or ":" in message else "INVALID_OPERATION"
        status = 409 if code in {"IDEMPOTENCY_CONFLICT", "USER_STATE_VERSION_CONFLICT", "SNAPSHOT_CHANGED",
                               "CONVERSATION_VERSION_CONFLICT", "QUERY_IN_PROGRESS","PREFERENCE_VERSION_CONFLICT",
                               "REQUERY_SOURCE_NOT_READY"} else 400
        if code in {"INDEX_NOT_READY", "EMBEDDING_NOT_READY", "RERANKER_NOT_READY",
                    "MODEL_CONFIGURATION_INCOMPLETE", "MODEL_NOT_READY", "SOURCE_SNAPSHOT_MISSING",
                    "STREAM_INCOMPLETE", "MODEL_OUTPUT_TRUNCATED", "QUERY_DEADLINE_EXCEEDED", "LEXICAL_FACET_RETRIEVAL_FAILED",
                    "QUERY_PLAN_MISSING", "QUERY_MODEL_BUDGET_EXCEEDED", "QUERY_RELEVANCE_UNAVAILABLE", "SEMANTIC_SCOPE_INCOMPLETE"}:
            status = 503
        if code == "QUERY_RELEVANCE_UNAVAILABLE":
            message = "本次语义核验未完成，结果未作为成功查询保存，请重试。"
        if code == "SEMANTIC_SCOPE_INCOMPLETE":
            message = "当前范围的全量算法核验尚未完成，暂时无法提供完整列表。"
        if code == "MODEL_PROVIDER_UNAVAILABLE":
            status, message = 503, "模型请求未完成，请检查自己的模型配置。"
        return JSONResponse(status_code=status, content={"error": {
            "code": code, "message": message, "retryable": status == 503},
            "request_id": request_id(request)})

    @app.exception_handler(APIError)
    async def provider_error(request: Request, error: APIError):
        code = "MODEL_PROVIDER_TIMEOUT" if isinstance(error, APITimeoutError) else "MODEL_PROVIDER_UNAVAILABLE"
        return JSONResponse(status_code=503, content={"error": {
            "code": code, "message": "模型请求未完成，请稍后重试。",
            "retryable": getattr(error, "status_code", None) not in {400, 401, 403, 404, 422}},
            "request_id": request_id(request)})

    @app.exception_handler(httpx.HTTPError)
    async def http_provider_error(request: Request, error: httpx.HTTPError):
        return JSONResponse(status_code=503, content={"error": {
            "code": "MODEL_PROVIDER_TIMEOUT" if isinstance(error, httpx.TimeoutException) else "MODEL_PROVIDER_UNAVAILABLE",
            "message": "模型或 Agent 服务暂时不可用。", "retryable": True}, "request_id": request_id(request)})
