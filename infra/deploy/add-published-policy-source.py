#!/usr/bin/env python3
"""给一个**已发布**政策版本补一条来源行（受控例外，SRDI 的 B 用）。

背景：SRDI（深工信规〔2025〕8号）的版本行对应实施细则，但 `source_url` 与
`policy_source` 只有 2025 年度申报通知（`post_12433720`）。细则第二十四条
「自印发之日起施行，有效期5年」不在该版本的任何来源里，因此有效期没有可绑定的出处。
M3 同意补一条细则正文来源，但要求先贴行形状；本脚本把「补哪一行」固定成可复核的形状。

`001` 的 `policy_source_guard_published` 拒绝给已发布版本增删改子表，所以本脚本走
**受控例外**：

1. 只处理数据文件里逐条列出的 `(targetSourceUrl, targetVersion)`，且必须当前是
   `published`；`kind` 只允许 `attachment`（每个版本只有一份 body，已被申报通知占用）；
2. 干跑打印版本快照、现有来源逐行、拟插入行的完整形状（`ordinal` 由现有来源的
   最大 ordinal + 1 得出）、引用计数；`--write-plan` 把数据文件摘要、现有来源集合、
   拟插入行固定下来，正式执行逐项比对，任一变化整批回滚；
3. 入库的是**提取文本**：`.txt` / `text/plain`，`content_hash` 与 `byte_size` 都按
   文本字节算；原始页面/附件哈希只作为证据打印与记录，不冒充文本哈希；
4. 写入在同一事务里 `DISABLE TRIGGER` → `INSERT` → `ENABLE TRIGGER`：
   `ALTER TABLE` 取 ACCESS EXCLUSIVE 锁，事务提交前别人进不来；
5. 操作前后比对「排除目标版本」的已发布行 `xmin` 指纹；提交后另开连接对刚插入的
   来源行试一次 UPDATE，必须被守卫拒绝（`CheckViolation`），证明守卫已恢复。

用法（ECS，venv 解释器）::

    PYTHONPATH=/opt/huice/app/services/api \\
      /opt/huice/venv/bin/python /opt/huice/app/infra/deploy/add-published-policy-source.py \\
      --write-plan /tmp/srdi-source-plan.json            # 干跑并生成计划
    # 把干跑打印的行形状与计划交给 M3/M2 复核，同意后再执行
    PYTHONPATH=/opt/huice/app/services/api \\
      /opt/huice/venv/bin/python /opt/huice/app/infra/deploy/add-published-policy-source.py \\
      --apply --plan /tmp/srdi-source-plan.json

退出码：0 成功（含「同一来源已存在、无需改动」）；2 数据或环境不合法；
3 写库失败/计划不一致；4 目标版本不是 `published`。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_DATA = SCRIPT_DIR / "srdi-detail-source.json"
TRIGGER = "policy_source_guard_published"
MAX_BYTES = 10485760


class DataError(ValueError):
    """数据文件不合法。消息里只带字段路径。"""


def _require_text(path: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DataError(f"{path} 必须是非空字符串")
    return value.strip()


def _load_source(raw: Any, base: Path) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise DataError("entries[].source 必须是对象")
    kind = raw.get("kind")
    if kind != "attachment":
        raise DataError("entries[].source.kind 只能是 attachment（body 已被申报通知占用）")
    url = _require_text("entries[].source.url", raw.get("url"))
    if not url.startswith("https://"):
        raise DataError("entries[].source.url 必须是 https 地址")
    title = _require_text("entries[].source.title", raw.get("title"))
    file_name = _require_text("entries[].source.file", raw.get("file"))
    path = (base / file_name).resolve()
    if not path.is_file():
        raise DataError(f"entries[].source.file 指向的文件不存在：{path}")
    text = path.read_text(encoding="utf-8")
    # 统一换行：Windows 检出可能把 LF 写成 CRLF；不归一化会让同一条来源在本地与
    # ECS 上算出两个 content_hash，计划与实际入库对不上。
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        raise DataError("entries[].source.file 是空文件")
    content = text.encode("utf-8")
    if len(content) > MAX_BYTES:
        raise DataError("entries[].source.file 超过 10 MiB")
    original_name = _require_text(
        "entries[].source.originalName", raw.get("originalName")
    )
    original_sha = _require_text(
        "entries[].source.originalSha256", raw.get("originalSha256")
    )
    if len(original_sha) != 64 or any(ch not in "0123456789abcdef" for ch in original_sha):
        raise DataError("entries[].source.originalSha256 必须是 64 位小写十六进制")
    original_size = raw.get("originalByteSize")
    if not isinstance(original_size, int) or isinstance(original_size, bool) or original_size <= 0:
        raise DataError("entries[].source.originalByteSize 必须是正整数")
    fetched_at = _require_text("entries[].source.fetchedAt", raw.get("fetchedAt"))
    try:
        parsed = datetime.fromisoformat(fetched_at)
    except ValueError:
        raise DataError("entries[].source.fetchedAt 必须是 ISO 8601 时间") from None
    if parsed.tzinfo is None:
        raise DataError("entries[].source.fetchedAt 必须带时区")
    return {
        "kind": kind,
        "url": url,
        "title": title,
        "path": path,
        "text": text,
        "contentHash": hashlib.sha256(content).hexdigest(),
        "byteSize": len(content),
        "fileName": f"{original_name}.txt",
        "originalName": original_name,
        "originalSha256": original_sha,
        "originalByteSize": original_size,
        "fetchedAt": fetched_at,
    }


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
        target_url = _require_text(f"entries[{index}].targetSourceUrl", item.get("targetSourceUrl"))
        if not target_url.startswith("https://"):
            raise DataError(f"entries[{index}].targetSourceUrl 必须是 https 地址")
        target_version = _require_text(f"entries[{index}].targetVersion", item.get("targetVersion"))
        reason = _require_text(f"entries[{index}].reason", item.get("reason"))
        result.append(
            {
                "index": index,
                "targetSourceUrl": target_url,
                "targetVersion": target_version,
                "reason": reason,
                "source": _load_source(item.get("source"), path.parent),
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


def _sources(conn, version_id: Any) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, kind, ordinal, file_name, source_url, content_hash, byte_size "
        "FROM policy_source WHERE policy_version_id = %s ORDER BY ordinal",
        (version_id,),
    ).fetchall()
    return [
        {
            "id": str(row[0]),
            "kind": row[1],
            "ordinal": int(row[2]),
            "fileName": row[3],
            "sourceUrl": row[4],
            "contentHash": row[5],
            "byteSize": int(row[6]),
        }
        for row in rows
    ]


def _find_version(conn, entry: dict[str, Any], *, lock: bool) -> dict[str, Any] | None:
    sql = (
        "SELECT id, policy_id, review_status, revision, xmin::text, source_url, version "
        "FROM policy_version WHERE source_url = %s AND version = %s"
    )
    if lock:
        sql += " FOR UPDATE"
    rows = conn.execute(sql, (entry["targetSourceUrl"], entry["targetVersion"])).fetchall()
    if len(rows) != 1:
        return {"count": len(rows)}
    return {
        "count": 1,
        "id": rows[0][0],
        "policyId": rows[0][1],
        "reviewStatus": rows[0][2],
        "revision": str(rows[0][3]),
        "xmin": rows[0][4],
        "sourceUrl": rows[0][5],
        "version": rows[0][6],
    }


def _planned_ordinal(sources: list[dict[str, Any]]) -> int:
    if not sources:
        return 1
    return max(source["ordinal"] for source in sources) + 1


def _same_source_exists(sources: list[dict[str, Any]], source: dict[str, Any]) -> bool:
    return any(
        row["sourceUrl"] == source["url"] and row["contentHash"] == source["contentHash"]
        for row in sources
    )


def _conflicting_source(sources: list[dict[str, Any]], source: dict[str, Any]) -> bool:
    return any(
        row["sourceUrl"] == source["url"] and row["contentHash"] != source["contentHash"]
        for row in sources
    )


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
        key = (item.get("targetSourceUrl"), item.get("targetVersion"))
        if not all(isinstance(part, str) and part for part in key):
            raise DataError(f"计划 entries[{index}] 缺少 targetSourceUrl/targetVersion")
        for field in ("expectedVersion", "expectedNewSource", "expectedSources", "publishedFingerprint"):
            if field not in item:
                raise DataError(f"计划 entries[{index}].{field} 缺失")
        plan[key] = item
    return data_sha, plan


def _build_store():
    from app.config import Settings
    from app.integrations.obs import build_object_store

    return build_object_store(Settings.from_env())


def run(
    data_path: Path,
    *,
    apply: bool = False,
    plan_path: Path | None = None,
    write_plan: Path | None = None,
    store=None,
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
            print(f"add-published-policy-source: 计划不合法：{exc}", file=sys.stderr)
            return 2
        if plan_sha != data_sha:
            print(
                "add-published-policy-source: 数据文件与计划生成时不一致（sha256 不同），"
                "整批不写——复核的是计划生成时的那份数据，请重新干跑并生成计划。",
                file=sys.stderr,
            )
            return 2

    try:
        import psycopg
    except ModuleNotFoundError as exc:
        print(f"缺少依赖 {exc.name}：请用项目虚拟环境的解释器运行。", file=sys.stderr)
        return 2

    if apply and store is None:
        try:
            store = _build_store()
        except Exception as exc:  # 只报类名，不回显配置
            print(f"初始化对象存储失败（{type(exc).__name__}）。", file=sys.stderr)
            return 2

    logging.getLogger("psycopg.pool").setLevel(logging.CRITICAL)
    planned: list[dict[str, Any]] = []
    inserted_ids: list[str] = []
    inserted = 0
    try:
        with psycopg.connect(dsn, autocommit=False) as conn:
            if apply:
                # 先锁住来源集合的写路径，再读现有来源；新增、删除、重排都会被挡住。
                conn.execute("LOCK TABLE policy_source IN SHARE ROW EXCLUSIVE MODE")

            for entry in entries:
                found = _find_version(conn, entry, lock=apply)
                if found["count"] != 1:
                    print(
                        f"{entry['targetVersion']}：匹配到 {found['count']} 个版本，停止（不写任何行）。",
                        file=sys.stderr,
                    )
                    conn.rollback()
                    return 2
                if found["reviewStatus"] != "published":
                    print(
                        f"{entry['targetVersion']}：当前是 {found['reviewStatus']!r}，不是 published，"
                        "拒绝执行（本脚本只给已发布版本补来源）。",
                        file=sys.stderr,
                    )
                    conn.rollback()
                    return 4

                sources = _sources(conn, found["id"])
                source = entry["source"]
                if _conflicting_source(sources, source):
                    print(
                        f"{entry['targetVersion']}：同一 source_url 已有内容不同的来源行，"
                        "拒绝覆盖（本脚本只新增）。",
                        file=sys.stderr,
                    )
                    conn.rollback()
                    return 3
                if _same_source_exists(sources, source):
                    print(
                        f"{entry['targetVersion']}：同一来源（URL + 内容哈希）已在库中，"
                        "无需改动。"
                    )
                    continue

                ordinal = _planned_ordinal(sources)
                refs = _references(conn, found["id"], found["policyId"])
                print(
                    f"{entry['targetVersion']}：published，id {found['id']}，revision "
                    f"{found['revision']}，xmin {found['xmin']}；现有来源 {len(sources)} 条"
                    + (
                        "（" + "、".join(f"{s['kind']}#{s['ordinal']} {s['fileName']}" for s in sources) + "）"
                        if sources
                        else ""
                    )
                    + "；引用："
                    + "、".join(f"{name}={count}" for name, count in refs.items())
                )
                print(
                    f"  拟插入行：kind=attachment ordinal={ordinal} "
                    f"file_name={source['fileName']} source_url={source['url']} "
                    f"content_hash={source['contentHash']} byte_size={source['byteSize']}；"
                    f"原件 {source['originalName']} {source['originalByteSize']} B "
                    f"SHA-256 {source['originalSha256']}；理由：{entry['reason']}"
                )

                record = {
                    "targetSourceUrl": entry["targetSourceUrl"],
                    "targetVersion": entry["targetVersion"],
                    "expectedVersion": {
                        "id": str(found["id"]),
                        "reviewStatus": found["reviewStatus"],
                        "revision": found["revision"],
                        "xmin": found["xmin"],
                        "sourceCount": len(sources),
                    },
                    "expectedSources": sources,
                    "expectedNewSource": {
                        "kind": "attachment",
                        "ordinal": ordinal,
                        "fileName": source["fileName"],
                        "sourceUrl": source["url"],
                        "title": source["title"],
                        "contentHash": source["contentHash"],
                        "byteSize": source["byteSize"],
                    },
                    "publishedFingerprint": _published_fingerprint(
                        conn, exclude_ids=[found["id"]]
                    ),
                }

                if apply:
                    approved = plan.get((entry["targetSourceUrl"], entry["targetVersion"]))
                    if approved is None:
                        conn.rollback()
                        print(
                            f"{entry['targetVersion']}：不在本次复核过的计划里，整批不写。",
                            file=sys.stderr,
                        )
                        return 3
                    if approved.get("expectedVersion") != record["expectedVersion"]:
                        conn.rollback()
                        print(
                            f"{entry['targetVersion']}：版本快照与计划不一致，整批不写。",
                            file=sys.stderr,
                        )
                        return 3
                    if approved.get("expectedSources") != record["expectedSources"]:
                        conn.rollback()
                        print(
                            f"{entry['targetVersion']}：来源集合与计划不一致（新增/删除/重排），"
                            "整批不写。",
                            file=sys.stderr,
                        )
                        return 3
                    if approved.get("expectedNewSource") != record["expectedNewSource"]:
                        conn.rollback()
                        print(
                            f"{entry['targetVersion']}：拟插入行与计划不一致，整批不写。",
                            file=sys.stderr,
                        )
                        return 3
                    if approved.get("publishedFingerprint") != record["publishedFingerprint"]:
                        conn.rollback()
                        print(
                            "除目标版本外的已发布行指纹与计划不一致，整批不写。",
                            file=sys.stderr,
                        )
                        return 3

                    storage_key = store.put(source["text"].encode("utf-8"), suffix=".txt")
                    new_id = uuid4()
                    conn.execute(f"ALTER TABLE policy_source DISABLE TRIGGER {TRIGGER}")
                    cursor = conn.execute(
                        "INSERT INTO policy_source (id, policy_version_id, kind, ordinal, "
                        "source_url, title, file_name, mime_type, fetched_at, content_hash, "
                        "byte_size, text, storage_key) "
                        "VALUES (%s, %s, 'attachment', %s, %s, %s, %s, 'text/plain', %s, %s, %s, %s, %s)",
                        (
                            new_id,
                            found["id"],
                            ordinal,
                            source["url"],
                            source["title"],
                            source["fileName"],
                            source["fetchedAt"],
                            source["contentHash"],
                            source["byteSize"],
                            source["text"],
                            storage_key,
                        ),
                    )
                    conn.execute(f"ALTER TABLE policy_source ENABLE TRIGGER {TRIGGER}")
                    if cursor.rowcount != 1:
                        conn.rollback()
                        print(
                            f"{entry['targetVersion']}：INSERT 命中 {cursor.rowcount} 行（预期 1），"
                            "已回滚。",
                            file=sys.stderr,
                        )
                        return 3
                    inserted += 1
                    inserted_ids.append(str(new_id))
                    after = _published_fingerprint(conn, exclude_ids=[found["id"]])
                    if after != record["publishedFingerprint"]:
                        conn.rollback()
                        print(
                            "除目标版本外的已发布行指纹发生了变化，回滚（这不应当发生）。",
                            file=sys.stderr,
                        )
                        return 3
                    print(
                        f"  已插入：{source['fileName']}（ordinal {ordinal}，{source['byteSize']} B），"
                        f"来源数 {len(sources)} → {len(sources) + 1}。"
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
        print(f"来源补录计划已写入 {write_plan}（{len(planned)} 条）。")

    if apply and inserted:
        try:
            with psycopg.connect(dsn, autocommit=False) as probe:
                try:
                    probe.execute(
                        "UPDATE policy_source SET title = title WHERE id = %s",
                        (inserted_ids[0],),
                    )
                except Exception as exc:  # noqa: BLE001 - 只关心「被拒绝」
                    probe.rollback()
                    if exc.__class__.__name__ != "CheckViolation":
                        raise RuntimeError(
                            f"守卫以 {type(exc).__name__} 拒绝，预期 CheckViolation"
                        ) from exc
                else:
                    probe.rollback()
                    raise RuntimeError("守卫没有生效：已发布版本的来源行居然可以被 UPDATE")
            print("守卫已验证恢复：对已发布版本来源行的 UPDATE 仍被拒绝。")
        except Exception as exc:
            print(f"守卫验证失败：{exc}", file=sys.stderr)
            return 3

    if apply:
        print(f"已补 {inserted} 条来源行；未修改任何 policy_version 行。")
    else:
        print("干跑完成，未写库。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="给已发布版本补一条来源行（受控例外）")
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
        print(f"add-published-policy-source: 数据不合法：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
