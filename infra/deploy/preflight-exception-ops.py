#!/usr/bin/env python3
"""例外操作（`v1.0` 降级 / SRDI 的 B）执行前的只读快照。

只读：不取锁、不写库。输出给执行者与 M3/M2 回告用，内容包括：

- `schema_migration` 已执行的迁移；
- `policy_version` 总数与按状态计数（M3 第三轮口径：12 行 / 8 已发布 / 4 未发布）；
- 全部已发布行（id、版本、状态、有效期、revision、xmin、来源行数）；
- 两条目标行：测试版本 `v1.0`、SRDI `深工信规〔2025〕8号`，含引用计数与来源逐行；
- SRDI 的 `policy_version_date_provenance` 现值（011 已执行时）；
- 两个守卫触发器是否处于启用状态（`tgenabled = 'O'`）；
- 「排除目标行」的已发布行 `xmin` 指纹，用于执行前后比对。

用法（ECS，venv 解释器）::

    systemd-run --pipe --wait --collect -p EnvironmentFile=/etc/huice/api.env \\
      -p WorkingDirectory=/opt/huice/app \\
      /opt/huice/venv/bin/python /opt/huice/app/infra/deploy/preflight-exception-ops.py

退出码：0 成功；2 缺少依赖或环境；3 查询失败。
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

TEST_VERSION_URL = "https://www.sz.gov.cn/zwgk/zfxxgkml/zcfg/content/post_1.html"
TEST_VERSION = "v1.0"
SRDI_URL = "https://www.sz.gov.cn/cn/xxgk/zfxxgj/tzgg/content/post_12433720.html"
SRDI_VERSION = "深工信规〔2025〕8号"
GUARDS = ("policy_version_guard_published", "policy_source_guard_published")


def _table_exists(conn, name: str) -> bool:
    return bool(conn.execute("SELECT to_regclass(%s) IS NOT NULL", (name,)).fetchone()[0])


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
        if not _table_exists(conn, table):
            counts[table] = 0
            continue
        counts[table] = int(
            conn.execute(f"SELECT count(*) FROM {table} WHERE {column} = %s", (value,)).fetchone()[0]
        )
    return counts


def _fingerprint(conn, *, exclude_ids: list[Any]) -> str | None:
    row = conn.execute(
        "SELECT md5(string_agg(id::text || ':' || xmin::text, ',' ORDER BY id)) "
        "FROM policy_version WHERE review_status = 'published' AND NOT (id = ANY(%s))",
        (exclude_ids or [],),
    ).fetchone()
    return None if row is None else row[0]


def _target(conn, *, source_url: str, version: str) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT id, policy_id, version, review_status, revision, xmin::text, published_at, "
        "effective_from, effective_to, reviewed_at "
        "FROM policy_version WHERE source_url = %s AND version = %s",
        (source_url, version),
    ).fetchall()
    if len(rows) != 1:
        return {"matched": len(rows)}
    row = rows[0]
    version_id, policy_id = row[0], row[1]
    sources = conn.execute(
        "SELECT id, kind, ordinal, file_name, source_url, content_hash, byte_size "
        "FROM policy_source WHERE policy_version_id = %s ORDER BY ordinal",
        (version_id,),
    ).fetchall()
    provenance = None
    if _table_exists(conn, "policy_version_date_provenance"):
        found = conn.execute(
            "SELECT provenance, updated_at FROM policy_version_date_provenance "
            "WHERE policy_version_id = %s",
            (version_id,),
        ).fetchone()
        if found is not None:
            provenance = {"provenance": found[0], "updatedAt": found[1].isoformat()}
    return {
        "matched": 1,
        "id": str(version_id),
        "policyId": str(policy_id),
        "version": row[2],
        "reviewStatus": row[3],
        "revision": str(row[4]),
        "xmin": row[5],
        "publishedAt": row[6].isoformat(),
        "effectiveFrom": row[7].isoformat(),
        "effectiveTo": None if row[8] is None else row[8].isoformat(),
        "reviewedAt": None if row[9] is None else row[9].isoformat(),
        "references": _references(conn, version_id, policy_id),
        "sources": [
            {
                "id": str(source[0]),
                "kind": source[1],
                "ordinal": int(source[2]),
                "fileName": source[3],
                "sourceUrl": source[4],
                "contentHash": source[5],
                "byteSize": int(source[6]),
            }
            for source in sources
        ],
        "provenance": provenance,
    }


def collect(conn) -> dict[str, Any]:
    migrations = (
        [row[0] for row in conn.execute("SELECT id FROM schema_migration ORDER BY id").fetchall()]
        if _table_exists(conn, "schema_migration")
        else []
    )
    counts = dict(
        conn.execute(
            "SELECT review_status, count(*) FROM policy_version GROUP BY review_status"
        ).fetchall()
    )
    total = int(conn.execute("SELECT count(*) FROM policy_version").fetchone()[0])
    published = [
        {
            "id": str(row[0]),
            "version": row[1],
            "effectiveFrom": row[2].isoformat(),
            "effectiveTo": None if row[3] is None else row[3].isoformat(),
            "revision": str(row[4]),
            "xmin": row[5],
            "sourceCount": int(row[6]),
        }
        for row in conn.execute(
            "SELECT pv.id, pv.version, pv.effective_from, pv.effective_to, pv.revision, "
            "pv.xmin::text, (SELECT count(*) FROM policy_source ps WHERE ps.policy_version_id = pv.id) "
            "FROM policy_version pv WHERE review_status = 'published' ORDER BY pv.version"
        ).fetchall()
    ]
    guards = {
        row[0]: row[1]
        for row in conn.execute(
            "SELECT tgname, tgenabled FROM pg_trigger WHERE tgname = ANY(%s)", (list(GUARDS),)
        ).fetchall()
    }
    test = _target(conn, source_url=TEST_VERSION_URL, version=TEST_VERSION)
    srdi = _target(conn, source_url=SRDI_URL, version=SRDI_VERSION)
    exclude = [item["id"] for item in (test, srdi) if item.get("matched") == 1]
    return {
        "migrations": migrations,
        "policyVersions": {
            "total": total,
            "byStatus": {key: int(value) for key, value in counts.items()},
        },
        "published": published,
        "guards": guards,
        "targets": {"testVersion": test, "srdi": srdi},
        "publishedFingerprintExcludingTargets": _fingerprint(conn, exclude_ids=exclude),
    }


def main() -> int:
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
    try:
        with psycopg.connect(dsn, autocommit=False) as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            snapshot = collect(conn)
    except Exception as exc:  # 不回显连接串与堆栈
        print(f"查询失败（{type(exc).__name__}）。", file=sys.stderr)
        return 3

    print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
