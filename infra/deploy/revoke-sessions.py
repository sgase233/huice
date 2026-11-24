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

    # 只处理「切换时刻 T 之前创建」的会话——迁移判据要用这个，不能用「全部活跃会话」
    ... revoke-sessions.py --before 2026-11-24T16:00:00+08:00
    ... revoke-sessions.py --before 2026-11-24T16:00:00+08:00 --apply

为什么要 ``--before``：新代码上线后，用户重新登录会**新建**带 ``Secure`` 的会话；「全部
活跃会话为 0」这个条件因此永远不成立，而重开 80 的前提恰恰是「升级前那批旧会话不再有效」。
``--before`` 把判据收窄到切换时刻之前创建的会话，新会话不参与统计，也不会被撤销。

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
_JOIN_SQL = "FROM user_session s JOIN app_user u ON u.id = s.user_id"


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


def _run(apply: bool, username: str | None, before: datetime.datetime | None) -> int:
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
            live = conn.execute(f"SELECT count(*) {_JOIN_SQL} WHERE {where}", params).fetchone()[0]
            if not apply:
                conn.rollback()
                print(f"{scope}活跃登录会话 {live} 条（dry-run，未写入）；加 --apply 才撤销。")
                return 0

            cursor = conn.execute(
                f"UPDATE user_session s SET revoked_at = now() {_JOIN_SQL} WHERE {where}",
                params,
            )
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
        help="只处理 created_at 早于该时刻的会话；必须带时区，例如 2026-11-24T16:00:00+08:00",
    )
    args = parser.parse_args(argv)
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
    return _run(args.apply, args.username, before)


if __name__ == "__main__":
    raise SystemExit(main())
