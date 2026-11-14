#!/usr/bin/env python3
"""把两个导入版本的有效期改成中性占位（M3 在 PR #69 收口里要求的「发布前必修」）。

背景：`import-policy-sources.py` 第一次导入 SMART（深工信规〔2025〕4号）与 GREEN
（深工信规〔2024〕12号）时，把**申报窗口**当成政策有效期写进了 `policy_version`。
清单已经改成「`effective_from = published_at`、`effective_to = NULL`」，但云端那两行
草稿仍是旧值。本脚本只改这两行**未发布**的草稿：

- 目标行必须是 `review_status <> 'published'`——`001` 的 `guard_published_version()`
  只拦已发布行，草稿行可以更新；脚本再额外拒绝任何已发布行，不靠触发器兜底；
- 改的是 `effective_from` / `effective_to` / `revision` / `updated_at` 四列，不动身份列、
  来源与状态。`revision` 是 API-05 的 ETag，行内容变了不换值，后台拿旧 ETag 的
  `If-Match` 就能把改写覆盖回去；
- **幂等**：已经是目标值的行直接跳过（``IS DISTINCT FROM``），不重复 UPDATE、不重复
  bump `revision`，所以「已改写 N 行」始终等于内容真的变了的那几行；
- `published_at` 为空的行不受理：`effective_from` 是 NOT NULL，拿 NULL 去写只会报错；
- 写入按「读到的那一版」`revision` 约束，读完之后被改过就命中 0 行、整批回滚；
- 写前写后各取一次「已发布行 `(id, xmin)` 的 md5」，两次必须相同；
- 默认干跑：打印每行当前值与目标值，不写库。

用法（ECS，venv 解释器）::

    systemd-run --pipe --wait --collect -p EnvironmentFile=/etc/huice/api.env \\
      -p WorkingDirectory=/opt/huice/app \\
      /opt/huice/venv/bin/python /opt/huice/app/infra/deploy/fix-draft-effective-dates.py
    # 确认输出后再加 --apply

退出码：0 成功（含「已是目标值、无需改动」）；2 数据或环境不合法（只报字段路径与变量名）；
3 写库失败；4 命中了已发布版本（拒绝执行，一行都不改）。
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
DEFAULT_DATA = SCRIPT_DIR / "draft-effective-dates.json"


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
        for field in ("sourceUrl", "version"):
            value = item.get(field)
            if not isinstance(value, str) or not value.strip():
                raise DataError(f"entries[{index}].{field} 必须是非空字符串")
        result.append({"sourceUrl": item["sourceUrl"], "version": item["version"]})
    return result


def update_row(conn, row: tuple) -> bool:
    """按「读到的那一版」改一行：revision 变了就命中 0 行，返回 False。

    ``row`` 是 ``(id, review_status, published_at, effective_from, effective_to, revision)``。
    revision 是 API-05 的 ETag：改了行内容就要换值，否则后台拿着旧 ETag 的 If-Match
    仍能通过、把这次改写覆盖回去。
    """

    cursor = conn.execute(
        "UPDATE policy_version SET effective_from = published_at, effective_to = NULL, "
        "revision = gen_random_uuid(), updated_at = now() "
        "WHERE id = %s AND review_status <> 'published' AND revision = %s "
        # 幂等：已经是目标值的行不重复写，也不重复 bump revision
        "AND (effective_from IS DISTINCT FROM published_at OR effective_to IS NOT NULL)",
        (row[0], row[5]),
    )
    return cursor.rowcount == 1


def _published_fingerprint(conn) -> str | None:
    row = conn.execute(
        "SELECT md5(string_agg(id::text || ':' || xmin::text, ',' ORDER BY id)) "
        "FROM policy_version WHERE review_status = 'published'"
    ).fetchone()
    return None if row is None else row[0]


def run(data_path: Path, apply: bool) -> int:
    entries = load_entries(data_path)

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("缺少环境变量 DATABASE_URL（不打印任何取值）。", file=sys.stderr)
        return 2

    try:
        import psycopg
    except ModuleNotFoundError as exc:
        print(
            f"缺少依赖 {exc.name}：请用项目虚拟环境的解释器运行本脚本。",
            file=sys.stderr,
        )
        return 2

    logging.getLogger("psycopg.pool").setLevel(logging.CRITICAL)
    changed = 0
    try:
        with psycopg.connect(dsn, autocommit=False) as conn:
            before = _published_fingerprint(conn)

            targets: list[tuple[Any, dict[str, Any]]] = []
            for entry in entries:
                rows = conn.execute(
                    "SELECT id, review_status, published_at, effective_from, effective_to, revision "
                    "FROM policy_version WHERE source_url = %s AND version = %s FOR UPDATE",
                    (entry["sourceUrl"], entry["version"]),
                ).fetchall()
                if len(rows) != 1:
                    print(
                        f"{entry['version']}：匹配到 {len(rows)} 个政策版本，停止（不写任何行）。",
                        file=sys.stderr,
                    )
                    conn.rollback()
                    return 2
                row = rows[0]
                if row[1] == "published":
                    print(
                        f"{entry['version']}：该行已发布（review_status=published），按 M3 的要求拒绝修改。",
                        file=sys.stderr,
                    )
                    conn.rollback()
                    return 4
                if row[2] is None:
                    print(
                        f"{entry['version']}：该行没有 published_at，无法用占位值改写。",
                        file=sys.stderr,
                    )
                    conn.rollback()
                    return 2
                targets.append((row, entry))

            for row, entry in targets:
                at_target = row[3] == row[2] and row[4] is None
                print(
                    f"{entry['version']}：{row[1]}，effective_from {row[3]} → {row[2]}，"
                    f"effective_to {row[4]} → NULL"
                    + ("（已是目标值，跳过）" if at_target else "")
                )
                if not apply or at_target:
                    continue
                if not update_row(conn, row):
                    conn.rollback()
                    print(
                        f"{entry['version']}：该行在读取之后被改过（revision 已变）或已发布，"
                        "整批回滚、未写任何行。",
                        file=sys.stderr,
                    )
                    return 3
                changed += 1

            if apply:
                after = _published_fingerprint(conn)
                if after != before:
                    conn.rollback()
                    print(
                        "已发布行的 xmin 指纹发生了变化，回滚（这不应当发生）。",
                        file=sys.stderr,
                    )
                    return 3
                conn.commit()
            else:
                conn.rollback()
    except Exception as exc:  # 不回显连接串与堆栈
        print(f"数据库操作失败（{type(exc).__name__}）。", file=sys.stderr)
        return 3

    if apply:
        print(f"已改写 {changed} 行；已发布行的 xmin 指纹前后一致。")
    else:
        print("干跑完成，未写库。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把草稿版本的有效期改成中性占位")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--apply", action="store_true", help="真正写库；不加只打印计划")
    args = parser.parse_args(argv)
    try:
        return run(args.data, args.apply)
    except DataError as exc:
        print(f"fix-draft-effective-dates: 数据不合法：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
