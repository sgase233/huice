"""Admin authentication and demo-session gate.

The admin Bearer token is always required. ``DEMO_ACCESS_MODE=restricted`` adds
the gateway session cookie; ``local`` mode bypasses only the cookie, never the
Bearer token.

The demo cookie is not a bare "present/absent" flag: it must be signed and
unexpired, so an arbitrary value cannot satisfy the gate. Format::

    v1.<expiresAtEpochSeconds>.<hmacSha256Hex>

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

from fastapi import Request

from .errors import ApiProblem

DEMO_SESSION_COOKIE = "huice_demo_session"
DEMO_SESSION_VERSION = "v1"

_KEY_PREFIX = "huice-demo-session:"


def _session_key(secret: str) -> bytes:
    return hashlib.sha256(f"{_KEY_PREFIX}{secret}".encode("utf-8")).digest()


def _sign(secret: str, payload: str) -> str:
    return hmac.new(
        _session_key(secret), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def issue_demo_session(secret: str, *, expires_at: int) -> str:
    """Build a signed cookie value that stays valid until ``expires_at``.

    ``secret`` is ``DEMO_SESSION_SECRET``; passing ``ADMIN_TOKEN`` here would
    re-couple the two gates, so callers must not do it.
    """
    payload = f"{DEMO_SESSION_VERSION}.{int(expires_at)}"
    return f"{payload}.{_sign(secret, payload)}"


def demo_session_is_valid(value: str, secret: str, *, now: int | None = None) -> bool:
    """Check version, signature and expiry; any malformed value is invalid."""
    version, _, remainder = value.strip().partition(".")
    if version != DEMO_SESSION_VERSION:
        return False
    expires_raw, _, signature = remainder.partition(".")
    if not expires_raw or not signature:
        return False
    try:
        expires_at = int(expires_raw)
    except ValueError:
        return False
    expected = _sign(secret, f"{version}.{expires_raw}")
    if not hmac.compare_digest(signature, expected):
        return False
    current = int(time.time()) if now is None else int(now)
    return expires_at > current


def require_admin(request: Request) -> None:
    settings = request.app.state.settings
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    supplied = token.strip()

    if scheme.lower() != "bearer" or not supplied or not hmac.compare_digest(
        supplied, settings.admin_token
    ):
        raise ApiProblem(
            401,
            "UNAUTHENTICATED",
            "需要管理授权：请在 Authorization 头提供 Bearer 令牌。",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if settings.demo_access_mode == "restricted":
        session = request.cookies.get(DEMO_SESSION_COOKIE, "")
        secret = settings.demo_session_secret
        # 配置校验已保证 restricted 模式下密钥存在且与 ADMIN_TOKEN 不同；这里再挡一次，
        # 覆盖绕过 Settings.from_env 直接构造配置的情形（例如测试或后续重构）：
        # 空密钥签出的 cookie 人人可伪造，密钥等于 ADMIN_TOKEN 则两层门禁退化为同一把钥匙。
        if not secret or secret == settings.admin_token or not demo_session_is_valid(
            session, secret
        ):
            raise ApiProblem(
                403,
                "DEMO_SESSION_REQUIRED",
                "演示会话无效或已过期，请从演示入口重新进入。",
            )
