#!/usr/bin/env python3
"""把两个导入版本的有效期改成中性占位（M3 在 PR #69 收口里要求的「发布前必修」）。

背景：`import-policy-sources.py` 第一次导入 SMART（深工信规〔2025〕4号）与 GREEN
（深工信规〔2024〕12号）时，把**申报窗口**当成政策有效期写进了 `policy_version`。
清单已经改成「`effective_from = published_at`、`effective_to = NULL`」，但云端那两行
草稿仍是旧值。本脚本只改这两行**未发布**的草稿：

- 目标行必须是 `review_status <> 'published'`——`001` 的 `guard_published_version()`
  只拦已发布行，草稿行可以更新；脚本再额外拒绝任何已发布行，不靠触发器兜底；
- 只改 `effective_from` / `effective_to` / `updated_at`，不动身份列、来源与状态；
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
                    "SELECT id, review_status, published_at, effective_from, effective_to "
                    "FROM policy_version WHERE source_url = %s AND version = %s",
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
                targets.append((row, entry))

            for row, entry in targets:
                print(
                    f"{entry['version']}：{row[1]}，effective_from {row[3]} → {row[2]}，"
                    f"effective_to {row[4]} → NULL"
                )
                if not apply:
                    continue
                conn.execute(
                    "UPDATE policy_version SET effective_from = published_at, effective_to = NULL, "
                    "updated_at = now() WHERE id = %s AND review_status <> 'published'",
                    (row[0],),
                )
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
