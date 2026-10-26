"""应用组装：中间件顺序、路由挂载、异常处理与生命周期。

``app`` 采用惰性属性（PEP 562），导入本模块不会读取环境变量；只有在真正获取
``app.main:app`` 时才加载配置，因此测试可以自由地注入 ``Settings``。
"""

from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

from .config import APP_VERSION, Settings
from .db import Database
from .errors import TraceIdMiddleware, register_error_handlers
from .integrations.llm import build_llm_client
from .integrations.obs import build_object_store
from .integrations.storage import PolicyRepository
from .routes import admin_policies, health

API_PREFIX = "/api/v1"

ALLOWED_REQUEST_HEADERS = ("Content-Type", "Authorization", "If-Match", "Idempotency-Key")
EXPOSED_RESPONSE_HEADERS = ("ETag", "X-Trace-Id", "Content-Disposition")
ALLOWED_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings.from_env()
    database = Database(resolved.database_url)
    repository = PolicyRepository(database, cursor_secret=_cursor_secret(resolved))
    object_store = build_object_store(resolved)
    llm_client = build_llm_client(resolved)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        database.close()
        close_store = getattr(object_store, "close", None)
        if callable(close_store):
            close_store()

    app = FastAPI(
        title="惠策协同 HTTP API",
        version=APP_VERSION,
        description="契约见 contracts/openapi.yaml；本服务当前实现 API-01/04/15/16。",
        lifespan=lifespan,
    )

    app.state.settings = resolved
    app.state.db = database
    app.state.repository = repository
    app.state.object_store = object_store
    app.state.llm = llm_client

    register_error_handlers(app)

    api = APIRouter()
    api.include_router(health.router)
    api.include_router(admin_policies.router)
    app.include_router(api, prefix=API_PREFIX)

    # 先加 TraceId、后加 CORS：CORS 位于最外层，错误响应也能带上跨域头。
    app.add_middleware(TraceIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved.cors_origins),
        allow_credentials=True,
        allow_methods=list(ALLOWED_METHODS),
        allow_headers=list(ALLOWED_REQUEST_HEADERS),
        expose_headers=list(EXPOSED_RESPONSE_HEADERS),
        max_age=600,
    )

    _install_openapi(app)
    return app


SECURITY_SCHEMES = {
    "DemoSession": {
        "type": "apiKey",
        "in": "cookie",
        "name": "huice_demo_session",
        "description": "restricted 模式要求经签名的会话 cookie（v1.<过期秒>.<HMAC>），用独立机密 DEMO_SESSION_SECRET 签发（infra/deploy/issue-session.py），与 ADMIN_TOKEN 相互独立；local 模式免校验。",
    },
    "AdminBearer": {
        "type": "http",
        "scheme": "bearer",
        "description": "仅运行时使用的 ADMIN_TOKEN，绝不进入前端构建变量。",
    },
}


def _install_openapi(app: FastAPI) -> None:
    """Serve an OpenAPI document that matches the security part of the contract."""

    def openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        schema.setdefault("components", {})["securitySchemes"] = SECURITY_SCHEMES
        for path, operations in schema.get("paths", {}).items():
            for operation in operations.values():
                if path.startswith(f"{API_PREFIX}/admin/"):
                    operation["security"] = [{"DemoSession": [], "AdminBearer": []}]
                else:
                    operation["security"] = []
        app.openapi_schema = schema
        return schema

    app.openapi = openapi  # type: ignore[method-assign]


def _cursor_secret(settings: Settings) -> str:
    """Derive the pagination-cursor key from ADMIN_TOKEN without storing a new secret."""
    return hashlib.sha256(f"huice-cursor:{settings.admin_token}".encode("utf-8")).hexdigest()


def __getattr__(name: str) -> Any:
    if name == "app":
        application = create_app()
        globals()["app"] = application
        return application
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
