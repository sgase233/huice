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
from .identity import LoginLimiter
from .integrations.accounts import AccountsRepository
from .integrations.cache import build_cache
from .integrations.llm import build_llm_client
from .integrations.obs import build_object_store
from .integrations.storage import PolicyRepository
from .logging_setup import configure_logging
from .routes import admin_policies, assessment_runs, auth, enterprises, health, policies

API_PREFIX = "/api/v1"

#: ``X-CSRF-Token`` 是 E1 会话写请求的必需头（#54 D1.6）：跨源开发时浏览器要先预检，
#: 不列进来就永远发不出去——表现为 403 ``CSRF_REQUIRED``，看起来像后端判错。
ALLOWED_REQUEST_HEADERS = (
    "Content-Type",
    "Authorization",
    "If-Match",
    "Idempotency-Key",
    "X-CSRF-Token",
)
EXPOSED_RESPONSE_HEADERS = ("ETag", "X-Trace-Id", "Content-Disposition")
ALLOWED_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings.from_env()
    # uvicorn 默认只配置 uvicorn.* 的 logger，应用里的 logger.info 会落在 WARNING 之下
    # 而被丢弃；这里显式让 app.* / huice.* 以 INFO 输出到 stderr（systemd 转 journal）。
    configure_logging()
    database = Database(resolved.database_url)
    repository = PolicyRepository(database, cursor_secret=_cursor_secret(resolved))
    accounts = AccountsRepository(database)
    object_store = build_object_store(resolved)
    llm_client = build_llm_client(resolved)
    evaluation_cache = build_cache(resolved)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        database.close()
        close_store = getattr(object_store, "close", None)
        if callable(close_store):
            close_store()
        evaluation_cache.close()

    app = FastAPI(
        title="惠策协同 HTTP API",
        version=APP_VERSION,
        description=(
            "契约见 contracts/openapi.yaml；本服务实现契约全部 16 个端点（API-01—API-16），"
            "以及 E1 的账号与会话接口 AUTH-01—AUTH-05（契约草案 contracts/openapi.e1-draft.yaml）。"
            "其中 API-13 报告与 API-14 轨迹只读既有历史，不重新求值。"
        ),
        lifespan=lifespan,
    )

    app.state.settings = resolved
    app.state.db = database
    app.state.repository = repository
    app.state.accounts = accounts
    # 进程内限流：单 worker 部署够用，重启清零是接受的代价（#54 D1.7）。
    app.state.login_limiter = LoginLimiter()
    app.state.object_store = object_store
    app.state.llm = llm_client
    app.state.cache = evaluation_cache

    register_error_handlers(app)

    api = APIRouter()
    api.include_router(health.router)
    api.include_router(auth.router)
    api.include_router(policies.router)
    api.include_router(enterprises.router)
    api.include_router(assessment_runs.router)
    api.include_router(assessment_runs.admin_router)
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
        "description": "restricted 模式要求经签名的会话 cookie（v2.<过期秒>.<sid>.<HMAC>；E1 前签发的 v1 仍验签但只读），用独立机密 DEMO_SESSION_SECRET 签发（infra/deploy/issue-session.py），与 ADMIN_TOKEN 相互独立；local 模式免校验。",
    },
    "AdminBearer": {
        "type": "http",
        "scheme": "bearer",
        "description": "仅运行时使用的 ADMIN_TOKEN，绝不进入前端构建变量。",
    },
    "UserSession": {
        "type": "apiKey",
        "in": "cookie",
        "name": "huice_session",
        "description": "登录会话（HttpOnly、可撤销、只存令牌摘要）。E1 起是唯一可写的身份；运维 Bearer 只读。",
    },
}

#: 不参与身份判定的路径：门禁豁免，且不要求任何凭证。
_PUBLIC_AUTH_PATHS = {
    f"{API_PREFIX}/auth/register",
    f"{API_PREFIX}/auth/login",
    f"{API_PREFIX}/auth/logout",
}
_SESSION_ONLY_PATHS = {
    f"{API_PREFIX}/auth/me",
    f"{API_PREFIX}/auth/password",
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
                tags = operation.get("tags") or []
                if path == f"{API_PREFIX}/health":
                    operation["security"] = []
                elif path in _PUBLIC_AUTH_PATHS:
                    # 注册/登录/登出不靠已有凭证；它们自己的校验是 Origin（与登出的 CSRF）。
                    operation["security"] = []
                elif path in _SESSION_ONLY_PATHS:
                    operation["security"] = [{"UserSession": []}]
                elif "admin" in tags:
                    # 后台接口（含 API-14 轨迹）在演示会话之外再加管理 Bearer。
                    operation["security"] = [{"DemoSession": [], "AdminBearer": []}]
                else:
                    # 公开接口仍受演示会话约束：网关拦一遍、后端再验一遍签名与有效期。
                    operation["security"] = [{"DemoSession": []}]
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
