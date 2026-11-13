#!/usr/bin/env python3
"""回填 ``policy_version.date_provenance``（VIS-06 的 B1a，E3 #48）。

迁移 ``011_date_provenance.sql`` 只加列；日期来源的实际取值来自 M3 对官方原文的核对
结论，逐条写在 ``date-provenance-backfill.json`` 里，本脚本负责校验、定位政策版本、
把出处绑定到该版本的 ``policy_source`` 行，并幂等写入。

五个设计点：

1. **只认结构，不认业务**：脚本不判断某个日期该是「已核实」还是「推算」，只检查
   数据文件自洽——五个状态之一、``verified`` 必须给出来源与定位、``derived`` 必须
   给出推算依据。口径错了是数据文件的问题，脚本不替它猜。

2. **出处必须绑定到该政策版本**：数据文件写的是来源类型（正文 / 附件第几份），脚本在
   库里查该 ``policy_version_id`` 的 ``policy_source`` 行，把真实 id 写进 jsonb。这样
   「能定位」与「定位到的是这个版本的原文」同时成立，评审提的第三条由结构保证，而不是
   靠回填人自觉。

   写入的是旁表 ``policy_version_date_provenance``，**已发布版本一行都不改**：001 的
   ``policy_version_guard_published`` 禁止更新已发布行，把来源塞回 ``policy_version``
   只能靠临时禁用触发器，那正是守卫要防的事。

3. **幂等**：值相同就不写。连跑两次第二次改动 0 行；执行前后都打印
   「已带来源的行数」，作为可复核的证据。

4. **默认干跑**：不加 ``--apply`` 只打印将要改动哪些版本，不写库。回填数据在 M3 复核
   前只能当草案，误跑一次就会把未复核的取值写进审计表。

5. **不回显敏感信息**：``DATABASE_URL`` 只从环境读（不上命令行，避免进 ``ps`` 与
   shell 历史）；数据库异常只打印异常类名，不打印连接串、堆栈或 SQL 参数。

用法（仓库根目录，用装了本项目依赖的解释器）::

    DATABASE_URL=... services/api/.venv/Scripts/python.exe infra/deploy/backfill-date-provenance.py
    DATABASE_URL=... services/api/.venv/Scripts/python.exe infra/deploy/backfill-date-provenance.py --apply
    ECS: systemd-run --pipe --wait --collect -p EnvironmentFile=/etc/huice/api.env \\
             /opt/huice/venv/bin/python /opt/huice/app/infra/deploy/backfill-date-provenance.py --apply

退出码：0 成功（含干跑）；1 数据库读写失败；2 数据文件或环境不合法（只报字段路径与
变量名，不报值）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_DATA = SCRIPT_DIR / "date-provenance-backfill.json"

#: 与 ``docs/devlog/M1-11-13-E3方案草案.md`` §4.2 一致，改口径必须同时改草案与契约。
STATUSES = ("verified", "derived", "unverified", "unknown", "not_applicable")
DATE_FIELDS = ("effectiveFrom", "effectiveTo", "applicationWindow")
SOURCE_KINDS = ("body", "attachment")
PROVENANCE_VERSION = 1


class DataError(ValueError):
    """数据文件不合法。消息里只带字段路径，不带连接串或密钥。"""


@dataclass
class Entry:
    """一条回填：按 (source_url, version) 定位政策版本，写入 provenance。"""

    index: int
    source_url: str
    version: str
    provenance: dict[str, Any]
    #: 日期字段 → 来源声明 ``{"kind": "body"}`` / ``{"kind": "attachment", "ordinal": 0}``
    sources: dict[str, dict[str, Any]] = field(default_factory=dict)


def _fail(index: int, path: str, reason: str) -> DataError:
    return DataError(f"entries[{index}].{path}: {reason}")


def _require_text(index: int, path: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _fail(index, path, "必须是非空字符串")
    return value.strip()


def _parse_iso_date(index: int, path: str, value: Any) -> None:
    if not isinstance(value, str):
        raise _fail(index, path, "必须是 YYYY-MM-DD 字符串")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise _fail(index, path, "必须是合法的 YYYY-MM-DD 日期") from None


def _validate_source_spec(index: int, path: str, spec: Any) -> dict[str, Any]:
    if not isinstance(spec, dict):
        raise _fail(index, path, "必须是对象，如 {\"kind\": \"body\"}")
    kind = spec.get("kind")
    if kind not in SOURCE_KINDS:
        raise _fail(index, f"{path}.kind", "只能是 body 或 attachment")
    ordinal = spec.get("ordinal")
    if kind == "body":
        if ordinal is not None:
            raise _fail(index, f"{path}.ordinal", "正文不写序号（每个版本只有一份正文）")
        return {"kind": "body"}
    if not isinstance(ordinal, int) or ordinal < 0:
        raise _fail(index, f"{path}.ordinal", "附件必须给非负整数序号")
    return {"kind": "attachment", "ordinal": ordinal}


def validate_entry(index: int, raw: Any) -> Entry:
    """校验一条回填。返回 Entry；日期字段的来源声明与存储值分开保存。"""
    if not isinstance(raw, dict):
        raise _fail(index, "", "必须是对象")
    source_url = _require_text(index, "sourceUrl", raw.get("sourceUrl"))
    if not source_url.startswith("https://"):
        raise _fail(index, "sourceUrl", "必须是 https 地址（与 policy_version.source_url 的约束一致）")
    version = _require_text(index, "version", raw.get("version"))

    provenance = raw.get("provenance")
    if not isinstance(provenance, dict):
        raise _fail(index, "provenance", "必须是对象")

    unknown_keys = set(provenance) - {"version", "note", *DATE_FIELDS}
    if unknown_keys:
        raise _fail(index, "provenance", f"出现未知字段 {sorted(unknown_keys)}")
    if provenance.get("version") != PROVENANCE_VERSION:
        raise _fail(index, "provenance.version", f"必须是 {PROVENANCE_VERSION}")

    note = provenance.get("note")
    if note is not None and (not isinstance(note, str) or not note.strip()):
        raise _fail(index, "provenance.note", "必须是字符串，或省略")

    sources: dict[str, dict[str, Any]] = {}
    stored: dict[str, Any] = {"version": PROVENANCE_VERSION}

    for name in DATE_FIELDS:
        if name not in provenance:
            continue
        spec = provenance[name]
        path = f"provenance.{name}"
        if not isinstance(spec, dict):
            raise _fail(index, path, "必须是对象")
        status = spec.get("status")
        if status not in STATUSES:
            raise _fail(index, f"{path}.status", f"只能是 {', '.join(STATUSES)}")

        allowed = {"status"}
        if status == "verified":
            allowed |= {"source", "locator"}
            stored[name] = {
                "status": status,
                "locator": _require_text(index, f"{path}.locator", spec.get("locator")),
            }
            sources[name] = _validate_source_spec(index, f"{path}.source", spec.get("source"))
        elif status == "derived":
            allowed |= {"basis", "derivedFrom"}
            derived: dict[str, Any] = {
                "status": status,
                "basis": _require_text(index, f"{path}.basis", spec.get("basis")),
            }
            if spec.get("derivedFrom") is not None:
                _parse_iso_date(index, f"{path}.derivedFrom", spec.get("derivedFrom"))
                derived["derivedFrom"] = spec["derivedFrom"]
            stored[name] = derived
        else:
            stored[name] = {"status": status}

        extra = set(spec) - allowed
        if extra:
            raise _fail(index, path, f"状态 {status} 不允许字段 {sorted(extra)}")

    if note is not None:
        stored["note"] = note.strip()
    if len(stored) == 1:
        raise _fail(index, "provenance", "至少要给一个日期字段的状态或 note")

    return Entry(index=index, source_url=source_url, version=version, provenance=stored, sources=sources)


def load_data(path: Path) -> list[Entry]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise DataError(f"数据文件不存在：{path}") from None
    except json.JSONDecodeError as exc:
        raise DataError(f"数据文件不是合法 JSON：{path}（第 {exc.lineno} 行）") from None
    if not isinstance(raw, dict):
        raise DataError("数据文件根节点必须是对象")
    if raw.get("version") != 1:
        raise DataError("数据文件 version 必须是 1")
    entries = raw.get("entries")
    if not isinstance(entries, list) or not entries:
        raise DataError("entries 必须是非空数组")

    seen: set[tuple[str, str]] = set()
    result: list[Entry] = []
    for index, item in enumerate(entries):
        entry = validate_entry(index, item)
        key = (entry.source_url, entry.version)
        if key in seen:
            raise _fail(index, "sourceUrl+version", "与其他条目重复")
        seen.add(key)
        result.append(entry)
    return result


def _redact(message: str) -> str:
    """数据库异常的文案偶尔会带地址；这里只保留类名，绝不回显连接串。"""
    return message.split("://")[0] if "://" in message else message


def run(data_path: Path, apply: bool) -> int:
    entries = load_data(data_path)

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("backfill-date-provenance: 缺少环境变量 DATABASE_URL（不打印任何取值）。", file=sys.stderr)
        return 2

    try:
        import psycopg
        from psycopg.types.json import Jsonb
    except ModuleNotFoundError as exc:
        print(
            f"缺少依赖 {exc.name}：请用项目虚拟环境的解释器运行"
            "（本地 services/api/.venv，ECS /opt/huice/venv/bin/python）。",
            file=sys.stderr,
        )
        return 2

    changed = 0
    skipped = 0
    try:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT (SELECT count(*) FROM policy_version_date_provenance), "
                    "(SELECT count(*) FROM policy_version)"
                )
                before_with, before_total = cur.fetchone()

            # 两阶段：先把每条都对上号（政策版本、来源行），全部成立才开始写。任何一条
            # 对不上都整批停下——回填错位比回填缺失更难发现，写到一半停下更糟。
            resolved: list[tuple[Entry, Any, dict[str, Any]]] = []
            for entry in entries:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id FROM policy_version WHERE source_url = %s AND version = %s",
                        (entry.source_url, entry.version),
                    )
                    rows = cur.fetchall()
                if len(rows) != 1:
                    print(
                        f"backfill-date-provenance: entries[{entry.index}] 匹配到 {len(rows)} 个政策版本"
                        f"（sourceUrl={entry.source_url} version={entry.version}）。停止：不写任何行。",
                        file=sys.stderr,
                    )
                    conn.rollback()
                    return 2
                version_id = rows[0][0]

                stored = dict(entry.provenance)
                for name, spec in entry.sources.items():
                    with conn.cursor() as cur:
                        if spec["kind"] == "body":
                            cur.execute(
                                "SELECT id FROM policy_source "
                                "WHERE policy_version_id = %s AND kind = 'body'",
                                (version_id,),
                            )
                        else:
                            cur.execute(
                                "SELECT id FROM policy_source "
                                "WHERE policy_version_id = %s AND kind = 'attachment' AND ordinal = %s",
                                (version_id, spec["ordinal"]),
                            )
                        source_rows = cur.fetchall()
                    if len(source_rows) != 1:
                        print(
                            f"backfill-date-provenance: entries[{entry.index}] 的 {name} 找不到"
                            f"对应的 {spec['kind']}"
                            + (f" #{spec['ordinal']}" if spec["kind"] == "attachment" else "")
                            + f" 来源（命中 {len(source_rows)} 行）。停止：不写任何行。",
                            file=sys.stderr,
                        )
                        conn.rollback()
                        return 2
                    stored[name]["sourceId"] = str(source_rows[0][0])
                resolved.append((entry, version_id, stored))

            for entry, version_id, stored in resolved:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT provenance FROM policy_version_date_provenance "
                        "WHERE policy_version_id = %s",
                        (version_id,),
                    )
                    row = cur.fetchone()
                    if row is not None and row[0] == stored:
                        skipped += 1
                        continue
                    if apply:
                        # 写旁表而不是 policy_version：已发布版本不可变由触发器保证，
                        # 回填不得以任何方式改动它。
                        cur.execute(
                            "INSERT INTO policy_version_date_provenance "
                            "(policy_version_id, provenance, updated_at) VALUES (%s, %s, now()) "
                            "ON CONFLICT (policy_version_id) DO UPDATE "
                            "SET provenance = EXCLUDED.provenance, updated_at = now()",
                            (version_id, Jsonb(stored)),
                        )
                    changed += 1
                    print(
                        f"entries[{entry.index}] {'写入' if apply else '将写入'} "
                        f"{entry.version}（{entry.source_url}）"
                    )
            if apply:
                conn.commit()
            else:
                conn.rollback()

            with conn.cursor() as cur:
                cur.execute(
                    "SELECT (SELECT count(*) FROM policy_version_date_provenance), "
                    "(SELECT count(*) FROM policy_version)"
                )
                after_with, after_total = cur.fetchone()
    except DataError:
        raise
    except Exception as exc:  # 不回显连接串与堆栈
        print(f"backfill-date-provenance: 数据库操作失败（{_redact(exc.__class__.__name__)}）。", file=sys.stderr)
        return 1

    print(
        f"backfill-date-provenance: {'已写入' if apply else '干跑'} {changed} 行，未变化 {skipped} 行；"
        f"带来源的版本 {before_with}/{before_total} → {after_with}/{after_total}。"
        f"{'' if apply else ' 加 --apply 才会写库。'}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="回填 policy_version.date_provenance（VIS-06 B1a）")
    parser.add_argument(
        "--data",
        type=Path,
        default=DEFAULT_DATA,
        help="回填数据文件（默认同目录的 date-provenance-backfill.json）",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="真正写库；不加时只打印将要改动的条目（回填数据未经 M3 复核前保持干跑）",
    )
    args = parser.parse_args(argv)
    try:
        return run(args.data, args.apply)
    except DataError as exc:
        print(f"backfill-date-provenance: 数据不合法：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
