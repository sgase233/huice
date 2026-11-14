"""运行采集起点：启动失败后重试、成功后周期重校准（PR #75 M2 复审）。"""

from __future__ import annotations

import asyncio
import contextlib

from app import main as app_main


def _run_until(calls: list, predicate, coro_factory) -> None:
    """跑后台循环直到 predicate 成立，然后取消；不依赖真实时长。"""

    async def scenario() -> None:
        task = asyncio.create_task(coro_factory())
        try:
            for _ in range(300):
                await asyncio.sleep(0.01)
                if predicate(calls):
                    return
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    asyncio.run(scenario())


def test_the_start_is_retried_without_restarting() -> None:
    """启动时库不可用一次：后台重试成功，不需要重启服务。"""

    calls: list[int] = []

    class Repository:
        def mark_run_lifecycle_start(self) -> None:
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("db down")

    _run_until(calls, lambda c: len(c) >= 2, lambda: app_main._ensure_lifecycle_start(
        Repository(), 0, 0
    ))

    assert len(calls) >= 2


def test_the_loop_keeps_running_after_the_first_success() -> None:
    """首次写入成功后不退出：没有重校准，后提交的更早 T1 永远补不上。"""

    calls: list[int] = []

    class Repository:
        def mark_run_lifecycle_start(self) -> None:
            calls.append(1)

    _run_until(calls, lambda c: len(c) >= 3, lambda: app_main._ensure_lifecycle_start(
        Repository(), 0, 0
    ))

    assert len(calls) >= 3


def test_startup_actually_writes_the_lifecycle_start(database_url_schema, tmp_path) -> None:
    """真正走一遍 lifespan：起点在启动时写入。

    这条用例存在的理由：上一版把 ``RUN_LIFECYCLE_START_RETRY_SECONDS`` 写进了 lifespan，
    却没有在 ``Settings`` 里定义字段——HTTP 用例都没进 lifespan（没当上下文管理器用），
    所以 AttributeError 要到生产启动才炸。
    """

    from fastapi.testclient import TestClient

    import psycopg

    from app.config import Settings
    from app.main import create_app

    settings = Settings(
        app_env="local",
        api_host="127.0.0.1",
        database_url=database_url_schema,
        admin_token="x" * 32,
        demo_access_mode="local",
        demo_session_secret="y" * 64,
        cors_origins=("http://127.0.0.1:5173",),
        model_mode="fixture",
        object_store_mode="local",
        local_object_dir=str(tmp_path / "objects"),
    )
    app = create_app(settings)

    with TestClient(app) as client:
        assert client.get("/api/v1/health").status_code == 200
        for _ in range(100):
            with psycopg.connect(database_url_schema) as conn:
                row = conn.execute(
                    "SELECT run_lifecycle_from FROM collection_baseline WHERE id = 'default'"
                ).fetchone()
            if row is not None and row[0] is not None:
                break
            import time

            time.sleep(0.02)
        else:  # pragma: no cover - 只在启动写入失败时走到
            raise AssertionError("启动后没有写入 run_lifecycle_from")
