"""后台写请求的公共前置校验：身份 + CSRF、If-Match、Idempotency-Key。

API-04/05/06/07 与 E2 的 DISC-02/06/07/08 是同一套规则：写操作只接受审核员会话
（E1 之后运维 Bearer 只读）、条件写必须带强 ETag、可重放的提交必须带幂等键。规则
写在两处就会出现「一个接口接受 ``*``、另一个拒绝」这种口径分裂，所以集中在这里。
"""

from __future__ import annotations

from uuid import UUID

from fastapi import Request

from ..errors import ApiProblem
from ..identity import require_csrf, require_reviewer


def require_reviewer_write(request: Request) -> None:
    """后台写操作：审核员会话 + CSRF/Origin。Bearer 在 E1 后只读，写请求一律 403。"""

    identity = require_reviewer(request)
    require_csrf(request, identity)


def require_if_match(request: Request) -> UUID:
    raw = request.headers.get("if-match")
    if raw is None or not raw.strip():
        raise ApiProblem(
            428,
            "MISSING_IF_MATCH",
            "写请求必须带 If-Match：取自上一步响应里的 ETag，原样回传。",
        )
    value = raw.strip()
    if value == "*" or value.upper().startswith("W/"):
        raise ApiProblem(400, "INVALID_IF_MATCH", "If-Match 只接受强 ETag，不接受 * 或弱校验。")
    if value.startswith('"') and value.endswith('"') and len(value) >= 2:
        value = value[1:-1]
    try:
        return UUID(value)
    except ValueError as exc:
        raise ApiProblem(400, "INVALID_IF_MATCH", "If-Match 不是合法的修订号。") from exc


def require_idempotency_key(request: Request) -> UUID:
    raw = (request.headers.get("idempotency-key") or "").strip()
    if not raw:
        raise ApiProblem(
            400,
            "MISSING_IDEMPOTENCY_KEY",
            "该接口必须带 Idempotency-Key（UUID），用于防止重复提交。",
        )
    try:
        return UUID(raw)
    except ValueError as exc:
        raise ApiProblem(
            400, "INVALID_IDEMPOTENCY_KEY", "Idempotency-Key 必须是 UUID。"
        ) from exc
