"""判断运行接口：API-11 创建、API-12 读取、API-13 报告、API-14 轨迹。

创建与读取都只做「校验请求 / 交给编排器 / 返回快照」；四态由
:mod:`app.domain.rules` 求值，路由不参与判断。创建是同步的（契约不定义 taskId），
阻塞的数据库与模型调用由 FastAPI 的线程池承接。

读取时的 ``validity``/``staleReasons``/``validUntil`` 是**读取视图**：由仓储按失效
事件与当前时间重算，路由与仓储都不回写历史快照。

API-13 报告与 API-14 轨迹都只读同一份历史：报告把 run 快照转写成 Markdown 并附
「截至下载时刻」的有效性提示；轨迹先列入库/审核步骤，再列本次在线步骤，需管理授权。
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Request, Response

from ..agents.contracts import AgentContext
from ..agents.orchestrator import assess
from ..agents.report import render_markdown
from ..errors import ApiProblem, trace_id_of
from ..schemas import AssessmentInput, AssessmentRun, TraceResponse
from ..security import require_admin, require_demo_session

router = APIRouter(tags=["public"], dependencies=[Depends(require_demo_session)])
admin_router = APIRouter(tags=["admin"], dependencies=[Depends(require_admin)])


@router.post(
    "/assessment-runs",
    response_model=AssessmentRun,
    operation_id="createRun",
    summary="API-11",
)
def create_run(request: Request, body: AssessmentInput) -> AssessmentRun:
    _check_period(body)
    _check_unique_policies(body)

    context = AgentContext(
        trace_id=trace_id_of(request),
        as_of=body.as_of,
        period=(body.period.start_date, body.period.end_date),
        request_id=trace_id_of(request),
    )
    return assess(
        request=body,
        context=context,
        repository=request.app.state.repository,
        llm=request.app.state.llm,
        cache=request.app.state.cache,
    )


@router.get(
    "/assessment-runs/{runId}",
    response_model=AssessmentRun,
    operation_id="getRun",
    summary="API-12",
)
def get_run(request: Request, runId: UUID = Path()) -> AssessmentRun:
    return request.app.state.repository.get_run(runId)


@router.get(
    "/assessment-runs/{runId}/report.md",
    operation_id="downloadReport",
    summary="API-13",
    response_class=Response,
    responses={200: {"content": {"text/markdown": {"schema": {"type": "string"}}}}},
)
def download_report(request: Request, runId: UUID = Path()) -> Response:
    # 先按读取视图取 run：历史四态与证据原样，validity/staleReasons 用当前时间重算。
    run = request.app.state.repository.get_run(runId)
    markdown = render_markdown(run, downloaded_at=datetime.now(timezone.utc))
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="assessment-{runId}.md"'},
    )


@admin_router.get(
    "/assessment-runs/{runId}/trace",
    response_model=TraceResponse,
    operation_id="getTrace",
    summary="API-14",
)
def get_trace(request: Request, runId: UUID = Path()) -> TraceResponse:
    return request.app.state.repository.get_trace(runId)


def _check_period(body: AssessmentInput) -> None:
    if body.period.start_date > body.period.end_date:
        raise ApiProblem(
            422,
            "VALIDATION_ERROR",
            "判断期间不合法：开始日期不能晚于结束日期。",
            {"fieldErrors": [{"field": "period", "message": "开始日期不能晚于结束日期。"}]},
        )


def _check_unique_policies(body: AssessmentInput) -> None:
    """契约要求 policyIds 不重复；重复会让同一政策在一次运行里出现两次。"""

    if len(set(body.policy_ids)) != len(body.policy_ids):
        raise ApiProblem(
            422,
            "VALIDATION_ERROR",
            "policyIds 不能重复。",
            {"fieldErrors": [{"field": "policyIds", "message": "存在重复的政策 ID。"}]},
        )
