#!/usr/bin/env python3
"""签发 restricted 模式用的 huice_demo_session cookie。

用法（必须用装了本项目依赖的解释器——脚本会导入 ``app.security``，后者依赖 FastAPI）：
    本地（仓库根目录）：ADMIN_TOKEN=... services/api/.venv/Scripts/python.exe infra/deploy/issue-session.py --hours 12
    ECS：ADMIN_TOKEN=... /opt/huice/venv/bin/python /opt/huice/app/infra/deploy/issue-session.py --hours 12
    也可用 --token 传令牌：... issue-session.py --token <ADMIN_TOKEN> --hours 12

输出两行：cookie 值，以及可直接放进网关/浏览器开发者工具的 Set-Cookie 头。
签名密钥由 ADMIN_TOKEN 派生，所以本脚本不引入新的机密；但也意味着**拿到
ADMIN_TOKEN 就能签发会话**，请不要把输出写进日志、文档或聊天记录。
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
    parser.add_argument("--token", default=os.environ.get("ADMIN_TOKEN", ""))
    parser.add_argument("--hours", type=float, default=12.0)
    args = parser.parse_args()

    token = args.token.strip()
    if len(token) < 16:
        print("缺少 ADMIN_TOKEN：请用 --token 传入或设置环境变量（至少 16 位）。", file=sys.stderr)
        return 2
    if args.hours <= 0:
        print("--hours 必须大于 0。", file=sys.stderr)
        return 2

    expires_at = int(time.time() + args.hours * 3600)
    value = issue_demo_session(token, expires_at=expires_at)
    ttl = int(args.hours * 3600)

    print(f"value: {value}")
    print(
        f"Set-Cookie: {DEMO_SESSION_COOKIE}={value}; Path=/; Max-Age={ttl}; "
        "HttpOnly; Secure; SameSite=Lax"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
