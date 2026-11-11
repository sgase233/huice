"""对象归属（判定顺序的第 4 层）：角色够之后，再看对象是不是自己的。

两层结论分开写，是因为它们回答不同的问题：

- **能不能看见**：审核员看全部（含无主），企业用户看自己那一份，demo 只看无主；
- **能不能改**：比「看见」更窄——审核员也只能改**自己**名下的企业（#54 D1.3）。

越权的响应与对象不存在**完全相同**（同一个码、同一段文案），否则 404/403 的差别就
是「他人是否存在该对象」的探针。码沿用对象专属码（``ENTERPRISE_NOT_FOUND`` /
``RUN_NOT_FOUND``），不造通用 ``OBJECT_NOT_FOUND``（#55 draft.4 的 not-found-codes）。
"""

from __future__ import annotations

from uuid import UUID

from fastapi import Request

from .errors import ApiProblem
from .identity import Identity

ENTERPRISE_NOT_FOUND = "没有找到对应的企业画像。"
RUN_NOT_FOUND = "没有找到对应的判断运行。"


def _not_found(code: str, message: str) -> ApiProblem:
    return ApiProblem(404, code, message)


# ---------------------------------------------------------------- 纯判定


def can_view_enterprise(identity: Identity, owner_user_id: UUID | None) -> bool:
    if identity.is_demo:
        return owner_user_id is None
    if identity.is_reviewer:
        return True
    return owner_user_id is not None and owner_user_id == identity.user_id


def can_write_enterprise(identity: Identity, owner_user_id: UUID | None) -> bool:
    """写企业（建 run、改事实）：审核员也只看自己名下的企业。"""

    if identity.is_demo:
        return owner_user_id is None
    return owner_user_id is not None and owner_user_id == identity.user_id


def can_view_run(identity: Identity, owner_user_id: UUID | None) -> bool:
    if identity.is_demo:
        return owner_user_id is None
    if identity.is_reviewer:
        return True
    return owner_user_id is not None and owner_user_id == identity.user_id


# ------------------------------------------------------------ 路由层助手


def require_enterprise_access(
    request: Request, identity: Identity, enterprise_id: UUID, *, write: bool
) -> UUID | None:
    """校验企业可见（或可写），返回归属账号 ID 供后续写路径使用。

    仓储在对象不存在时自己抛同一个 404，所以这里只处理「存在但不属于你」。
    """

    owner = request.app.state.repository.enterprise_owner(enterprise_id)
    allowed = (
        can_write_enterprise(identity, owner)
        if write
        else can_view_enterprise(identity, owner)
    )
    if not allowed:
        raise _not_found("ENTERPRISE_NOT_FOUND", ENTERPRISE_NOT_FOUND)
    return owner


def require_run_access(request: Request, identity: Identity, run_id: UUID) -> UUID | None:
    owner = request.app.state.repository.run_owner(run_id)
    if not can_view_run(identity, owner):
        raise _not_found("RUN_NOT_FOUND", RUN_NOT_FOUND)
    return owner
