"""API-01 健康检查：不鉴权，也不暴露任何连接信息。"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool

from ..config import APP_VERSION
from ..errors import ApiProblem
from ..schemas import HealthResponse

router = APIRouter(tags=["public"])


@router.get(
    "/health",
    response_model=HealthResponse,
    operation_id="getHealth",
    summary="API-01",
)
async def get_health(request: Request) -> HealthResponse:
    database = request.app.state.db
    if not await run_in_threadpool(database.ping):
        raise ApiProblem(503, "DEPENDENCY_UNAVAILABLE", "依赖服务暂不可用，请稍后重试。")
    return HealthResponse(status="ok", version=APP_VERSION)
