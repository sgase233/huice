"""E3 后台统计接口：D5 两个指标 + D4 资料完整度。

三件事在路由层做，别的不做：鉴权（审核员会话或只读运维 Bearer）、参数边界
（日期区间最多 31 天、``runId`` 与 ``policyIds`` 二选一）、把查询层结果装成响应。
口径本身在 ``app/analytics/metrics.py`` 与 ``app/analytics/completeness.py``。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request
from pydantic import AwareDatetime

from ..analytics.completeness import CompletenessReader
from ..analytics.metrics import (
    NOT_COLLECTED,
    MetricsReader,
)
from ..domain.versioning import SHANGHAI
from ..errors import ApiProblem
from ..identity import require_reviewer_or_bearer
from ..schemas import (
    AssessmentRunsResponse,
    CompletenessResponse,
    ReviewWorkloadResponse,
)
from ..security import GatedRoute

router = APIRouter(prefix="/admin/metrics", tags=["admin"], route_class=GatedRoute)

#: 首版最多 31 天（METRICS_SPEC §3.1，E0 冻结）。超限直接 400，不悄悄截断。
MAX_RANGE_DAYS = 31


def _range(from_: date, to: date) -> None:
    if from_ > to:
        raise ApiProblem(400, "INVALID_RANGE", "起始日期不能晚于结束日期。")
    if (to - from_).days + 1 > MAX_RANGE_DAYS:
        raise ApiProblem(
            400,
            "RANGE_TOO_LARGE",
            f"日期区间最多 {MAX_RANGE_DAYS} 天，请缩小后重试。",
        )


def _generated_at() -> datetime:
    return datetime.now(SHANGHAI)


def _review_days(rows: list[dict]) -> list[dict]:
    """缺测不是 0：``not_collected`` 的计数返回 ``null``，不返回 0。"""

    for row in rows:
        availability = row["availability"]
        if availability["reviewsCompleted"]["state"] == NOT_COLLECTED:
            row["reviewsCompleted"] = None
        if availability["pendingAtEndOfDay"]["state"] == NOT_COLLECTED:
            # 当天未结束时本来就是 None；这里只处理「未采集」的历史日
            row["pendingAtEndOfDay"] = None
    return rows


def _run_days(rows: list[dict]) -> list[dict]:
    for row in rows:
        availability = row["availability"]
        for name in ("total", "succeeded", "failed", "unfinished"):
            if availability[name]["state"] == NOT_COLLECTED:
                row[name] = None
        if availability["avgDurationMs"]["state"] == NOT_COLLECTED:
            row["avgDurationMs"] = None
    return rows


@router.get(
    "/review-workload",
    response_model=ReviewWorkloadResponse,
    operation_id="getReviewWorkload",
    summary="审核工作量（D5）",
)
def review_workload(
    request: Request,
    from_: Annotated[date, Query(alias="from")],
    to: Annotated[date, Query()],
) -> ReviewWorkloadResponse:
    """按北京时间逐日给 `newDrafts` / `reviewsCompleted` / `pendingAtEndOfDay`。

    每个指标各自带 ``availability``：可能同一天里一个新草稿精确、审核完成只覆盖
    ``T_review`` 之后、运行只覆盖 ``T_run`` 之后。当天未结算时
    ``pendingAtEndOfDay`` 为 ``null`` 且 ``dayOpen=true``。
    """

    require_reviewer_or_bearer(request)
    _range(from_, to)
    generated_at = _generated_at()
    rows = MetricsReader(request.app.state.db).review_workload(
        start=from_, end=to, now=generated_at
    )
    return ReviewWorkloadResponse.model_validate(
        {
            "from": from_,
            "to": to,
            "timeZone": "Asia/Shanghai",
            "generatedAt": generated_at,
            "days": _review_days(rows),
        }
    )


@router.get(
    "/assessment-runs",
    response_model=AssessmentRunsResponse,
    operation_id="getAssessmentRunTrends",
    summary="系统运行趋势（D5）",
)
def assessment_runs(
    request: Request,
    from_: Annotated[date, Query(alias="from")],
    to: Annotated[date, Query()],
) -> AssessmentRunsResponse:
    """按运行**开始时间**归日；跨午夜完成后回看，开始日的结果会更新。

    ``unfinished`` 含 ``queued`` / ``running`` / 崩溃恢复的 ``interrupted``，
    不能并进 ``failed``。耗时只纳入可信起止；旧记录 ``created_at = finished_at``
    不算 0ms，样本数为 0 时 ``avgDurationMs`` 为 ``null``。
    """

    require_reviewer_or_bearer(request)
    _range(from_, to)
    generated_at = _generated_at()
    rows = MetricsReader(request.app.state.db).assessment_runs(
        start=from_, end=to, now=generated_at
    )
    return AssessmentRunsResponse.model_validate(
        {
            "from": from_,
            "to": to,
            "timeZone": "Asia/Shanghai",
            "generatedAt": generated_at,
            "days": _run_days(rows),
        }
    )


@router.get(
    "/completeness",
    response_model=CompletenessResponse,
    operation_id="getProfileCompleteness",
    summary="资料完整度（D4）",
)
def completeness(
    request: Request,
    enterpriseId: UUID,
    asOf: AwareDatetime | None = Query(default=None),
    runId: UUID | None = Query(default=None),
    policyIds: Annotated[list[UUID] | None, Query()] = None,
) -> CompletenessResponse:
    """按 ``(FactKey, period_start, period_end)`` 单位返回七状态与分组计数。

    范围：不传 ``runId``/``policyIds`` 时用「当前已发布政策的最新版本（排除
    closed）」；传 ``runId`` 固定该 run 的版本集合；传 ``policyIds`` 固定政策集合。
    分子始终读**当前事实**——这只是「按该版本统计当前资料完整度」，不是重现当时。
    """

    require_reviewer_or_bearer(request)
    if runId is not None and policyIds:
        raise ApiProblem(
            400,
            "INVALID_SCOPE",
            "runId 与 policyIds 只能给一个：要么按某次运行的版本，要么按指定政策。",
        )
    payload = CompletenessReader(request.app.state.db).build(
        enterprise_id=enterpriseId,
        as_of=asOf,
        run_id=runId,
        policy_ids=policyIds,
    )
    return CompletenessResponse.model_validate(payload)
