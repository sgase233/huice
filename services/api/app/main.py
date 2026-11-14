"""应用组装：中间件顺序、路由挂载、异常处理与生命周期。

``app`` 采用惰性属性（PEP 562），导入本模块不会读取环境变量；只有在真正获取
``app.main:app`` 时才加载配置，因此测试可以自由地注入 ``Settings``。
"""

from __future__ import annotations

import copy
import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

from .config import APP_VERSION, Settings, cursor_secret
from .db import Database
from .errors import TraceIdMiddleware, register_error_handlers
from .identity import LoginLimiter
from .integrations.accounts import AccountsRepository
from .integrations.cache import build_cache
from .integrations.discovery import DiscoveryRepository
from .integrations.llm import build_llm_client
from .integrations.obs import build_object_store
from .integrations.storage import PolicyRepository
from .logging_setup import configure_logging
from .routes import (
    admin_policies,
    assessment_runs,
    auth,
    discovery,
    enterprises,
    health,
    policies,
)

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


logger = logging.getLogger("huice.api")


async def _run_interrupt_scan(repository, settings) -> None:
    """D5 的中断扫描：启动一次 + 每 ``RUN_INTERRUPT_SCAN_SECONDS`` 一次。

    判据在仓储里（超时 + 锁未被持有 + 原连接已断开，三者缺一不可）。这里只管节奏：
    串行、单进程、不并发，失败只记告警不退出——一次扫不动不该拖垮服务。
    """

    mark = getattr(repository, "mark_interrupted_runs", None)
    if mark is None:  # 测试替身没有这条能力
        return
    while True:
        try:
            await asyncio.to_thread(
                mark, max_duration_seconds=settings.run_max_duration_seconds
            )
        except Exception:  # noqa: BLE001 - 扫描失败不能影响请求处理
            logger.warning("中断扫描失败，下一轮再试", exc_info=True)
        await asyncio.sleep(settings.run_interrupt_scan_seconds)


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings.from_env()
    # uvicorn 默认只配置 uvicorn.* 的 logger，应用里的 logger.info 会落在 WARNING 之下
    # 而被丢弃；这里显式让 app.* / huice.* 以 INFO 输出到 stderr（systemd 转 journal）。
    configure_logging()
    database = Database(resolved.database_url)
    repository = PolicyRepository(
        database,
        cursor_secret=_cursor_secret(resolved),
        require_completed_status=resolved.run_lifecycle_contract_enabled,
    )
    discovery_repository = DiscoveryRepository(database, cursor_secret=_cursor_secret(resolved))
    accounts = AccountsRepository(database)
    object_store = build_object_store(resolved)
    llm_client = build_llm_client(resolved)
    evaluation_cache = build_cache(resolved)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # E3 B2：启动扫一次残留的 running 行，之后按 RUN_INTERRUPT_SCAN_SECONDS 周期扫。
        # 只扫一次会让「重启时还没超时、之后才超时」的行永远停在 running。
        # 默认关闭：interrupted 是 RunStatus 的新取值，契约要求与前端类型同窗升级，
        # 不允许后端单边先行；与 1.3.0-rc.1 同窗发布时再打开。
        # 运行采集的覆盖起点：只在为 NULL 时写，重启/并发启动都不会挪动它。
        # 与 B3 的审核事件起点按列幂等，先跑哪个都成立。
        try:
            await asyncio.to_thread(repository.mark_run_lifecycle_start)
        except Exception:  # noqa: BLE001 - 库未迁移到 012 时不能挡住启动
            logger.warning("写入运行采集起点失败，下一轮重启再试", exc_info=True)

        scan_task = (
            asyncio.create_task(_run_interrupt_scan(repository, resolved))
            if resolved.run_lifecycle_contract_enabled
            else None
        )
        try:
            yield
        finally:
            if scan_task is not None:
                scan_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await scan_task
        database.close()
        close_store = getattr(object_store, "close", None)
        if callable(close_store):
            close_store()
        evaluation_cache.close()

    app = FastAPI(
        title="惠策协同 HTTP API",
        version=APP_VERSION,
        description=(
            "契约见 contracts/openapi.yaml；本服务实现契约全部 23 个操作：API-01—API-16，"
            "以及 E1 的 AUTH-01—AUTH-07（账号与会话 5 个、按身份与归属过滤的列表 2 个）。"
            "其中 API-13 报告与 API-14 轨迹只读既有历史，不重新求值。"
            "另有 E2（#47）的 8 个发现/候选操作已经在跑、但只在 contracts/openapi.e2-draft.yaml "
            "里：草案评审通过并并入契约之前，它们不构成对外承诺，前端类型也不据此生成。"
        ),
        lifespan=lifespan,
    )

    app.state.settings = resolved
    app.state.db = database
    app.state.repository = repository
    # 发现候选是独立暂存区：仓储分开，避免「候选写进政策库」在代码里变成顺手的事。
    app.state.discovery = discovery_repository
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
    # 登出单独挂：它豁免演示门禁，不能跟着 ``route_class=GatedRoute`` 的 router 走。
    api.include_router(auth.logout_router)
    api.include_router(policies.router)
    api.include_router(enterprises.router)
    api.include_router(assessment_runs.router)
    api.include_router(assessment_runs.admin_router)
    api.include_router(admin_policies.router)
    api.include_router(discovery.router)
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
#: 轨迹既给本人、也给审核员会话与运维 Bearer（``require_identity`` + 归属判定）。
_ANY_IDENTITY_PATH = f"{API_PREFIX}/assessment-runs/{{runId}}/trace"

#: 发布出来的 ``security`` 与 ``app/identity.py`` 的实际判定一一对应，按「或」组织：
#: 数组里每一项是一个可接受的凭证组合。E1 之后 Bearer 只读，所以写操作只剩会话；
#: 业务接口不再接受 Bearer（``require_public_identity`` 对它回 403）。
_BUSINESS_SECURITY = [{"UserSession": []}, {"DemoSession": []}]
_ANY_IDENTITY_SECURITY = [{"UserSession": []}, {"AdminBearer": []}, {"DemoSession": []}]
_REVIEWER_READ_SECURITY = [{"UserSession": []}, {"AdminBearer": []}]
_REVIEWER_WRITE_SECURITY = [{"UserSession": []}]

#: ``x-e1-gate``：标准 ``security`` 只能表达「要哪些凭证」，表达不了「restricted 部署
#: 还要先过外层 demo 门禁」，而门禁用的是另一个 cookie、在身份判定之前短路（PR #61
#: 最新复审 P1）。所以每个操作另发一个 ``x-e1-gate``，取值按部署模式给。
_GATE_EXEMPT_PATHS = {f"{API_PREFIX}/health", f"{API_PREFIX}/auth/logout"}
_GATE_REQUIRED_NOTE = (
    "restricted 部署下先过 demo 门禁（同一个 huice_demo_session cookie）："
    "缺失、破损或过期一律 403 DEMO_SESSION_REQUIRED。门禁在解析请求体之前执行，"
    "先于字段校验也先于身份判定——缺 cookie 时请求体不合法同样是 403，不是 422。"
)
_GATE_EXEMPT_NOTE = (
    "门禁豁免（仅 GET /health 与 POST /auth/logout 两项）；"
    "身份仍按 security 判定。"
)
_GATE_MODEL = {
    "standard-security-limitation": (
        "标准 OpenAPI 3.1 的 security 只表达「这次请求需要哪些凭证」（列表内 OR、对象内 AND）；"
        "它无法表达随部署模式变化的外层门禁，因此门禁逐操作写在 x-e1-gate。"
    ),
    "evaluation-order": "门禁 → 请求体解析与字段校验 → 身份 → 角色 → 对象归属 → CSRF/Origin",
    "gate-before-validation": (
        "门禁跑在请求体解析之前（GatedRoute 包装 get_route_handler）。restricted 下缺 cookie 的请求"
        "不会因为请求体不合法先拿到 422：缺字段、错类型、空体、非 JSON 一律 403 DEMO_SESSION_REQUIRED；"
        "local 模式没有门禁，同样的请求才是 422。"
    ),
    "modes": {
        "local": "无门禁；身份仍按 security 判定（无效 demo cookie 在同一层回 401 DEMO_SESSION_INVALID）。",
        "restricted": (
            "要求签名有效的 huice_demo_session（DemoSession）；缺失、格式/签名不对、已过期都回同一个 "
            "403 DEMO_SESSION_REQUIRED，且先于身份判定——连 security 为空的注册/登录也不例外。"
        ),
    },
    "exempt": ["GET /health", "POST /auth/logout"],
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
        schema["x-e1-gate-model"] = copy.deepcopy(_GATE_MODEL)
        for path, operations in schema.get("paths", {}).items():
            for method, operation in operations.items():
                tags = operation.get("tags") or []
                operation["security"] = _security_for(path, method, tags)
                operation["x-e1-gate"] = _gate_for(path)
        app.openapi_schema = schema
        return schema

    app.openapi = openapi  # type: ignore[method-assign]


def _gate_for(path: str) -> dict[str, str]:
    """一个操作的外层门禁：门禁只按部署模式与路径决定，与身份无关。

    ``security: []`` 的注册/登录在 restricted 下同样要过门禁——它们用的是
    ``GatedRoute``，在**解析请求体之前**就调 ``require_demo_session``；只把 ``security``
    标成「无凭证要求」会让读者以为这两个接口在任何部署下都能直接调。
    """

    if path in _GATE_EXEMPT_PATHS:
        return {"local": "none", "restricted": "exempt", "note": _GATE_EXEMPT_NOTE}
    return {"local": "none", "restricted": "DemoSession", "note": _GATE_REQUIRED_NOTE}


def _security_for(path: str, method: str, tags: list[str]) -> list[dict[str, list[str]]]:
    """一个操作的 ``security``：与路由里真正调用的 ``require_*`` 逐条对齐。

    后台的读与写必须分开写——E1 之后运维 Bearer 只能读，把写操作也标成
    「DemoSession + AdminBearer」会让契约承诺一个后端一定会 403 的入口。
    """

    if path == f"{API_PREFIX}/health" or path in _PUBLIC_AUTH_PATHS:
        # 注册/登录/登出不靠已有凭证；它们自己的校验是 Origin（与登出的 CSRF）。
        return []
    if path in _SESSION_ONLY_PATHS:
        return [{"UserSession": []}]
    if path == _ANY_IDENTITY_PATH:
        return copy.deepcopy(_ANY_IDENTITY_SECURITY)
    if "admin" in tags:
        if method.lower() in {"get", "head"}:
            return copy.deepcopy(_REVIEWER_READ_SECURITY)
        return copy.deepcopy(_REVIEWER_WRITE_SECURITY)
    return copy.deepcopy(_BUSINESS_SECURITY)


def _cursor_secret(settings: Settings) -> str:
    """Derive the pagination-cursor key from ADMIN_TOKEN without storing a new secret."""

    return cursor_secret(settings)


def __getattr__(name: str) -> Any:
    if name == "app":
        application = create_app()
        globals()["app"] = application
        return application
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
