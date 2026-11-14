"""运行采集起点：启动失败后在后台重试，不让服务等库（PR #75 M2 复审）。"""

from __future__ import annotations

import asyncio

from app import main as app_main


def test_the_start_is_retried_without_restarting(monkeypatch) -> None:
    """启动时库不可用一次：后台重试成功，不需要重启服务。"""

    calls: list[int] = []

    class Repository:
        def mark_run_lifecycle_start(self) -> None:
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("db down")

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(app_main.asyncio, "sleep", no_sleep)

    asyncio.run(app_main._ensure_lifecycle_start(Repository(), retry_seconds=1))

    assert len(calls) == 2
