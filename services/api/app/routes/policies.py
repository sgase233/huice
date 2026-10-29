"""公开政策接口：API-02 列表、API-03 详情。

公开读取只返回**已审核发布**的版本；``closed`` 是按当前时刻派生的状态，不落库。
未发布或不存在的版本一律 404，绝不回退到别的版本——把草稿或旧版本当真展示，
比没有结果更危险。
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import AwareDatetime

from ..domain.versioning import shanghai_date
from ..schemas import PolicyDetail, PolicyList
from ..security import require_demo_session

router = APIRouter(tags=["public"], dependencies=[Depends(require_demo_session)])


@router.get(
    "/policies",
    response_model=PolicyList,
    operation_id="listPolicies",
    summary="API-02",
)
def list_policies(
    request: Request,
    status: Literal["published", "closed"] | None = Query(default=None),
    policy_type: Literal["tax", "grant"] | None = Query(default=None, alias="type"),
    region: str | None = Query(default=None, min_length=1),
    as_of: AwareDatetime | None = Query(default=None, alias="asOf"),
    cursor: str | None = Query(default=None, min_length=1),
    limit: int = Query(default=20, ge=1, le=50),
) -> PolicyList:
    """缺省 ``status`` 时同时包含开放与已关闭的已审样本。

    ``region`` 按「该区划 + 全国性政策」匹配：只做等值会把深圳企业该适用的国家税收
    政策（``region=CN``）全部藏掉。

    ``asOf`` 只按元数据过滤「当天是否处于适用期」，不用于选择历史版本；日期一律
    按北京时间解释，与判断链路口径一致。
    """

    return request.app.state.repository.list_public_policies(
        status=status,
        policy_type=policy_type,
        region=region,
        as_of=shanghai_date(as_of) if as_of is not None else None,
        cursor=cursor,
        limit=limit,
    )


@router.get(
    "/policies/{policyId}",
    response_model=PolicyDetail,
    operation_id="getPolicy",
    summary="API-03",
)
def get_policy(
    request: Request,
    policyId: UUID,
    version: str | None = Query(default=None, min_length=1),
) -> PolicyDetail:
    return request.app.state.repository.get_policy_detail(policyId, version)
