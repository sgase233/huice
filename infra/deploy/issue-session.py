#!/usr/bin/env python3
"""签发 restricted 模式用的 huice_demo_session cookie。

用法（在代码根目录）：
    ADMIN_TOKEN=... python3 infra/deploy/issue-session.py --hours 12
    python3 infra/deploy/issue-session.py --token <ADMIN_TOKEN> --hours 12

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

from app.security import DEMO_SESSION_COOKIE, issue_demo_session  # noqa: E402


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
