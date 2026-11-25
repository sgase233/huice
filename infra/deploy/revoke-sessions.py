#!/usr/bin/env python3
"""撤销运行中的登录会话——部署「会话 Cookie 加 Secure」这类变更时的迁移步骤。

背景（PR #84 复审 P1）：`session_cookie_attributes` 的 `Secure` 判定改成「`APP_ENV` 非
local **或** 站点源是 https」。新代码只管**之后签发**的 Cookie；**已经存在浏览器里的旧
Cookie 不会因为部署而变**——它在用户重新登录或清除之前仍是非 `Secure` 的，浏览器会继续
把它发往明文的 `http://站点/api/...`。所以升级顺序是：

    1) 先关掉公网明文入口（安全组不放行 80，或 nginx 只 listen 443）；
    2) 部署新代码；
    3) 用本脚本撤销旧会话（可选，但要让旧凭据「立即失效」就必须做）；
    4) 通知用户退出并重新登录，DevTools 确认 `huice_session` 的 Secure 已勾上。

只读统计是默认行为；`--apply` 才写 `revoked_at`（不删行，与 AUTH-05 登出同一语义）。

    # 统计（不写库）
    systemd-run -p EnvironmentFile=/etc/huice/api.env \\
      /opt/huice/venv/bin/python /opt/huice/app/infra/deploy/revoke-sessions.py

    # 撤销全部活跃会话
    ... revoke-sessions.py --apply

    # 只撤某个账号（例如只清掉演示账号的旧会话）
    ... revoke-sessions.py --username reviewer.demo --apply

    # 先停旧服务，再从数据库时钟取边界 T（微秒级、带时区）
    ... revoke-sessions.py --print-boundary
    ... revoke-sessions.py --before <T>            # 统计
    ... revoke-sessions.py --before <T> --apply    # 撤销

为什么要 ``--before``：新代码上线后，用户重新登录会**新建**带 ``Secure`` 的会话；「全部
活跃会话为 0」这个条件因此永远不成立，而重开 80 的前提恰恰是「升级前那批旧会话不再有效」。
``--before`` 把判据收窄到切换时刻之前创建的会话，新会话不参与统计，也不会被撤销。

边界 ``T`` 必须**用 ``--print-boundary`` 从数据库时钟取**，不要用 ``date -Iseconds``：
后者只有秒精度，旧服务在同一秒内（``16:00:00.5``）签发的会话会满足不了 ``created_at < T``
而被漏掉，统计就会错误地显示清零；``created_at`` 由数据库时间产生，用服务器系统时钟取 ``T``
还会叠加两台机器之间的时钟偏差。``--print-boundary`` 读的是 ``clock_timestamp()``，与
``created_at`` 同一个时钟，且带微秒与时区。

只打印条数，不打印令牌、摘要或连接串；失败也不带堆栈。

退出码：0 成功；2 环境变量缺失/取值不合法或依赖缺失；3 写库失败。
"""

from __future__ import annotations

import argparse
import datetime
import logging
import os
import re
import sys

_USERNAME_SHAPE = re.compile(r"^[a-z0-9_.-]{3,32}$")

#: 判据的公共部分：会话仍可用（未撤销、未过期）。
_LIVE_SQL = "s.revoked_at IS NULL AND s.expires_at > now()"


def count_sql(where: str) -> str:
    """统计语句。

    ``UPDATE`` 的目标表不能再出现在 ``FROM`` 里——那样别名 ``s`` 会被声明两次，PostgreSQL
    直接报错；统计与撤销各自的 FROM 子句因此分开写，并有 ``test_revoke_sessions_script``
    的一条不连库用例守着（CI 的带库用例才发现过一次）。
    """

    return (
        "SELECT count(*) FROM user_session s JOIN app_user u ON u.id = s.user_id "
        f"WHERE {where}"
    )


def revoke_sql(where: str) -> str:
    return (
        "UPDATE user_session s SET revoked_at = now() FROM app_user u "
        f"WHERE u.id = s.user_id AND {where}"
    )


def format_boundary(value: datetime.datetime) -> str:
    """把数据库时钟读到的时刻写成 `--before` 可直接使用的 ISO 8601（含微秒与时区）。"""

    return value.isoformat()


def _parse_before(raw: str) -> datetime.datetime | None:
    """解析 `--before`：必须是带时区的 ISO 8601。

    不带时区时无法判断它指的是哪一刻，宁可拒绝也不替操作者猜——猜错会把「还没过期的旧
    会话」算成新会话，重开 80 的判据就失真了。
    """

    try:
        value = datetime.datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        return None
    return value


def _run(
    apply: bool,
    username: str | None,
    before: datetime.datetime | None,
    *,
    print_boundary: bool = False,
) -> int:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        print("缺少环境变量 DATABASE_URL（不打印取值）。", file=sys.stderr)
        return 2
    if not dsn.startswith(("postgresql://", "postgres://")):
        print("DATABASE_URL 不是 PostgreSQL 连接串（不打印取值）。", file=sys.stderr)
        return 2
    if username is not None:
        # 与 app_user_username_shape 同一规则：先挡住非法形状，避免拿用户输入去查库。
        normalized = username.strip().lower()
        if not _USERNAME_SHAPE.fullmatch(normalized):
            print("--username 只接受 3—32 位小写字母、数字、下划线、点、连字符。", file=sys.stderr)
            return 2
        username = normalized

    try:
        import psycopg
    except ModuleNotFoundError as exc:
        print(f"缺少依赖 {exc.name}：请用项目虚拟环境的解释器运行。", file=sys.stderr)
        return 2

    logging.getLogger("psycopg.pool").setLevel(logging.CRITICAL)

    if print_boundary:
        try:
            with psycopg.connect(dsn, autocommit=True) as conn:
                boundary = conn.execute("SELECT clock_timestamp()").fetchone()[0]
        except Exception:  # noqa: BLE001 - 不打印异常详情：报错里带主机与库名
            print("读取数据库时钟失败：请检查数据库连通性（不打印连接信息）。", file=sys.stderr)
            return 3
        print(format_boundary(boundary))
        print("把上面这行原样用作 --before <T>；它是数据库时钟，与 created_at 同源。", file=sys.stderr)
        return 0

    conditions: list[str] = []
    params: list[object] = []
    labels: list[str] = []
    if before is not None:
        conditions.append("s.created_at < %s")
        params.append(before)
        labels.append(f"{before.isoformat()} 之前创建的")
    if username is not None:
        conditions.append("u.username = %s")
        params.append(username)
        labels.append(f"账号 {username} 的")
    where = " AND ".join([_LIVE_SQL, *conditions])
    scope = "、".join(labels) or "全部"

    try:
        with psycopg.connect(dsn, autocommit=False) as conn:
            live = conn.execute(count_sql(where), params).fetchone()[0]
            if not apply:
                conn.rollback()
                print(f"{scope}活跃登录会话 {live} 条（dry-run，未写入）；加 --apply 才撤销。")
                return 0

            cursor = conn.execute(revoke_sql(where), params)
            revoked = cursor.rowcount
            conn.commit()
    except Exception:  # noqa: BLE001 - 不打印异常详情：psycopg 的报错里带主机与库名
        print("写库失败：请检查数据库连通性与迁移 009 是否已执行（不打印连接信息）。", file=sys.stderr)
        return 3

    print(f"已撤销{scope}会话 {revoked} 条（统计时为 {live} 条）。")
    print("接下来：通知用户退出并重新登录；DevTools → Application → Cookies 确认 huice_session 的 Secure 已勾上。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="撤销活跃登录会话（默认只统计，--apply 才写库）。")
    parser.add_argument("--apply", action="store_true", help="真正写 revoked_at（默认 dry-run）")
    parser.add_argument("--username", default=None, help="只处理这个账号（默认全部活跃会话）")
    parser.add_argument(
        "--before",
        default=None,
        metavar="ISO8601",
        help="只处理 created_at 早于该时刻的会话；必须带时区，取自 --print-boundary",
    )
    parser.add_argument(
        "--print-boundary",
        action="store_true",
        help="只打印数据库时钟的当前时刻（微秒级、带时区），用作 --before 的边界",
    )
    args = parser.parse_args(argv)
    if args.print_boundary and (args.apply or args.username or args.before):
        print("--print-boundary 不能与 --apply / --username / --before 同时使用。", file=sys.stderr)
        return 2
    before = None
    if args.before is not None:
        before = _parse_before(args.before)
        if before is None:
            print(
                "--before 必须是带时区的 ISO 8601（例如 2026-11-24T16:00:00+08:00，"
                "可用 date -Iseconds 取值）。",
                file=sys.stderr,
            )
            return 2
    return _run(args.apply, args.username, before, print_boundary=args.print_boundary)


if __name__ == "__main__":
    raise SystemExit(main())
