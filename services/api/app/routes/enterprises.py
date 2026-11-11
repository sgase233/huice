"""企业画像接口：API-08 创建、API-09 读取、API-10 单条事实更新。

三条路由共用一个版本口径：``ETag`` 就是带双引号的 ``factVersion``，写请求的
``If-Match`` 必须原样回传。缺头 → 428，不是强标签 → 400，版本已变 → 409 并带
``currentVersion``；服务端从不「先读再无条件写」。``verified`` 只由服务端决定，
客户端即使传了也会被契约拒绝。
"""

from __future__ import annotations

import re
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request, Response

from ..access import require_enterprise_access
from ..agents.profile import normalize_fact_update, normalize_profile
from ..errors import ApiProblem, trace_id_of
from ..identity import (
    Identity,
    claim_demo_sample,
    require_csrf,
    require_public_identity,
    require_writable_demo,
)
from ..schemas import (
    Enterprise,
    EnterpriseInput,
    EnterpriseSummaryList,
    FactKey,
    FactUpdate,
)
from ..security import require_demo_session

router = APIRouter(tags=["public"], dependencies=[Depends(require_demo_session)])

_FACT_VERSION_RE = re.compile(r"^[a-f0-9]{64}$")


@router.get(
    "/enterprises",
    response_model=EnterpriseSummaryList,
    operation_id="listEnterprises",
    summary="AUTH-06",
)
def list_enterprises(
    request: Request,
    cursor: str | None = Query(default=None, min_length=1),
    limit: int = Query(default=20, ge=1, le=50),
) -> EnterpriseSummaryList:
    """归属过滤在服务端：企业用户至多一条、审核员全部、demo 仅无主。

    游标绑定身份与可见范围（``scope`` 进了签名摘要），拿别人的游标续页会 400，
    不会返回他人数据的下一页。
    """

    identity = require_public_identity(request)
    if identity.is_demo:
        scope = "unowned"
    elif identity.is_reviewer:
        scope = "all"
    else:
        scope = "owned"
    return request.app.state.repository.list_enterprises(
        scope=scope,
        viewer_user_id=identity.user_id,
        cursor=cursor,
        limit=limit,
    )


@router.post(
    "/enterprises",
    status_code=201,
    response_model=Enterprise,
    operation_id="createEnterprise",
    summary="API-08",
)
def create_enterprise(
    request: Request, response: Response, body: EnterpriseInput
) -> Enterprise:
    identity = require_public_identity(request)
    owner_user_id = _create_owner(request, identity)
    profile = normalize_profile(body)
    enterprise = request.app.state.repository.create_enterprise(
        profile=profile, trace_id=trace_id_of(request), owner_user_id=owner_user_id
    )
    response.headers["ETag"] = f'"{enterprise.fact_version}"'
    return enterprise


def _create_owner(request: Request, identity: Identity) -> UUID | None:
    """建样本的归属：会话建自己的，demo 建无主的；两种写路径的校验不同。

    账号已经绑过企业时 409 而不是再建一家——``enterprise.owner_user_id`` 的部分唯一
    索引也会挡住，但那是并发兜底；正常情况下应该给一个说得清的错误。
    """

    if identity.is_user:
        if identity.enterprise_id is not None:
            raise ApiProblem(
                409,
                "ENTERPRISE_ALREADY_BOUND",
                "当前账号已绑定企业，不能重复创建；如需更新请调用 PATCH 事实接口。",
                {"enterpriseId": str(identity.enterprise_id)},
            )
        require_csrf(request, identity)
        return identity.user_id
    require_writable_demo(request, identity)
    claim_demo_sample(request, identity)
    return None


@router.get(
    "/enterprises/{enterpriseId}",
    response_model=Enterprise,
    operation_id="getEnterprise",
    summary="API-09",
)
def get_enterprise(
    request: Request, response: Response, enterpriseId: UUID = Path()
) -> Enterprise:
    identity = require_public_identity(request)
    require_enterprise_access(request, identity, enterpriseId, write=False)
    enterprise = request.app.state.repository.get_enterprise(enterpriseId)
    response.headers["ETag"] = f'"{enterprise.fact_version}"'
    return enterprise


@router.patch(
    "/enterprises/{enterpriseId}/facts/{factKey}",
    response_model=Enterprise,
    operation_id="updateFact",
    summary="API-10",
)
def update_fact(
    request: Request,
    response: Response,
    body: FactUpdate,
    enterpriseId: UUID = Path(),
    factKey: FactKey = Path(),
) -> Enterprise:
    identity = require_public_identity(request)
    require_enterprise_access(request, identity, enterpriseId, write=True)
    if identity.is_user:
        require_csrf(request, identity)
    else:
        require_writable_demo(request, identity)
    expected_version = _require_fact_version(request)
    repository = request.app.state.repository

    # 先读画像：既要 404，也要拿到画像年度来校验 region/industry 等年度范围键。
    current = repository.get_enterprise(enterpriseId)
    fact = normalize_fact_update(
        key=factKey,
        update=body,
        profile_period=(current.period.start_date, current.period.end_date),
    )
    enterprise = repository.update_fact(
        enterprise_id=enterpriseId,
        fact=fact,
        expected_fact_version=expected_version,
        trace_id=trace_id_of(request),
    )
    response.headers["ETag"] = f'"{enterprise.fact_version}"'
    return enterprise


def _require_fact_version(request: Request) -> str:
    """解析 If-Match：只接受强 ETag，值必须是 64 位小写十六进制的 factVersion。"""

    raw = request.headers.get("if-match")
    if raw is None or not raw.strip():
        raise ApiProblem(
            428,
            "MISSING_IF_MATCH",
            "更新事实必须带 If-Match：取自 API-08/API-09/上一次 PATCH 响应里的 ETag，原样回传。",
        )
    value = raw.strip()
    if value == "*" or value.upper().startswith("W/"):
        raise ApiProblem(400, "INVALID_IF_MATCH", "If-Match 只接受强 ETag，不接受 * 或弱校验。")
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        value = value[1:-1]
    if not _FACT_VERSION_RE.match(value):
        raise ApiProblem(400, "INVALID_IF_MATCH", "If-Match 不是合法的画像版本（factVersion）。")
    return value
