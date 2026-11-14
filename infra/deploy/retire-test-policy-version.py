#!/usr/bin/env python3
"""把一个**已发布的测试版本**降级为草稿（M3 第三轮选的 B 方案，受控例外）。

背景：云端 `v1.0` 是早期测试数据（`post_1.html`），却已是 `published`，会出现在政策
列表里。`001` 的 `guard_published_version()` 拒绝任何已发布行的 UPDATE/DELETE，所以正常
路径上无法取消发布。

本脚本走**受控例外**：

1. 只处理数据文件里逐个列出的 `(source_url, version)`，且必须当前是 `published`；
2. 执行前打印快照：`id`、`version`、`review_status`、`revision`、`xmin`，以及引用计数
   （`assessment` / `policy_review_event`）——有引用时降级是对的，删除会破坏审计链，
   所以本脚本**只降级、不删除**；
3. 写入在**同一个事务**里 `DISABLE TRIGGER` → `UPDATE` → `ENABLE TRIGGER`：
   `ALTER TABLE ... DISABLE TRIGGER` 会取 ACCESS EXCLUSIVE 锁，事务提交前别人进不来；
   不删除任何行、不动子表；
4. 提交前比对「已发布行（排除目标行）的 `xmin` 指纹」，提交后再算一次，必须一致；
5. 提交后**验证守卫已恢复**：对一行已发布行做一次 UPDATE，必须被拒（`CheckViolation`），
   然后回滚——证明临时禁用没有遗留。

用法（ECS，venv 解释器）::

    systemd-run --pipe --wait --collect -p EnvironmentFile=/etc/huice/api.env \\
      -p WorkingDirectory=/opt/huice/app \\
      /opt/huice/venv/bin/python /opt/huice/app/infra/deploy/retire-test-policy-version.py
    # 确认快照与引用计数后再加 --apply

退出码：0 成功（含「已是草稿、无需改动」）；2 数据或环境不合法；3 写库失败；
4 目标行不是 `published`（拒绝执行，避免误伤真实政策）。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_DATA = SCRIPT_DIR / "retire-test-versions.json"

TRIGGER = "policy_version_guard_published"


class DataError(ValueError):
    """数据文件不合法。消息里只带字段路径。"""


def load_entries(path: Path) -> list[dict[str, str]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise DataError(f"数据文件不存在：{path}") from None
    except json.JSONDecodeError as exc:
        raise DataError(f"数据文件不是合法 JSON（第 {exc.lineno} 行）") from None
    entries = raw.get("entries") if isinstance(raw, dict) else None
    if not isinstance(entries, list) or not entries:
        raise DataError("entries 必须是非空数组")
    result = []
    for index, item in enumerate(entries):
        if not isinstance(item, dict):
            raise DataError(f"entries[{index}] 必须是对象")
        for field in ("sourceUrl", "version", "reason"):
            value = item.get(field)
            if not isinstance(value, str) or not value.strip():
                raise DataError(f"entries[{index}].{field} 必须是非空字符串")
        result.append(
            {
                "sourceUrl": item["sourceUrl"],
                "version": item["version"],
                "reason": item["reason"],
            }
        )
    return result


def _published_fingerprint(conn, *, exclude_ids: list[Any] | None = None) -> str | None:
    row = conn.execute(
        "SELECT md5(string_agg(id::text || ':' || xmin::text, ',' ORDER BY id)) "
        "FROM policy_version WHERE review_status = 'published' AND NOT (id = ANY(%s))",
        (exclude_ids or [],),
    ).fetchone()
    return None if row is None else row[0]


def _references(conn, version_id: Any, policy_id: Any) -> dict[str, int]:
    """数一下有哪些行引用这个版本 / 它所属的政策。

    表可能还没建（012 未执行时没有事件表），跳过即可；`policy_candidate` 记的是
    政策 id（`submitted_policy_id`），不是版本 id。本脚本只降级不删除，这些计数是给
    执行者看的证据，不改变动作。
    """

    checks = (
        ("assessment", "policy_version_id", version_id),
        ("policy_review_event", "policy_version_id", version_id),
        ("policy_source", "policy_version_id", version_id),
        ("policy_criterion", "policy_version_id", version_id),
        ("policy_candidate", "submitted_policy_id", policy_id),
    )
    counts: dict[str, int] = {}
    for table, column, value in checks:
        exists = conn.execute("SELECT to_regclass(%s) IS NOT NULL", (table,)).fetchone()[0]
        if not exists:
            counts[table] = 0
            continue
        row = conn.execute(
            f"SELECT count(*) FROM {table} WHERE {column} = %s", (value,)
        ).fetchone()
        counts[table] = int(row[0])
    return counts


def _assert_guard_active(conn, published_id: Any) -> None:
    """对一行已发布行试一次 UPDATE：必须被守卫拒绝；无论如何都回滚。"""

    try:
        conn.execute(
            "UPDATE policy_version SET revision = gen_random_uuid() WHERE id = %s",
            (published_id,),
        )
    except Exception as exc:  # noqa: BLE001 - 只关心「被拒绝」这件事
        conn.rollback()
        if exc.__class__.__name__ != "CheckViolation":
            raise RuntimeError(f"守卫以 {type(exc).__name__} 拒绝，预期 CheckViolation") from exc
        return
    conn.rollback()
    raise RuntimeError("守卫没有生效：已发布行居然可以被 UPDATE")


def run(data_path: Path, apply: bool) -> int:
    entries = load_entries(data_path)

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("缺少环境变量 DATABASE_URL（不打印任何取值）。", file=sys.stderr)
        return 2

    try:
        import psycopg
    except ModuleNotFoundError as exc:
        print(f"缺少依赖 {exc.name}：请用项目虚拟环境的解释器运行。", file=sys.stderr)
        return 2

    logging.getLogger("psycopg.pool").setLevel(logging.CRITICAL)
    retired = 0
    try:
        with psycopg.connect(dsn, autocommit=False) as conn:
            # 先锁定所有目标并打印快照，任何一个不合格就整批退出
            targets: list[tuple[dict[str, str], dict[str, Any]]] = []
            for entry in entries:
                rows = conn.execute(
                    "SELECT id, review_status, revision, xmin::text, policy_id, reviewed_at, "
                    "(SELECT count(*) FROM policy_source ps WHERE ps.policy_version_id = pv.id) "
                    "FROM policy_version pv WHERE source_url = %s AND version = %s FOR UPDATE",
                    (entry["sourceUrl"], entry["version"]),
                ).fetchall()
                if len(rows) != 1:
                    print(
                        f"{entry['version']}：匹配到 {len(rows)} 个版本，停止（不写任何行）。",
                        file=sys.stderr,
                    )
                    conn.rollback()
                    return 2
                row = {
                    "id": rows[0][0],
                    "status": rows[0][1],
                    "revision": rows[0][2],
                    "xmin": rows[0][3],
                    "policy_id": rows[0][4],
                    "reviewed_at": rows[0][5],
                    "sources": int(rows[0][6]),
                }
                if row["status"] != "published":
                    print(
                        f"{entry['version']}：当前是 {row['status']!r}，不是 published，"
                        "拒绝执行（本脚本只处理测试用的已发布版本）。",
                        file=sys.stderr,
                    )
                    conn.rollback()
                    return 4
                targets.append((entry, row))

            target_ids = [row["id"] for _entry, row in targets]
            # 操作前基线：排除全部目标行的已发布行指纹。操作后用同一范围再算一次，
            # 才能证明「除目标以外」没有变化——降级后目标已不在 published 集合里，
            # 事后两次查询会是同一集合，比不出前后差异。
            before = _published_fingerprint(conn, exclude_ids=target_ids)

            for entry, row in targets:
                refs = _references(conn, row["id"], row["policy_id"])
                print(
                    f"{entry['version']}：{row['status']}，id {row['id']}，policy {row['policy_id']}，"
                    f"revision {row['revision']}，xmin {row['xmin']}，reviewed_at {row['reviewed_at']}，"
                    f"来源 {row['sources']} 条；引用："
                    + "、".join(f"{name}={count}" for name, count in refs.items())
                    + f"；理由：{entry['reason']}"
                )
                if not apply:
                    continue
                conn.execute(f"ALTER TABLE policy_version DISABLE TRIGGER {TRIGGER}")
                cursor = conn.execute(
                    "UPDATE policy_version SET review_status = 'pending_parse', "
                    "reviewed_at = NULL, revision = gen_random_uuid(), updated_at = now() "
                    "WHERE id = %s AND review_status = 'published'",
                    (row["id"],),
                )
                conn.execute(f"ALTER TABLE policy_version ENABLE TRIGGER {TRIGGER}")
                if cursor.rowcount != 1:
                    conn.rollback()
                    print(
                        f"{entry['version']}：UPDATE 命中 {cursor.rowcount} 行（预期 1），已回滚。",
                        file=sys.stderr,
                    )
                    return 3
                retired += 1

            if apply:
                after = _published_fingerprint(conn, exclude_ids=target_ids)
                if after != before:
                    conn.rollback()
                    print(
                        "除目标行之外的已发布行指纹发生了变化，回滚（这不应当发生）。",
                        file=sys.stderr,
                    )
                    return 3
                conn.commit()
            else:
                conn.rollback()

        if apply and retired:
            # 守卫恢复验证：另开一个事务试一次「改已发布行」，必须被拒
            with psycopg.connect(dsn, autocommit=False) as probe:
                row = probe.execute(
                    "SELECT id FROM policy_version WHERE review_status = 'published' LIMIT 1"
                ).fetchone()
                if row is None:
                    print(
                        "守卫无法验证：库里已没有其它已发布行可供试验。请人工确认触发器已恢复。",
                        file=sys.stderr,
                    )
                    return 3
                try:
                    _assert_guard_active(probe, row[0])
                except RuntimeError as exc:
                    print(f"守卫验证失败：{exc}", file=sys.stderr)
                    return 3
                print("守卫已验证恢复：对已发布行的 UPDATE 仍被拒绝。")
    except Exception as exc:  # 不回显连接串与堆栈
        print(f"数据库操作失败（{type(exc).__name__}）。", file=sys.stderr)
        return 3

    if apply:
        print(f"已降级 {retired} 个测试版本为 pending_parse；未删除任何行。")
    else:
        print("干跑完成，未写库。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把已发布的测试版本降级为草稿（受控例外）")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--apply", action="store_true", help="真正写库；不加只打印快照")
    args = parser.parse_args(argv)
    try:
        return run(args.data, args.apply)
    except DataError as exc:
        print(f"retire-test-policy-version: 数据不合法：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
