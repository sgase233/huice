"""Admin authentication and demo-session gate.

The admin Bearer token is always required. ``DEMO_ACCESS_MODE=restricted`` adds
the gateway session cookie; ``local`` mode bypasses only the cookie, never the
Bearer token.
"""

from __future__ import annotations

import hmac

from fastapi import Request

from .errors import ApiProblem

DEMO_SESSION_COOKIE = "huice_demo_session"


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
        if not session.strip():
            raise ApiProblem(
                403,
                "DEMO_SESSION_REQUIRED",
                "演示会话无效或已过期，请从演示入口重新进入。",
            )
