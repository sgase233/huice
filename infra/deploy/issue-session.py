#!/usr/bin/env python3
"""签发 restricted 模式用的 huice_demo_session cookie。

签名密钥是 **DEMO_SESSION_SECRET**，与后台 Bearer 使用的 ``ADMIN_TOKEN`` 相互独立：
持有 ADMIN_TOKEN 不能签发会话，反之亦然（M2 二审 P1）。

E1（#46）起签发的 cookie 载荷带 ``sid``（格式 ``v2.<过期秒>.<sid>.<HMAC>``），并在
``demo_quota`` 表里写一行 ``created = 0``、``expires_at`` 与 cookie 同寿命——演示身份
在服务端按这个 sid 计「本次演示会话还能建几个样本」（#54 D1.5）。旧格式 ``v1``
（无 sid）仍然验签通过，但按「只读」处理：写操作一律 403 ``DEMO_SESSION_LEGACY``。

用法（必须用装了本项目依赖的解释器——脚本会导入 ``app.security``，后者依赖 FastAPI）：
    本地（仓库根目录）：DEMO_SESSION_SECRET=... DATABASE_URL=... services/api/.venv/Scripts/python.exe infra/deploy/issue-session.py --hours 12
    ECS：DEMO_SESSION_SECRET=... /opt/huice/venv/bin/python /opt/huice/app/infra/deploy/issue-session.py --hours 12
    也可用 --secret 传密钥：... issue-session.py --secret <DEMO_SESSION_SECRET> --hours 12

``DATABASE_URL`` 只从环境读（不设命令行参数，避免连接串进 ``ps`` / shell 历史）；
ECS 上用 §3.1 的 ``systemd-run -p EnvironmentFile=/etc/huice/api.env`` 提供。

输出两行：cookie 值，以及可直接放进网关/浏览器开发者工具的 Set-Cookie 头。
拿到密钥即可签发会话，请不要把密钥或输出写进日志、文档或聊天记录。

退出码：0 成功；2 密钥/参数/环境不合法（只报变量名，不报值）；3 配额行写库失败
（此时**不输出** cookie——配额行没落库的会话发出去，演示建样会从错误的起点计数）。
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(REPO_ROOT / "services" / "api"))

try:  # noqa: E402
    from app import auth as auth_tools
    from app.config import MIN_DEMO_SESSION_SECRET_LENGTH
    from app.db import Database
    from app.integrations.accounts import AccountsRepository
    from app.security import DEMO_SESSION_COOKIE, issue_demo_session
except ModuleNotFoundError as exc:  # 用到系统解释器时给出可操作提示，而不是抛堆栈
    print(
        f"缺少依赖 {exc.name}：请用项目虚拟环境的解释器运行本脚本"
        "（本地 services/api/.venv，ECS /opt/huice/venv/bin/python）。",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

#: sid 会被拼进 ``v2.<过期秒>.<sid>.<HMAC>``，含 ``.`` 会让验签端拆错字段。
_SID_SHAPE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def _write_quota_row(sid: str, expires_at: int) -> bool:
    """写 demo_quota 的初始行；失败时只在 stderr 报原因类别，不回显连接串。"""

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn.startswith(("postgresql://", "postgres://")):
        print(
            "缺少可用的 DATABASE_URL：签发演示会话需要写 demo_quota 行"
            "（ECS 上用 systemd-run -p EnvironmentFile=/etc/huice/api.env 提供）。",
            file=sys.stderr,
        )
        return False

    # 连不上库时连接池会在 close() 时再喊一句「couldn't stop thread」——那句话不说明
    # 原因，真正的原因由下面统一打印（库不可达、迁移未执行、无写权限）。
    logging.getLogger("psycopg.pool").setLevel(logging.CRITICAL)

    database = Database(dsn, max_size=1)
    try:
        AccountsRepository(database).ensure_demo_quota(
            sid_hash=auth_tools.digest(sid),
            expires_at=datetime.fromtimestamp(expires_at, tz=timezone.utc),
        )
    except Exception as exc:  # 不打印异常详情：psycopg 的报错里带主机与库名
        print(
            f"写 demo_quota 失败（{type(exc).__name__}）：请确认迁移 009 已执行、"
            "DATABASE_URL 指向本环境的库、账号有写权限。",
            file=sys.stderr,
        )
        return False
    finally:
        database.close()
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="签发 huice_demo_session cookie")
    parser.add_argument("--secret", default=os.environ.get("DEMO_SESSION_SECRET", ""))
    parser.add_argument("--hours", type=float, default=12.0)
    parser.add_argument(
        "--sid",
        default="",
        help="演示会话标识（8—64 位字母/数字/下划线/连字符）；默认随机生成",
    )
    args = parser.parse_args()

    secret = args.secret.strip()
    if len(secret) < MIN_DEMO_SESSION_SECRET_LENGTH:
        print(
            "缺少 DEMO_SESSION_SECRET：请用 --secret 传入或设置环境变量"
            f"（至少 {MIN_DEMO_SESSION_SECRET_LENGTH} 位）。"
            "注意这里用的是会话密钥，不是 ADMIN_TOKEN。",
            file=sys.stderr,
        )
        return 2
    if args.hours <= 0:
        print("--hours 必须大于 0。", file=sys.stderr)
        return 2

    sid = args.sid.strip() or auth_tools.new_demo_sid()
    if not _SID_SHAPE.match(sid):
        print(
            "--sid 只允许 8—64 位字母、数字、下划线与连字符"
            "（它会被拼进 cookie 载荷，不能含点）。",
            file=sys.stderr,
        )
        return 2

    expires_at = int(time.time() + args.hours * 3600)
    if not _write_quota_row(sid, expires_at):
        return 3

    value = issue_demo_session(secret, expires_at=expires_at, sid=sid)
    ttl = int(args.hours * 3600)

    print(f"value: {value}")
    print(
        f"Set-Cookie: {DEMO_SESSION_COOKIE}={value}; Path=/; Max-Age={ttl}; "
        "HttpOnly; Secure; SameSite=Lax"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
