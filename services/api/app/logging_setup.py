"""应用日志管线：让 ``app.*`` / ``huice.*`` 的 INFO 真的进 systemd journal。

uvicorn 自带的 ``LOGGING_CONFIG`` 只配置 ``uvicorn``、``uvicorn.error``、
``uvicorn.access`` 三个 logger；root 保持默认的 WARNING 且不带 handler。应用模块
（``app.routes.*``、``app.agents.*``、``huice.api``）因此继承到 WARNING，
``logger.info("model_call ...")`` 在部署环境里**不会**被输出——测试里
``caplog.set_level(logging.INFO)`` 会掩盖这一点。

这里显式给 ``app`` 与 ``huice`` 两个命名空间挂一个 stderr handler（systemd 的
``StandardOutput/StandardError=journal`` 会把它转进 journal），并设为 INFO。
``propagate`` 保持默认的 ``True``，这样测试里的 ``caplog`` 仍能从 root 收到记录。

只调级别与输出目标，不碰任何字段内容：日志里永远只有对象标识、错误码与用量，
没有提示词、正文、密钥或连接串。
"""

from __future__ import annotations

import logging
import sys

#: 应用自己的 logger 命名空间（见各模块的 ``getLogger`` 调用）。
APP_LOGGERS = ("app", "huice")

_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"

#: 标记挂在 handler 上，重复调用 ``configure_logging`` 不会叠加 handler。
_HANDLER_MARK = "_huice_app_log"


def configure_logging(level: int = logging.INFO) -> None:
    """幂等地配置应用日志；重复调用只会同步级别，不会重复挂 handler。"""

    for name in APP_LOGGERS:
        logger = logging.getLogger(name)
        logger.setLevel(level)
        if any(getattr(handler, _HANDLER_MARK, False) for handler in logger.handlers):
            continue
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(_FORMAT))
        setattr(handler, _HANDLER_MARK, True)
        logger.addHandler(handler)
