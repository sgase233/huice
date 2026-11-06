"""部署日志管线：``app.*`` 的 INFO 必须真的落到 stderr，不能只在 ``caplog`` 里可见。

uvicorn 自带的 ``LOGGING_CONFIG`` 不配置应用 logger，root 停在 WARNING 且没有
handler——这正是「测试里 ``caplog.set_level(logging.INFO)`` 通过、线上
``journalctl | grep model_call`` 却查不到」的原因。本文件按部署顺序（uvicorn 先
初始化日志、应用再启动）验证一遍，且**不依赖测试强制调级**。
"""

from __future__ import annotations

import logging
import logging.config

import pytest
from uvicorn.config import LOGGING_CONFIG

from app.logging_setup import APP_LOGGERS, configure_logging


@pytest.fixture
def uvicorn_like_logging():
    """还原部署顺序：uvicorn 按默认配置初始化日志，应用 logger 回到未配置状态。"""

    saved = {
        name: (logging.getLogger(name).level, list(logging.getLogger(name).handlers))
        for name in APP_LOGGERS
    }
    logging.config.dictConfig(LOGGING_CONFIG)
    for name in APP_LOGGERS:
        logger = logging.getLogger(name)
        logger.setLevel(logging.NOTSET)
        logger.handlers.clear()
    try:
        yield
    finally:
        for name, (level, handlers) in saved.items():
            logger = logging.getLogger(name)
            logger.setLevel(level)
            logger.handlers[:] = handlers


def test_uvicorn_defaults_would_swallow_app_info_logs(uvicorn_like_logging):
    # 前提：不加应用侧配置时 INFO 是关的——否则下面的用例等于没测。
    assert not logging.getLogger("app.routes.admin_policies").isEnabledFor(logging.INFO)
    assert not logging.getLogger("huice.api").isEnabledFor(logging.INFO)


def test_configure_logging_puts_model_call_lines_on_stderr(uvicorn_like_logging, capfd):
    configure_logging()
    logging.getLogger("app.agents.orchestrator").info(
        "model_call purpose=explanation outcome=ok usage=known"
    )

    # 走真实 fd：不是 caplog 抓的，systemd 的 journal 看到的也是这一路。
    assert "model_call purpose=explanation outcome=ok usage=known" in capfd.readouterr().err


def test_configure_logging_is_idempotent(uvicorn_like_logging):
    configure_logging()
    configure_logging()

    handlers = [h for h in logging.getLogger("app").handlers if getattr(h, "_huice_app_log", False)]
    # 每次建 app 都调一次，重复挂 handler 会让同一行日志出现多份。
    assert len(handlers) == 1


def test_the_service_starts_with_app_info_logging_on(uvicorn_like_logging):
    from conftest import TEST_DATABASE_URL

    from app.config import Settings
    from app.main import create_app

    create_app(
        Settings(
            app_env="local",
            api_host="127.0.0.1",
            database_url=TEST_DATABASE_URL,
            cors_origins=("http://127.0.0.1:5173",),
            admin_token="0123456789abcdef0123456789abcdef",
            demo_access_mode="local",
        )
    )

    # 建 app 就会配置日志：部署时不需要额外的环境变量或 systemd 参数。
    assert logging.getLogger("app.routes.admin_policies").isEnabledFor(logging.INFO)
