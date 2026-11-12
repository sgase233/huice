"""E1 身份判定：门禁之后选出**唯一**身份，再单独判角色与归属。

判定顺序固定，来自 #55 draft.4 的 ``evaluation-order``：

1. **门禁**（``security.require_demo_session``）：restricted 部署的外层 demo cookie；
   豁免只有 ``GET /health`` 与 ``POST /auth/logout``。
2. **身份**（本模块）：按 #54 D1.5 的真值表在「会话 / 运维 Bearer / demo」中选一个。
   核心规则是**只有「未提供」才轮到下一个；「提供了但无效」一律 401**，
   绝不静默降级——否则会从失效会话掉到 demo，把合成数据当成用户自己的数据。
3. **角色**：凭证有效但角色不够 → 403 ``ROLE_FORBIDDEN``。
4. **对象归属**：角色够但对象不属于本人 → 404，与「不存在」同一个对象专属码。
5. **CSRF / Origin**：带会话的写请求要 ``X-CSRF-Token``；注册/登录只校验 Origin；
   demo 写请求只校验 Origin（+ Referer）；Bearer 不校验。

``security`` 只表达身份，不表达角色与归属；本模块两者都在，#55 draft.4 的
``x-e1-impact`` 逐端点写明了每个操作属于哪一列。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

from fastapi import Request, Response

from . import auth
from .demo_quota import DemoQuota
from .errors import ApiProblem
from .security import (
    DEMO_SESSION_COOKIE,
    demo_session_claims,
)

IdentityKind = Literal["user", "bearer", "demo"]
Role = Literal["enterprise", "reviewer"]


@dataclass(frozen=True, slots=True)
class Identity:
    """一次请求的身份。``kind`` 以外的字段只在对应维度上有值。"""

    kind: IdentityKind
    user_id: UUID | None = None
    username: str | None = None
    role: Role | None = None
    enterprise_id: UUID | None = None
    session_id: UUID | None = None
    expires_at: datetime | None = None
    csrf_token: str | None = None
    demo_sid: str | None = None

    @property
    def is_user(self) -> bool:
        return self.kind == "user"

    @property
    def is_reviewer(self) -> bool:
        return self.kind == "user" and self.role == "reviewer"

    @property
    def is_demo(self) -> bool:
        return self.kind == "demo"

    @property
    def is_legacy_demo(self) -> bool:
        """E1 之前签发的 demo cookie（没有 ``sid``）：只读，不能建样本。"""

        return self.kind == "demo" and self.demo_sid is None


# --------------------------------------------------------------- 身份判定


def resolve_identity(request: Request, *, ignore_session: bool = False) -> Identity | None:
    """按真值表解析身份；没有提供任何凭证时返回 ``None``。

    ``ignore_session`` 只给注册与登录用：浏览器的 HttpOnly Cookie 删不掉，
    注册/登录不能因为「带了一个失效会话」就失败（#55 draft.4 的两处显式例外）。
    缓存写在 ``request.state.identity`` 上，一次请求只解析一次。
    """

    cached = getattr(request.state, "identity", None)
    if cached is not None and not ignore_session:
        return cached  # type: ignore[return-value]

    identity = _resolve(request, ignore_session=ignore_session)
    if not ignore_session:
        request.state.identity = identity
    return identity


def _resolve(request: Request, *, ignore_session: bool) -> Identity | None:
    settings = request.app.state.settings
    now = datetime.now(timezone.utc)

    session_token = request.cookies.get(auth.SESSION_COOKIE, "")
    if session_token and not ignore_session:
        return _user_identity(request, session_token, now)

    header = request.headers.get("authorization", "")
    if header:
        scheme, _, supplied = header.partition(" ")
        supplied = supplied.strip()
        if scheme.lower() != "bearer" or not supplied or supplied != settings.admin_token:
            # 提供了 Bearer 但不对：401 按 RFC 7235 带 WWW-Authenticate，且不降级到 demo。
            raise ApiProblem(
                401,
                "TOKEN_INVALID",
                "运维令牌无效。",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return Identity(kind="bearer")

    demo_value = request.cookies.get(DEMO_SESSION_COOKIE, "")
    if demo_value:
        claims = demo_session_claims(
            demo_value, settings.demo_session_secret, now=int(now.timestamp())
        )
        if claims is None:
            # restricted 下走不到这里：门禁已经用 403 拦下；这是 local 模式的分支。
            raise ApiProblem(401, "DEMO_SESSION_INVALID", "演示会话签名无效或已过期。")
        return Identity(
            kind="demo",
            demo_sid=claims.sid,
            expires_at=datetime.fromtimestamp(claims.expires_at, tz=timezone.utc),
        )

    return None


def _user_identity(request: Request, token: str, now: datetime) -> Identity:
    settings = request.app.state.settings
    accounts = request.app.state.accounts
    row = accounts.find_session(auth.digest(token))
    if row is None:
        raise _session_invalid("unknown")
    if row["revoked_at"] is not None:
        raise _session_invalid("revoked")
    if row["expires_at"] <= now:
        raise _session_invalid("expired")
    idle_deadline = row["last_seen_at"] + timedelta(minutes=settings.session_idle_minutes)
    if idle_deadline <= now:
        raise _session_invalid("expired")
    if row["user_status"] != "active":
        raise _session_invalid("revoked")
    accounts.touch_session(row["id"], now)
    return Identity(
        kind="user",
        user_id=row["user_id"],
        username=row["username"],
        role=row["role"],
        enterprise_id=row["enterprise_id"],
        session_id=row["id"],
        expires_at=row["expires_at"],
        csrf_token=auth.csrf_token_for(token),
    )


def session_is_live(row: dict, settings, now: datetime) -> bool:
    """会话行是否仍然有效：未撤销、未过绝对期限、未超过空闲期、账号未停用。

    登出与登录复用这一判定，避免「登录时撤销旧会话」与「身份判定」两处规则漂移。
    """

    if row.get("revoked_at") is not None:
        return False
    if row.get("user_status", "active") != "active":
        return False
    if row["expires_at"] <= now:
        return False
    return row["last_seen_at"] + timedelta(minutes=settings.session_idle_minutes) > now


def _session_invalid(reason: str) -> ApiProblem:
    return ApiProblem(
        401,
        "SESSION_INVALID",
        "会话已失效，请重新登录。",
        {"reason": reason},
    )


def require_identity(request: Request) -> Identity:
    """任何一类有效凭证都行；完全没有凭证 → 401。"""

    identity = resolve_identity(request)
    if identity is None:
        raise ApiProblem(
            401,
            "UNAUTHENTICATED",
            "需要有效身份凭证：登录后携带会话 Cookie，或使用受限运维令牌。",
        )
    return identity


def require_user(request: Request) -> Identity:
    """只接受登录会话；Bearer 与 demo 都不是「用户」。"""

    identity = resolve_identity(request)
    if identity is None:
        raise ApiProblem(401, "UNAUTHENTICATED", "需要登录后才能访问。")
    if identity.kind != "user":
        raise ApiProblem(403, "ROLE_FORBIDDEN", "该操作需要登录账号。")
    return identity


def require_public_identity(request: Request) -> Identity:
    """公开业务接口：会话（任意角色）或 demo 都行；运维 Bearer 在 E1 后读不到业务数据。

    票据仍然有效（它确实通过了身份判定），只是这个操作的角色不够——所以是 403 而不是 401。
    """

    identity = require_identity(request)
    if identity.kind == "bearer":
        raise ApiProblem(403, "ROLE_FORBIDDEN", "运维令牌只用于后台只读接口。")
    return identity


def require_reviewer(request: Request) -> Identity:
    """写操作：只接受审核员会话（Bearer 在 E1 后只读，不能再写）。"""

    identity = require_user(request)
    if identity.role != "reviewer":
        raise ApiProblem(403, "ROLE_FORBIDDEN", "该操作需要审核员账号。")
    return identity


def require_reviewer_or_bearer(request: Request) -> Identity:
    """后台读操作：审核员会话，或只读运维 Bearer；企业会话与 demo 一律 403。"""

    identity = require_identity(request)
    if identity.kind == "bearer" or identity.is_reviewer:
        return identity
    raise ApiProblem(403, "ROLE_FORBIDDEN", "该操作需要审核员账号或运维令牌。")


# ------------------------------------------------------------ Origin / CSRF


def require_origin(request: Request) -> None:
    """注册/登录：Origin 必须存在且与 ``PUBLIC_ORIGIN`` 同源，否则 403。"""

    settings = request.app.state.settings
    expected = settings.effective_public_origin
    supplied = request.headers.get("origin")
    if not expected or not supplied or supplied != expected:
        raise ApiProblem(
            403,
            "ORIGIN_MISMATCH",
            "请求来源与配置的站点不同源，已拒绝。",
        )


def require_csrf(request: Request, identity: Identity) -> None:
    """带会话的写请求必须带匹配的 ``X-CSRF-Token``；先判 Origin 再判令牌。"""

    if identity.kind != "user":
        return
    require_origin(request)
    supplied = request.headers.get(auth.CSRF_HEADER, "")
    expected = identity.csrf_token or ""
    if not supplied or not expected or supplied != expected:
        raise ApiProblem(
            403,
            "CSRF_REQUIRED",
            "写请求缺少或未通过 CSRF 校验，请刷新页面后重试。",
        )


def require_demo_write(request: Request) -> None:
    """demo 写请求：Origin 同源；带了 Referer 时它也必须同源。不要求 CSRF。"""

    settings = request.app.state.settings
    expected = settings.effective_public_origin
    origin = request.headers.get("origin")
    if not expected or origin != expected:
        raise ApiProblem(403, "ORIGIN_MISMATCH", "请求来源与配置的站点不同源，已拒绝。")
    referer = request.headers.get("referer")
    if referer and not referer.startswith(f"{expected}/") and referer != expected:
        raise ApiProblem(403, "ORIGIN_MISMATCH", "请求来源与配置的站点不同源，已拒绝。")


def require_writable_demo(request: Request, identity: Identity) -> None:
    """demo 的写操作：旧格式（无 ``sid``）只读，新格式再校验 Origin（+Referer）。"""

    if identity.is_legacy_demo:
        raise ApiProblem(
            403,
            "DEMO_SESSION_LEGACY",
            "演示会话版本过旧，不能创建或修改样本，请从演示入口重新进入。",
        )
    require_demo_write(request)


def demo_quota_claim(request: Request, identity: Identity) -> DemoQuota | None:
    """演示会话的建样配额参数；会话身份返回 ``None``（一个账号只能有一家企业）。

    **这里不占额**：名额与企业在仓储的同一个事务里创建，失败一起回滚。早先的
    「先占后建、失败再退」在进程退出或退额本身失败时仍会白扣一个名额（PR #61 复审 P2）。
    """

    if not identity.is_demo:
        return None
    assert identity.demo_sid is not None and identity.expires_at is not None
    return DemoQuota(
        sid_hash=auth.digest(identity.demo_sid),
        expires_at=identity.expires_at,
        limit=request.app.state.settings.demo_session_quota,
    )


# ------------------------------------------------------------- Cookie 工具


def session_cookie_attributes(settings) -> dict[str, object]:
    return {
        "httponly": True,
        "secure": settings.app_env != "local",
        "samesite": "lax",
        "path": auth.SESSION_PATH,
    }


def set_session_cookie(
    response: Response, token: str, settings, *, max_age: int
) -> None:
    response.set_cookie(
        auth.SESSION_COOKIE,
        token,
        max_age=max_age,
        **session_cookie_attributes(settings),
    )


def clear_session_cookie(response: Response, settings) -> None:
    response.set_cookie(
        auth.SESSION_COOKIE,
        "",
        max_age=0,
        **session_cookie_attributes(settings),
    )


# ------------------------------------------------------------------ 限流


class LoginLimiter:
    """进程内的登录/注册限流（#54 D1.7）。

    三条约束，都按 **IP** 计（``username`` 只出现在日志里，不锁账号，避免他人恶意
    锁死某个用户名）：

    1. **在途名额**：同一 IP 同时最多 ``max_inflight``（默认 5）个认证请求在跑。
       名额在进入 scrypt **之前**原子占下，所以并发洪峰无法绕过限流同时压满 CPU——
       这是 #61 review 指出的窗口：旧实现只检查已记账的失败，首个失败落账前所有
       并发请求都能通过。
    2. **失败窗口**：60 秒内 5 次失败后拒绝，直到窗口滑过。
    3. **连续失败**：累计 20 次失败锁 900 秒。只有 401/403 算失败；数据库故障等
       5xx 只释放名额，故障期间不会把整片 IP 锁死。

    **成功不清零失败记录**，只释放自己的在途名额。否则握有一个有效账号的人可以
    靠反复「登录成功」把同一 IP 的失败计数清掉，再接着猜别的账号——失败窗口就形同
    虚设。被锁、超窗、在途满一律 429 ``ACCOUNT_LOCKED``（带 ``Retry-After``）。

    计数在进程内：``huice-api.service`` 是单 worker（``--proxy-headers`` 已启用，
    ``request.client`` 是 nginx 转发来的真实对端）。重启清零、多 worker 不共享
    都是接受的代价；``infra/deploy/nginx-huice.conf`` 另有一层粗粒度 ``limit_req``
    兜突发流量，但它不替代这里的按失败次数锁定。
    """

    def __init__(
        self,
        *,
        window_seconds: float = 60.0,
        max_attempts: int = 5,
        lock_after: int = 20,
        lock_seconds: float = 900.0,
        max_inflight: int | None = None,
        clock=time.monotonic,
    ) -> None:
        self._window = window_seconds
        self._max_attempts = max_attempts
        self._lock_after = lock_after
        self._lock_seconds = lock_seconds
        self._max_inflight = max_attempts if max_inflight is None else max_inflight
        self._clock = clock
        self._lock = threading.Lock()
        self._attempts: dict[str, list[float]] = {}
        self._failures: dict[str, int] = {}
        self._locked_until: dict[str, float] = {}
        self._inflight: dict[str, int] = {}

    @contextmanager
    def attempt(self, key: str) -> Iterator[None]:
        """把一次认证请求框起来：进入时原子占名额，被拒（401/403）记一次失败。

        用法是 ``with limiter.attempt(client_key(request)):`` 包住「校验密码/注册」
        这一段；纯字段校验（422）与 Origin/门禁放在块外，不消耗失败次数。
        """

        self._reserve(key)
        try:
            yield
        except ApiProblem as exc:
            # 只把「确实被拒的凭据」记成失败：401 密码错、403 注册密钥错。
            # 数据库不可用之类的 5xx 只释放名额——故障期间不该把整片 IP 锁死。
            self._settle(key, failed=exc.status_code in (401, 403))
            raise
        except BaseException:
            self._settle(key, failed=False)
            raise
        else:
            self._settle(key, failed=False)

    def _reserve(self, key: str) -> None:
        now = self._clock()
        with self._lock:
            locked_until = self._locked_until.get(key, 0.0)
            if locked_until > now:
                raise _too_many(locked_until - now)
            recent = [stamp for stamp in self._attempts.get(key, []) if stamp > now - self._window]
            self._attempts[key] = recent
            if len(recent) >= self._max_attempts:
                retry_after = self._window - (now - recent[0])
                raise _too_many(max(retry_after, 1.0))
            if self._inflight.get(key, 0) >= self._max_inflight:
                # 在途满只说明「现在正忙」，不代表这次尝试失败：不记账，马上重试即可。
                raise _too_many(1.0)
            self._inflight[key] = self._inflight.get(key, 0) + 1

    def _settle(self, key: str, *, failed: bool) -> None:
        now = self._clock()
        with self._lock:
            remaining = self._inflight.get(key, 0) - 1
            if remaining > 0:
                self._inflight[key] = remaining
            else:
                self._inflight.pop(key, None)
            if not failed or self._locked_until.get(key, 0.0) > now:
                return
            recent = [stamp for stamp in self._attempts.get(key, []) if stamp > now - self._window]
            recent.append(now)
            self._attempts[key] = recent
            failures = self._failures.get(key, 0) + 1
            self._failures[key] = failures
            if failures >= self._lock_after:
                self._locked_until[key] = now + self._lock_seconds
                self._failures[key] = 0
                self._attempts[key] = []

    def reset(self) -> None:
        """测试用：清空全部计数。"""

        with self._lock:
            self._attempts.clear()
            self._failures.clear()
            self._locked_until.clear()
            self._inflight.clear()


def _too_many(retry_after: float) -> ApiProblem:
    seconds = max(int(round(retry_after)), 1)
    return ApiProblem(
        429,
        "ACCOUNT_LOCKED",
        "尝试过于频繁，请稍后再试。",
        {"retryAfterSeconds": seconds},
        headers={"Retry-After": str(seconds)},
    )


def client_key(request: Request) -> str:
    """限流键取对端地址。nginx 在前面时这是它转发的地址；不再信任 X-Forwarded-For。"""

    client = request.client
    return client.host if client is not None else "unknown"
