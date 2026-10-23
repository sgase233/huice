"""Uniform API error envelope and request tracing.

Every response — success or failure — carries ``X-Trace-Id``; error bodies use
the same value in ``traceId``. Internal details (stack traces, DSNs, SQL) never
reach the client.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Mapping

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("huice.api")

TRACE_HEADER = "X-Trace-Id"


class ApiProblem(Exception):
    """A business error that maps directly onto the ApiError envelope."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = dict(details or {})
        self.headers = dict(headers or {})


class StorageUnavailable(Exception):
    """Raised when the database cannot be reached for a request."""


def trace_id_of(request: Request) -> str:
    trace_id = getattr(request.state, "trace_id", None)
    return trace_id if isinstance(trace_id, str) else str(uuid.uuid4())


def error_response(
    status_code: int,
    code: str,
    message: str,
    details: Mapping[str, Any] | None = None,
    trace_id: str | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    resolved_trace = trace_id or str(uuid.uuid4())
    payload = {
        "error": {"code": code, "message": message, "details": dict(details or {})},
        "traceId": resolved_trace,
    }
    response = JSONResponse(status_code=status_code, content=payload)
    response.headers[TRACE_HEADER] = resolved_trace
    for name, value in (headers or {}).items():
        response.headers[name] = value
    return response


class TraceIdMiddleware:
    """Assign a trace UUID, expose it as a header, and sanitise unexpected errors."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        trace_id = str(uuid.uuid4())
        scope.setdefault("state", {})["trace_id"] = trace_id
        response_started = False

        async def send_with_trace(message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                MutableHeaders(scope=message)[TRACE_HEADER] = trace_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_trace)
        except Exception:
            logger.exception("未处理的请求异常 trace_id=%s path=%s", trace_id, scope.get("path"))
            if response_started:
                raise
            response = error_response(
                500,
                "INTERNAL_ERROR",
                "服务内部错误，请稍后重试。",
                trace_id=trace_id,
            )
            await response(scope, receive, send)


async def api_problem_handler(request: Request, exc: ApiProblem) -> JSONResponse:
    return error_response(
        exc.status_code,
        exc.code,
        exc.message,
        exc.details,
        trace_id_of(request),
        exc.headers,
    )


async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = exc.errors()
    locations = {str(error.get("loc", ("",))[0]) for error in errors}
    is_query_or_path = bool(locations) and locations <= {"query", "path"}

    field_errors = [
        {
            "field": ".".join(str(part) for part in error.get("loc", ())[1:]) or "body",
            "message": str(error.get("msg", "取值不合法")),
        }
        for error in errors
    ]

    if is_query_or_path:
        return error_response(
            400,
            "INVALID_QUERY",
            "请求参数不合法，请检查查询参数。",
            {"fieldErrors": field_errors},
            trace_id_of(request),
        )
    return error_response(
        422,
        "VALIDATION_ERROR",
        "请求内容不合法，请检查后重试。",
        {"fieldErrors": field_errors},
        trace_id_of(request),
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    codes = {
        400: ("INVALID_REQUEST", "请求格式不合法。"),
        401: ("UNAUTHENTICATED", "需要有效身份凭证。"),
        403: ("FORBIDDEN", "没有访问该资源的权限。"),
        404: ("NOT_FOUND", "请求的资源不存在。"),
        405: ("METHOD_NOT_ALLOWED", "请求方法不被允许。"),
        413: ("FILE_TOO_LARGE", "上传内容超过大小限制。"),
    }
    code, message = codes.get(exc.status_code, ("INVALID_REQUEST", "请求无法完成。"))
    return error_response(exc.status_code, code, message, trace_id=trace_id_of(request))


async def storage_unavailable_handler(request: Request, exc: StorageUnavailable) -> JSONResponse:
    return error_response(
        503,
        "DEPENDENCY_UNAVAILABLE",
        "数据服务暂不可用，请稍后重试。",
        trace_id=trace_id_of(request),
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiProblem, api_problem_handler)
    app.add_exception_handler(StorageUnavailable, storage_unavailable_handler)
    app.add_exception_handler(RequestValidationError, validation_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
