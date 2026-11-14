#!/usr/bin/env python3
"""受控修正**已发布**政策版本的有效期（SRDI 的 B）。

背景：SRDI（深工信规〔2025〕8号）的 `effective_from/to` 被登记成申报窗口
2025-10-15—2025-11-10，而细则第二十四条写的是「自印发之日起施行，有效期5年」。
窗口关闭后 `resolve_validity` 会按有效期已过判 `closed`，`staleReasons` 可能含
`policy_expired`——这是把窗口当有效期。M3 选 B（受控修正），但要求：

1. 先补细则来源（见 `add-published-policy-source.py`），有效期才有可绑定的出处；
2. 按官方页面的成文日期 2025-10-14 起算，`effective_to` 按「5 年减 1 日」推算为
   2030-10-13（页面「失效日期」栏写 2030-10-14，一日差异写进 provenance note）；
3. 已发布行受 `policy_version_guard_published` 保护，只能走**受控例外**。

本脚本只做三件事：把指定版本的有效期改成数据文件里的新值、换 `revision`、写
`updated_at`；不碰标题、来源、条款、申报窗口，也不删除任何行。

安全措施与 `retire-test-policy-version.py` / `backfill-date-provenance.py` 一致：

- 只处理数据文件逐条列出的 `(sourceUrl, version)`，且必须当前是 `published`；
- 干跑打印快照（id/status/revision/xmin/published_at/当前有效期/来源行数/引用计数），
  `--write-plan` 固定数据文件摘要、旧值与 `revision`/`xmin`、新值、已发布行指纹；
- 正式执行先 `SELECT ... FOR UPDATE`，逐项比对旧值，任一变化整批回滚；
- 只在目标行 UPDATE 前后临时 `DISABLE/ENABLE TRIGGER`，`ALTER TABLE` 的
  ACCESS EXCLUSIVE 锁保证事务提交前没有并发写；
- 提交前用同一「排除全部目标行」范围比对已发布行 `xmin` 指纹；
- 提交后另开连接对已发布行试一次 UPDATE，必须被 `CheckViolation` 拒绝。

用法（ECS，venv 解释器）::

    systemd-run --pipe --wait --collect -p EnvironmentFile=/etc/huice/api.env \\
      -p WorkingDirectory=/opt/huice/app \\
      /opt/huice/venv/bin/python /opt/huice/app/infra/deploy/fix-published-effective-dates.py \\
      --write-plan /tmp/srdi-dates-plan.json          # 干跑并生成计划
    # 复核快照与计划后
    systemd-run --pipe --wait --collect -p EnvironmentFile=/etc/huice/api.env \\
      -p WorkingDirectory=/opt/huice/app \\
      /opt/huice/venv/bin/python /opt/huice/app/infra/deploy/fix-published-effective-dates.py \\
      --apply --plan /tmp/srdi-dates-plan.json

退出码：0 成功（含「已是目标值、无需改动」）；2 数据或环境不合法/计划不一致；
3 写库失败；4 目标版本不是 `published`。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_DATA = SCRIPT_DIR / "srdi-effective-dates.json"
TRIGGER = "policy_version_guard_published"


class DataError(ValueError):
    """数据文件不合法。消息里只带字段路径。"""


def _date(path: str, value: Any, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        raise DataError(f"{path} 必须是 YYYY-MM-DD 字符串")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise DataError(f"{path} 必须是合法的 YYYY-MM-DD") from None
    return value


def load_entries(path: Path) -> list[dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise DataError(f"数据文件不存在：{path}") from None
    except json.JSONDecodeError as exc:
        raise DataError(f"数据文件不是合法 JSON（第 {exc.lineno} 行）") from None
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise DataError("数据文件根节点必须是对象且 version 为 1")
    entries = raw.get("entries")
    if not isinstance(entries, list) or not entries:
        raise DataError("entries 必须是非空数组")
    result = []
    for index, item in enumerate(entries):
        if not isinstance(item, dict):
            raise DataError(f"entries[{index}] 必须是对象")
        source_url = item.get("sourceUrl")
        version = item.get("version")
        if not isinstance(source_url, str) or not source_url.startswith("https://"):
            raise DataError(f"entries[{index}].sourceUrl 必须是 https 地址")
        if not isinstance(version, str) or not version.strip():
            raise DataError(f"entries[{index}].version 必须是非空字符串")
        old = item.get("expectedOld")
        new = item.get("new")
        if not isinstance(old, dict) or not isinstance(new, dict):
            raise DataError(f"entries[{index}] 必须含 expectedOld 与 new 两个对象")
        if old.get("reviewStatus") != "published":
            raise DataError(f"entries[{index}].expectedOld.reviewStatus 必须是 published")
        old_from = _date(f"entries[{index}].expectedOld.effectiveFrom", old.get("effectiveFrom"))
        old_to = _date(
            f"entries[{index}].expectedOld.effectiveTo",
            old.get("effectiveTo"),
            nullable=True,
        )
        new_from = _date(f"entries[{index}].new.effectiveFrom", new.get("effectiveFrom"))
        new_to = _date(
            f"entries[{index}].new.effectiveTo", new.get("effectiveTo"), nullable=True
        )
        if new_to is not None and new_to < new_from:
            raise DataError(f"entries[{index}].new.effectiveTo 不能早于 effectiveFrom")
        reason = item.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise DataError(f"entries[{index}].reason 必须是非空字符串")
        result.append(
            {
                "index": index,
                "sourceUrl": source_url,
                "version": version.strip(),
                "expectedOld": {
                    "reviewStatus": "published",
                    "effectiveFrom": old_from,
                    "effectiveTo": old_to,
                },
                "new": {"effectiveFrom": new_from, "effectiveTo": new_to},
                "reason": reason.strip(),
            }
        )
    return result


def _published_fingerprint(conn, *, exclude_ids: list[Any]) -> str | None:
    row = conn.execute(
        "SELECT md5(string_agg(id::text || ':' || xmin::text, ',' ORDER BY id)) "
        "FROM policy_version WHERE review_status = 'published' AND NOT (id = ANY(%s))",
        (exclude_ids or [],),
    ).fetchone()
    return None if row is None else row[0]


def _references(conn, version_id: Any, policy_id: Any) -> dict[str, int]:
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
        counts[table] = int(
            conn.execute(f"SELECT count(*) FROM {table} WHERE {column} = %s", (value,)).fetchone()[0]
        )
    return counts


def _find_version(conn, entry: dict[str, Any], *, lock: bool) -> dict[str, Any]:
    sql = (
        "SELECT id, policy_id, review_status, revision, xmin::text, published_at, "
        "effective_from, effective_to "
        "FROM policy_version WHERE source_url = %s AND version = %s"
    )
    if lock:
        sql += " FOR UPDATE"
    rows = conn.execute(sql, (entry["sourceUrl"], entry["version"])).fetchall()
    if len(rows) != 1:
        return {"count": len(rows)}
    return {
        "count": 1,
        "id": rows[0][0],
        "policyId": rows[0][1],
        "reviewStatus": rows[0][2],
        "revision": str(rows[0][3]),
        "xmin": rows[0][4],
        "publishedAt": rows[0][5].isoformat(),
        "effectiveFrom": rows[0][6].isoformat(),
        "effectiveTo": None if rows[0][7] is None else rows[0][7].isoformat(),
    }


def _load_plan(path: Path) -> tuple[str, dict[tuple[str, str], dict[str, Any]]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise DataError(f"计划文件不存在：{path}") from None
    except json.JSONDecodeError as exc:
        raise DataError(f"计划文件不是合法 JSON（第 {exc.lineno} 行）") from None
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise DataError("计划根节点必须是对象且 version 为 1")
    data_sha = raw.get("dataFileSha256")
    if not isinstance(data_sha, str) or len(data_sha) != 64:
        raise DataError("计划 dataFileSha256 必须是 64 位十六进制")
    entries = raw.get("entries")
    if not isinstance(entries, list) or not entries:
        raise DataError("计划 entries 必须是非空数组")
    plan: dict[tuple[str, str], dict[str, Any]] = {}
    for index, item in enumerate(entries):
        if not isinstance(item, dict):
            raise DataError(f"计划 entries[{index}] 必须是对象")
        key = (item.get("sourceUrl"), item.get("version"))
        if not all(isinstance(part, str) and part for part in key):
            raise DataError(f"计划 entries[{index}] 缺少 sourceUrl/version")
        for field in ("expectedOld", "expectedNew", "publishedFingerprint"):
            if field not in item:
                raise DataError(f"计划 entries[{index}].{field} 缺失")
        plan[key] = item
    return data_sha, plan


def _assert_guard_active(conn, published_id: Any) -> None:
    try:
        conn.execute(
            "UPDATE policy_version SET revision = gen_random_uuid() WHERE id = %s",
            (published_id,),
        )
    except Exception as exc:  # noqa: BLE001 - 只关心「被拒绝」
        conn.rollback()
        if exc.__class__.__name__ != "CheckViolation":
            raise RuntimeError(f"守卫以 {type(exc).__name__} 拒绝，预期 CheckViolation") from exc
        return
    conn.rollback()
    raise RuntimeError("守卫没有生效：已发布行居然可以被 UPDATE")


def run(
    data_path: Path,
    *,
    apply: bool = False,
    plan_path: Path | None = None,
    write_plan: Path | None = None,
) -> int:
    entries = load_entries(data_path)

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("缺少环境变量 DATABASE_URL（不打印任何取值）。", file=sys.stderr)
        return 2
    data_sha = hashlib.sha256(data_path.read_bytes()).hexdigest()

    plan_sha = None
    plan: dict[tuple[str, str], dict[str, Any]] = {}
    if apply:
        if plan_path is None:
            print("--apply 必须同时给 --plan（先干跑生成计划并复核）。", file=sys.stderr)
            return 2
        try:
            plan_sha, plan = _load_plan(plan_path)
        except DataError as exc:
            print(f"fix-published-effective-dates: 计划不合法：{exc}", file=sys.stderr)
            return 2
        if plan_sha != data_sha:
            print(
                "fix-published-effective-dates: 数据文件与计划生成时不一致（sha256 不同），"
                "整批不写——复核的是计划生成时的那份数据，请重新干跑并生成计划。",
                file=sys.stderr,
            )
            return 2

    try:
        import psycopg
    except ModuleNotFoundError as exc:
        print(f"缺少依赖 {exc.name}：请用项目虚拟环境的解释器运行。", file=sys.stderr)
        return 2

    logging.getLogger("psycopg.pool").setLevel(logging.CRITICAL)
    planned: list[dict[str, Any]] = []
    changed = 0
    changed_ids: list[Any] = []
    try:
        with psycopg.connect(dsn, autocommit=False) as conn:
            for entry in entries:
                found = _find_version(conn, entry, lock=apply)
                if found["count"] != 1:
                    print(
                        f"{entry['version']}：匹配到 {found['count']} 个版本，停止（不写任何行）。",
                        file=sys.stderr,
                    )
                    conn.rollback()
                    return 2
                if found["reviewStatus"] != "published":
                    print(
                        f"{entry['version']}：当前是 {found['reviewStatus']!r}，不是 published，"
                        "拒绝执行（本脚本只处理已发布行的受控例外）。",
                        file=sys.stderr,
                    )
                    conn.rollback()
                    return 4
                current = {
                    "reviewStatus": found["reviewStatus"],
                    "effectiveFrom": found["effectiveFrom"],
                    "effectiveTo": found["effectiveTo"],
                }
                if current != entry["expectedOld"]:
                    print(
                        f"{entry['version']}：当前有效期/状态与数据文件 expectedOld 不一致"
                        f"（库中 {current}），拒绝执行。",
                        file=sys.stderr,
                    )
                    conn.rollback()
                    return 2
                if entry["new"]["effectiveFrom"] < found["publishedAt"]:
                    print(
                        f"{entry['version']}：新 effectiveFrom {entry['new']['effectiveFrom']} "
                        f"早于 published_at {found['publishedAt']}，违反库约束，拒绝执行。",
                        file=sys.stderr,
                    )
                    conn.rollback()
                    return 2

                refs = _references(conn, found["id"], found["policyId"])
                print(
                    f"{entry['version']}：published，id {found['id']}，revision {found['revision']}，"
                    f"xmin {found['xmin']}，published_at {found['publishedAt']}，"
                    f"有效期 {found['effectiveFrom']}→{found['effectiveTo']}；引用："
                    + "、".join(f"{name}={count}" for name, count in refs.items())
                    + f"；理由：{entry['reason']}"
                )
                record = {
                    "sourceUrl": entry["sourceUrl"],
                    "version": entry["version"],
                    "expectedOld": {
                        "reviewStatus": found["reviewStatus"],
                        "effectiveFrom": found["effectiveFrom"],
                        "effectiveTo": found["effectiveTo"],
                        "revision": found["revision"],
                        "xmin": found["xmin"],
                    },
                    "expectedNew": dict(entry["new"]),
                    "publishedFingerprint": _published_fingerprint(
                        conn, exclude_ids=[found["id"]]
                    ),
                }

                if current["effectiveFrom"] == entry["new"]["effectiveFrom"] and current[
                    "effectiveTo"
                ] == entry["new"]["effectiveTo"]:
                    print(f"  已是目标值（{entry['new']}），无需改动。")
                    continue

                if apply:
                    approved = plan.get((entry["sourceUrl"], entry["version"]))
                    if approved is None:
                        conn.rollback()
                        print(
                            f"{entry['version']}：不在本次复核过的计划里，整批不写。",
                            file=sys.stderr,
                        )
                        return 3
                    if approved.get("expectedOld") != record["expectedOld"]:
                        conn.rollback()
                        print(
                            f"{entry['version']}：库中旧值与计划生成时不一致（可能被改写），"
                            "整批不写。",
                            file=sys.stderr,
                        )
                        return 3
                    if approved.get("expectedNew") != record["expectedNew"]:
                        conn.rollback()
                        print(
                            f"{entry['version']}：新值与计划不一致，整批不写。",
                            file=sys.stderr,
                        )
                        return 3
                    if approved.get("publishedFingerprint") != record["publishedFingerprint"]:
                        conn.rollback()
                        print(
                            "除目标行外的已发布行指纹与计划不一致，整批不写。",
                            file=sys.stderr,
                        )
                        return 3

                    conn.execute(f"ALTER TABLE policy_version DISABLE TRIGGER {TRIGGER}")
                    cursor = conn.execute(
                        "UPDATE policy_version SET effective_from = %s, effective_to = %s, "
                        "revision = gen_random_uuid(), updated_at = now() "
                        "WHERE id = %s AND review_status = 'published' AND revision = %s",
                        (
                            entry["new"]["effectiveFrom"],
                            entry["new"]["effectiveTo"],
                            found["id"],
                            found["revision"],
                        ),
                    )
                    conn.execute(f"ALTER TABLE policy_version ENABLE TRIGGER {TRIGGER}")
                    if cursor.rowcount != 1:
                        conn.rollback()
                        print(
                            f"{entry['version']}：UPDATE 命中 {cursor.rowcount} 行（预期 1），已回滚。",
                            file=sys.stderr,
                        )
                        return 3
                    changed += 1
                    changed_ids.append(found["id"])
                    after = _published_fingerprint(conn, exclude_ids=[found["id"]])
                    if after != record["publishedFingerprint"]:
                        conn.rollback()
                        print(
                            "除目标行外的已发布行指纹发生了变化，回滚（这不应当发生）。",
                            file=sys.stderr,
                        )
                        return 3
                    print(
                        f"  已改：有效期 {found['effectiveFrom']}→{found['effectiveTo']} 改为 "
                        f"{entry['new']['effectiveFrom']}→{entry['new']['effectiveTo']}，"
                        "revision 已换新。"
                    )
                else:
                    planned.append(record)

            if apply:
                conn.commit()
            else:
                conn.rollback()
    except Exception as exc:  # 不回显连接串与堆栈
        print(f"数据库操作失败（{type(exc).__name__}）。", file=sys.stderr)
        return 3

    if not apply and write_plan is not None:
        write_plan.write_text(
            json.dumps(
                {
                    "version": 1,
                    "dataFileSha256": data_sha,
                    "entries": planned,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"有效期修正计划已写入 {write_plan}（{len(planned)} 条）。")

    if apply and changed:
        try:
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
                _assert_guard_active(probe, row[0])
            print("守卫已验证恢复：对已发布行的 UPDATE 仍被拒绝。")
        except RuntimeError as exc:
            print(f"守卫验证失败：{exc}", file=sys.stderr)
            return 3

    if apply:
        print(f"已修正 {changed} 个已发布版本的有效期；未修改其它字段。")
    else:
        print("干跑完成，未写库。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="受控修正已发布版本的有效期（例外操作）")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--apply", action="store_true", help="真正写库；不加只打印快照")
    parser.add_argument("--plan", type=Path, help="干跑用 --write-plan 生成；--apply 必须给")
    parser.add_argument("--write-plan", type=Path, help="把干跑快照写成计划文件")
    args = parser.parse_args(argv)
    try:
        return run(
            args.data,
            apply=args.apply,
            plan_path=args.plan,
            write_plan=args.write_plan,
        )
    except DataError as exc:
        print(f"fix-published-effective-dates: 数据不合法：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
