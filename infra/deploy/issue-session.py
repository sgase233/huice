#!/usr/bin/env python3
"""签发 restricted 模式用的 huice_demo_session cookie。

签名密钥是 **DEMO_SESSION_SECRET**，与后台 Bearer 使用的 ``ADMIN_TOKEN`` 相互独立：
持有 ADMIN_TOKEN 不能签发会话，反之亦然（M2 二审 P1）。

用法（必须用装了本项目依赖的解释器——脚本会导入 ``app.security``，后者依赖 FastAPI）：
    本地（仓库根目录）：DEMO_SESSION_SECRET=... services/api/.venv/Scripts/python.exe infra/deploy/issue-session.py --hours 12
    ECS：DEMO_SESSION_SECRET=... /opt/huice/venv/bin/python /opt/huice/app/infra/deploy/issue-session.py --hours 12
    也可用 --secret 传密钥：... issue-session.py --secret <DEMO_SESSION_SECRET> --hours 12

输出两行：cookie 值，以及可直接放进网关/浏览器开发者工具的 Set-Cookie 头。
拿到密钥即可签发会话，请不要把密钥或输出写进日志、文档或聊天记录。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(REPO_ROOT / "services" / "api"))

try:  # noqa: E402
    from app.config import MIN_DEMO_SESSION_SECRET_LENGTH
    from app.security import DEMO_SESSION_COOKIE, issue_demo_session
except ModuleNotFoundError as exc:  # 用到系统解释器时给出可操作提示，而不是抛堆栈
    print(
        f"缺少依赖 {exc.name}：请用项目虚拟环境的解释器运行本脚本"
        "（本地 services/api/.venv，ECS /opt/huice/venv/bin/python）。",
        file=sys.stderr,
    )
    raise SystemExit(2) from None


def main() -> int:
    parser = argparse.ArgumentParser(description="签发 huice_demo_session cookie")
    parser.add_argument("--secret", default=os.environ.get("DEMO_SESSION_SECRET", ""))
    parser.add_argument("--hours", type=float, default=12.0)
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

    expires_at = int(time.time() + args.hours * 3600)
    value = issue_demo_session(secret, expires_at=expires_at)
    ttl = int(args.hours * 3600)

    print(f"value: {value}")
    print(
        f"Set-Cookie: {DEMO_SESSION_COOKIE}={value}; Path=/; Max-Age={ttl}; "
        "HttpOnly; Secure; SameSite=Lax"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
