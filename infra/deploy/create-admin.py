#!/usr/bin/env python3
"""在没有审核员账号的部署上建第一个审核员（保底初始化，#61 review 第 5 条）。

E1 之后 ``POST /auth/register`` 只有在配置了 ``REVIEWER_SIGNUP_KEY`` 时才能建审核员，
而全新部署里没有任何审核员——没有人能登进后台，也就没有人能改这个配置。这个脚本就是
那个缺口：**由持有服务器 root 的人**在 ECS 上直接写库，只建 ``role = 'reviewer'`` 的
账号，不建企业，不签发会话。

密钥只从环境变量读，不设命令行参数、不回显：

    本地（仓库根目录）：
        DATABASE_URL=... ADMIN_INIT_USERNAME=... ADMIN_INIT_PASSWORD=... \\
          services/api/.venv/Scripts/python.exe infra/deploy/create-admin.py
    ECS：
        systemd-run -p EnvironmentFile=/etc/huice/api.env \\
          /opt/huice/venv/bin/python /opt/huice/app/infra/deploy/create-admin.py

    ``ADMIN_INIT_PASSWORD`` 用交互式 ``read -s`` 现读现用，不要写进 ``api.env``
    （那会让保底口令与运行时长期共存），也不要出现在 shell 历史里。

成功后只打印用户名与退出码；口令、哈希、连接串都不进 stdout / stderr。

退出码：0 成功；2 变量缺失或取值不合法（只报变量名，不报值）；3 写库失败；
4 该用户名已存在（不覆盖已有口令——重置口令要走 AUTH-05，不能靠这个脚本降级）。
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(REPO_ROOT / "services" / "api"))

try:  # noqa: E402
    from app import auth as auth_tools
    from app.db import Database
    from app.errors import ApiProblem
    from app.integrations.accounts import AccountsRepository
except ModuleNotFoundError as exc:  # 用到系统解释器时给出可操作提示，而不是抛堆栈
    print(
        f"缺少依赖 {exc.name}：请用项目虚拟环境的解释器运行本脚本"
        "（本地 services/api/.venv，ECS /opt/huice/venv/bin/python）。",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

_REQUIRED_ENV = ("DATABASE_URL", "ADMIN_INIT_USERNAME", "ADMIN_INIT_PASSWORD")


def _read_env() -> dict[str, str] | None:
    """读出三个变量并校验；缺失或不合规时只报变量名。"""

    values = {name: os.environ.get(name, "") for name in _REQUIRED_ENV}
    missing = [name for name in _REQUIRED_ENV if not values[name].strip()]
    if missing:
        print(
            f"缺少环境变量 {', '.join(missing)}：本脚本只从环境读，不接受命令行参数"
            "（避免口令进 ps 与 shell 历史）。",
            file=sys.stderr,
        )
        return None

    dsn = values["DATABASE_URL"].strip()
    if not dsn.startswith(("postgresql://", "postgres://")):
        print("DATABASE_URL 不是可用的 PostgreSQL 连接串。", file=sys.stderr)
        return None

    username = auth_tools.normalize_username(values["ADMIN_INIT_USERNAME"])
    if not auth_tools.username_is_valid(username):
        print(
            "ADMIN_INIT_USERNAME 不合法：3—32 位小写字母、数字、下划线、点或连字符"
            "（会先按注册同一规则去空白、转小写）。",
            file=sys.stderr,
        )
        return None

    password = values["ADMIN_INIT_PASSWORD"]
    if not auth_tools.password_is_acceptable(password):
        print(
            "ADMIN_INIT_PASSWORD 长度不合契约："
            f"{auth_tools.MIN_PASSWORD_LENGTH}—{auth_tools.MAX_PASSWORD_LENGTH} 位。",
            file=sys.stderr,
        )
        return None

    return {"dsn": dsn, "username": username, "password": password}


def main() -> int:
    env = _read_env()
    if env is None:
        return 2

    # 连不上库时连接池会在 close() 时再喊一句「couldn't stop thread」——那句话不说明
    # 原因，真正的原因由下面统一打印（库不可达、迁移 009 未执行、无写权限）。
    logging.getLogger("psycopg.pool").setLevel(logging.CRITICAL)

    database = Database(env["dsn"], max_size=1)
    try:
        AccountsRepository(database).register(
            username=env["username"],
            password_hash=auth_tools.hash_password(env["password"]),
            role="reviewer",
            profile=None,
        )
    except ApiProblem as exc:
        if exc.code == "USERNAME_TAKEN":
            print(
                f"用户名 {env['username']} 已存在：本脚本不覆盖既有账号的口令。"
                "如需重置请用该账号登录后调用 AUTH-05。",
                file=sys.stderr,
            )
            return 4
        print(f"建账号失败（{exc.code}）。", file=sys.stderr)
        return 3
    except Exception as exc:  # 不打印异常详情：psycopg 的报错里带主机与库名
        print(
            f"建账号失败（{type(exc).__name__}）：请确认迁移 009 已执行、"
            "DATABASE_URL 指向本环境的库、账号有写权限。",
            file=sys.stderr,
        )
        return 3
    finally:
        database.close()

    print(f"已创建审核员账号 {env['username']}；口令未回显，请立即登录并改一次密码。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
