"""演示门禁（E1 后的第 1 层）。

本模块只表达**门禁**，不表达身份：E1 之前后台接口在这里同时验运维 Bearer，那是把
「门禁」与「身份」混成一层；现在两种凭证的取舍统一在 :mod:`app.identity` 里按真值表
判定（会话 / 运维 Bearer / demo 三选一），运维 Bearer 只读。

``DEMO_ACCESS_MODE=restricted`` 时要求签名有效的演示会话 cookie；``local`` 模式免门禁。

The demo cookie is not a bare "present/absent" flag: it must be signed and
unexpired, so an arbitrary value cannot satisfy the gate. Format::

    v1.<expiresAtEpochSeconds>.<hmacSha256Hex>            （旧格式，无 sid，E1 起只读）
    v2.<expiresAtEpochSeconds>.<sid>.<hmacSha256Hex>      （E1 起，sid 用于演示建样配额）

The signing key is derived from the dedicated ``DEMO_SESSION_SECRET``, never from
``ADMIN_TOKEN``: the gateway gate and the admin Bearer gate must stay independent,
so holding the Bearer token must not be enough to mint a session. The secret is
required (≥32 chars) whenever ``DEMO_ACCESS_MODE=restricted``.
``infra/deploy/issue-session.py`` is the supported way to mint a session.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import NamedTuple

from fastapi import Request

from .errors import ApiProblem

DEMO_SESSION_COOKIE = "huice_demo_session"
#: 旧格式：``v1.<过期秒>.<HMAC>``，没有 ``sid``。E1 起只读（#54 D1.5）。
DEMO_SESSION_VERSION = "v1"
#: 新格式：``v2.<过期秒>.<sid>.<HMAC>``。``sid`` 区分每一个演示会话，
#: 用于「单次演示会话最多建 20 个样本」的原子计数；签发时同步写入 ``demo_quota``。
DEMO_SESSION_SID_VERSION = "v2"

_KEY_PREFIX = "huice-demo-session:"


class DemoSessionClaims(NamedTuple):
    expires_at: int
    sid: str | None


def _session_key(secret: str) -> bytes:
    return hashlib.sha256(f"{_KEY_PREFIX}{secret}".encode("utf-8")).digest()


def _sign(secret: str, payload: str) -> str:
    return hmac.new(
        _session_key(secret), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def issue_demo_session(secret: str, *, expires_at: int, sid: str | None = None) -> str:
    """Build a signed cookie value that stays valid until ``expires_at``.

    ``secret`` is ``DEMO_SESSION_SECRET``; passing ``ADMIN_TOKEN`` here would
    re-couple the two gates, so callers must not do it.

    ``sid`` 只在 E1 之后的签发里给出（``v2``）；不给出则签旧格式 ``v1``，
    旧 cookie 按「只读」处理——不建议再这样签发。
    """
    if sid is None:
        payload = f"{DEMO_SESSION_VERSION}.{int(expires_at)}"
    else:
        payload = f"{DEMO_SESSION_SID_VERSION}.{int(expires_at)}.{sid}"
    return f"{payload}.{_sign(secret, payload)}"


def demo_session_claims(
    value: str, secret: str, *, now: int | None = None
) -> DemoSessionClaims | None:
    """验签并拆出过期时间与 ``sid``；任何格式/签名/过期问题都返回 ``None``。"""

    version, _, remainder = value.strip().partition(".")
    if version == DEMO_SESSION_VERSION:
        expires_raw, _, signature = remainder.partition(".")
        sid: str | None = None
    elif version == DEMO_SESSION_SID_VERSION:
        fields = remainder.split(".")
        if len(fields) != 3:
            return None
        expires_raw, sid, signature = fields
        if not sid:
            return None
    else:
        return None
    if not expires_raw or not signature:
        return None
    try:
        expires_at = int(expires_raw)
    except ValueError:
        return None
    expected = _sign(secret, f"{version}.{remainder.rsplit('.', 1)[0]}")
    if not hmac.compare_digest(signature, expected):
        return None
    current = int(time.time()) if now is None else int(now)
    if expires_at <= current:
        return None
    return DemoSessionClaims(expires_at=expires_at, sid=sid)


def demo_session_is_valid(value: str, secret: str, *, now: int | None = None) -> bool:
    """Check version, signature and expiry; any malformed value is invalid."""
    return demo_session_claims(value, secret, now=now) is not None


def require_demo_session(request: Request) -> None:
    """公开接口的演示会话门禁：local 模式直接放行，restricted 模式校验签名与有效期。

    网关是这一层的主要执行者；后端再校验一次，保证绕过网关的请求也拿不到数据。
    """

    settings = request.app.state.settings
    if settings.demo_access_mode != "restricted":
        return
    _check_session(request, settings)


def _check_session(request: Request, settings) -> None:
    session = request.cookies.get(DEMO_SESSION_COOKIE, "")
    secret = settings.demo_session_secret
    # 配置校验已保证 restricted 模式下密钥存在且与 ADMIN_TOKEN 不同；这里再挡一次，
    # 覆盖绕过 Settings.from_env 直接构造配置的情形（例如测试或后续重构）：
    # 空密钥签出的 cookie 人人可伪造，密钥等于 ADMIN_TOKEN 则两层门禁退化为同一把钥匙。
    if not secret or secret == settings.admin_token or not demo_session_is_valid(session, secret):
        raise ApiProblem(
            403,
            "DEMO_SESSION_REQUIRED",
            "演示会话无效或已过期，请从演示入口重新进入。",
        )
