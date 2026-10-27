"""企业画像接口：API-08 创建、API-09 读取、API-10 单条事实更新。

三条路由共用一个版本口径：``ETag`` 就是带双引号的 ``factVersion``，写请求的
``If-Match`` 必须原样回传。缺头 → 428，不是强标签 → 400，版本已变 → 409 并带
``currentVersion``；服务端从不「先读再无条件写」。``verified`` 只由服务端决定，
客户端即使传了也会被契约拒绝。
"""

from __future__ import annotations

import re
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Request, Response

from ..agents.profile import normalize_fact_update, normalize_profile
from ..errors import ApiProblem, trace_id_of
from ..schemas import Enterprise, EnterpriseInput, FactKey, FactUpdate
from ..security import require_demo_session

router = APIRouter(tags=["public"], dependencies=[Depends(require_demo_session)])

_FACT_VERSION_RE = re.compile(r"^[a-f0-9]{64}$")


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
    profile = normalize_profile(body)
    enterprise = request.app.state.repository.create_enterprise(
        profile=profile, trace_id=trace_id_of(request)
    )
    response.headers["ETag"] = f'"{enterprise.fact_version}"'
    return enterprise


@router.get(
    "/enterprises/{enterpriseId}",
    response_model=Enterprise,
    operation_id="getEnterprise",
    summary="API-09",
)
def get_enterprise(
    request: Request, response: Response, enterpriseId: UUID = Path()
) -> Enterprise:
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
