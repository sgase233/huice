"""政策发现的仓储：配置、运行记录、候选暂存区（E2，#47）。

与 :mod:`app.integrations.storage` 分开：政策库是**已审核**的正式数据，发现候选是
**未审核**的暂存数据，两者的写路径、可见范围与保留期都不同；混在一个类里会让
「候选为什么不能出现在政策列表」更难说清。

两条不变量靠数据库而不是靠调用顺序保证：

1. **候选不进政策库**：本模块只写 ``policy_candidate*`` / ``discovery_*``，任何一条
   SQL 都不碰 ``policy`` / ``policy_version`` / ``criterion``。提交候选时改由
   :class:`app.integrations.storage.PolicyRepository` 走既有导入链路写政策。
2. **轮换是一个事务**（E0 D3.5）：置旧候选为 ``expired``、插入新的运行行、推进
   ``next_run_at`` 三件事在同一个 ``conn.transaction()`` 里提交。运行器把这条事务
   跑在持锁的那条连接上，所以「锁还在、候选却已经轮换」这种中间态不存在。

运行器需要「锁与写共用一条连接」（D3.1），因此写方法都显式接收 ``conn``；
HTTP 层用的读方法自己从连接池取连接。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any, Sequence
from uuid import UUID

from psycopg import Connection
from psycopg.rows import dict_row

from ..db import Database
from ..errors import ApiProblem

_SETTINGS_COLUMNS = (
    "id, enabled, source_urls, interval_minutes, time_zone, next_run_at, revision, updated_at"
)

_RUN_COLUMNS = (
    "id, trigger, started_at, finished_at, status, terminal_reason, sources_checked, "
    "links_seen, duplicates, new_candidates, failed_sources, failed_articles, tokens_used, "
    "tokens_unknown, late_by_seconds, source_status, detail"
)

_CANDIDATE_COLUMNS = (
    "id, run_id, revision, status, source_url, fetched_at, content_hash, raw_hash, "
    "storage_key, body_text, policy_id, version, title, policy_type, issuer, region, "
    "document_number, "
    "applicable_entities, published_at, effective_from, effective_to, application_window, "
    "official_entry_url, attachment_urls, unsupported_attachment_urls, missing_fields, "
    "criteria, validation_findings, "
    "extraction, duplicate_of, existing_policy_id, existing_version, version_changed, "
    "submitted_policy_id, submitted_version, submitted_at, discarded_at, expired_at, "
    "discard_reason, created_at, updated_at"
)


class DiscoveryRepository:
    def __init__(self, db: Database, cursor_secret: str) -> None:
        self._db = db
        self._secret = cursor_secret.encode("utf-8")

    # ------------------------------------------------------------------ 配置

    def get_settings(self) -> dict[str, Any]:
        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"SELECT {_SETTINGS_COLUMNS} FROM discovery_settings WHERE id = 1"
            )
            row = cur.fetchone()
        if row is None:  # 迁移已保证单行存在；缺行说明库没迁移，不能静默造一条
            raise ApiProblem(503, "DEPENDENCY_UNAVAILABLE", "发现配置尚未初始化。")
        return row

    def update_settings(
        self,
        *,
        expected_revision: UUID,
        enabled: bool,
        source_urls: Sequence[str],
        interval_minutes: int,
        time_zone: str,
    ) -> dict[str, Any]:
        """``If-Match`` 是乐观锁：修订号对不上就 409，不覆盖别人的修改。"""

        with self._db.connection() as conn, conn.transaction():
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "UPDATE discovery_settings SET enabled = %s, source_urls = %s, "
                    "interval_minutes = %s, time_zone = %s, revision = gen_random_uuid(), "
                    "updated_at = now() "
                    "WHERE id = 1 AND revision = %s "
                    f"RETURNING {_SETTINGS_COLUMNS}",
                    (
                        enabled,
                        json.dumps(list(source_urls)),
                        interval_minutes,
                        time_zone,
                        expected_revision,
                    ),
                )
                row = cur.fetchone()
                current = None
                if row is None:
                    cur.execute(
                        f"SELECT {_SETTINGS_COLUMNS} FROM discovery_settings WHERE id = 1"
                    )
                    current = cur.fetchone()
        if row is not None:
            return row
        raise ApiProblem(
            409,
            "VERSION_CONFLICT",
            "发现配置已被更新，请重新读取后再保存。",
            {"currentRevision": str(current["revision"]) if current else None},
        )

    # ------------------------------------------------------------- 运行记录

    def list_runs(self, *, cursor: str | None, limit: int) -> dict[str, Any]:
        fingerprint = "runs"
        params: list[Any] = []
        where = ""
        if cursor:
            started_at, last_id = self._decode_cursor(cursor, fingerprint)
            where = "WHERE (started_at, id) < (%s, %s)"
            params += [started_at, last_id]
        params.append(limit + 1)
        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"SELECT {_RUN_COLUMNS} FROM discovery_run {where} "
                "ORDER BY started_at DESC, id DESC LIMIT %s",
                tuple(params),
            )
            rows = cur.fetchall()
        next_cursor = None
        if len(rows) > limit:
            rows = rows[:limit]
            tail = rows[-1]
            next_cursor = self._encode_cursor(tail["started_at"], tail["id"], fingerprint)
        return {"items": rows, "next_cursor": next_cursor}

    def get_run(self, run_id: UUID) -> dict[str, Any] | None:
        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(f"SELECT {_RUN_COLUMNS} FROM discovery_run WHERE id = %s", (run_id,))
            return cur.fetchone()

    # --------------------------------------------------------------- 候选读

    def list_candidates(
        self, *, status: str | None, cursor: str | None, limit: int
    ) -> dict[str, Any]:
        fingerprint = f"candidates:{status or ''}"
        params: list[Any] = []
        clauses: list[str] = []
        if status:
            clauses.append("status = %s")
            params.append(status)
        if cursor:
            created_at, last_id = self._decode_cursor(cursor, fingerprint)
            clauses.append("(created_at, id) < (%s, %s)")
            params += [created_at, last_id]
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit + 1)
        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"SELECT {_CANDIDATE_COLUMNS} FROM policy_candidate {where} "
                "ORDER BY created_at DESC, id DESC LIMIT %s",
                tuple(params),
            )
            rows = cur.fetchall()
        next_cursor = None
        if len(rows) > limit:
            rows = rows[:limit]
            tail = rows[-1]
            next_cursor = self._encode_cursor(tail["created_at"], tail["id"], fingerprint)
        return {"items": rows, "next_cursor": next_cursor}

    def get_candidate(self, candidate_id: UUID) -> dict[str, Any] | None:
        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"SELECT {_CANDIDATE_COLUMNS} FROM policy_candidate WHERE id = %s",
                (candidate_id,),
            )
            return cur.fetchone()

    # ------------------------------------------------- 运行器写入（显式连接）

    def rotate_candidates(
        self, conn: Connection, *, now: datetime
    ) -> int:
        """把上一批仍待处理的候选置为 ``expired``；返回影响行数。

        失败运行也照样轮换：页面显示本轮失败原因，而不是把过期候选变回可提交
        （EXTENSION_INTERFACES §4.1 最后一段）。
        """

        with conn.cursor() as cur:
            cur.execute(
                "UPDATE policy_candidate SET status = 'expired', expired_at = %s, "
                "updated_at = %s "
                "WHERE status IN ('pending_review', 'extraction_failed')",
                (now, now),
            )
            expired = cur.rowcount
            cur.execute(
                "UPDATE policy_candidate_fingerprint SET outcome = 'expired' "
                "WHERE outcome = 'pending'"
            )
        return expired

    def start_run(
        self,
        conn: Connection,
        *,
        run_id: UUID,
        started_at: datetime,
        trigger: str,
        next_run_at: datetime,
        late_by_seconds: int | None,
    ) -> None:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO discovery_run (id, trigger, started_at, status, late_by_seconds) "
                "VALUES (%s, %s, %s, 'running', %s)",
                (run_id, trigger, started_at, late_by_seconds),
            )
            cur.execute(
                "UPDATE discovery_settings SET next_run_at = %s WHERE id = 1", (next_run_at,)
            )

    def finish_run(
        self,
        conn: Connection,
        *,
        run_id: UUID,
        status: str,
        terminal_reason: str,
        finished_at: datetime,
        sources_checked: int,
        links_seen: int,
        duplicates: int,
        new_candidates: int,
        failed_sources: int,
        failed_articles: int,
        tokens_used: int,
        tokens_unknown: bool,
        source_status: list[dict[str, Any]],
        detail: str | None,
    ) -> None:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE discovery_run SET status = %s, terminal_reason = %s, "
                "finished_at = %s, sources_checked = %s, links_seen = %s, duplicates = %s, "
                "new_candidates = %s, failed_sources = %s, failed_articles = %s, "
                "tokens_used = %s, "
                "tokens_unknown = %s, source_status = %s, detail = %s "
                "WHERE id = %s",
                (
                    status,
                    terminal_reason,
                    finished_at,
                    sources_checked,
                    links_seen,
                    duplicates,
                    new_candidates,
                    failed_sources,
                    failed_articles,
                    tokens_used,
                    tokens_unknown,
                    json.dumps(source_status, ensure_ascii=False),
                    detail,
                    run_id,
                ),
            )

    def find_fingerprint(
        self, conn: Connection, content_hash: str
    ) -> dict[str, Any] | None:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT content_hash, canonical_url, outcome, candidate_id, "
                "submitted_policy_id, first_seen_at, last_seen_at "
                "FROM policy_candidate_fingerprint WHERE content_hash = %s",
                (content_hash,),
            )
            return cur.fetchone()

    def find_fingerprint_by_body(
        self, conn: Connection, body_fingerprint: str, *, exclude_url: str | None = None
    ) -> dict[str, Any] | None:
        """按「正文句子集合」指纹找已经见过的内容（换 URL 同内容判重）。

        与 ``content_hash`` 不同，这个指纹不含导航、页脚，因此同一份公告被不同站点
        转载时仍然相同。``exclude_url`` 用来排除同一个地址：同一 URL 内容变了是新版本，
        不是转载，调用方不该拿旧指纹把它当重复跳过。

        旧数据（此列加入前写入的指纹行）为 NULL，不参与匹配。
        """

        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT content_hash, canonical_url, outcome, candidate_id, "
                "submitted_policy_id, first_seen_at, last_seen_at "
                "FROM policy_candidate_fingerprint WHERE body_fingerprint = %s "
                "AND (%s::text IS NULL OR canonical_url <> %s) "
                "ORDER BY first_seen_at LIMIT 1",
                (body_fingerprint, exclude_url, exclude_url),
            )
            return cur.fetchone()

    def record_fingerprint(
        self,
        conn: Connection,
        *,
        content_hash: str,
        canonical_url: str,
        now: datetime,
        outcome: str,
        candidate_id: UUID | None,
        submitted_policy_id: UUID | None = None,
        body_fingerprint: str | None = None,
    ) -> None:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO policy_candidate_fingerprint "
                "(content_hash, body_fingerprint, canonical_url, first_seen_at, last_seen_at, "
                " outcome, candidate_id, submitted_policy_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (content_hash) DO UPDATE SET "
                "last_seen_at = EXCLUDED.last_seen_at, outcome = EXCLUDED.outcome, "
                "candidate_id = EXCLUDED.candidate_id, "
                "body_fingerprint = COALESCE("
                "EXCLUDED.body_fingerprint, policy_candidate_fingerprint.body_fingerprint)",
                (
                    content_hash,
                    body_fingerprint,
                    canonical_url,
                    now,
                    now,
                    outcome,
                    candidate_id,
                    submitted_policy_id,
                ),
            )

    def close_stale_runs(self, conn: Connection, *, now: datetime) -> int:
        """把上一个进程遗留的 ``running`` 行收尾成 ``failed / lock_lost``。

        调用点已经持住运行锁，所以此刻不存在「另一个进程正在跑」：任何仍为
        ``running`` 的行都来自崩溃/被杀的进程——它的连接已断，锁也已释放，未提交的
        写入按 PostgreSQL 的语义一并回滚。把「进程没了、行还是运行中」留在列表里，
        页面上就会永远显示「运行中」，反而掩盖了真实原因。

        这条 UPDATE 必须在持锁连接上执行（D3.1）：它本身就是本轮写入的一部分。
        """

        with conn.cursor() as cur:
            cur.execute(
                "UPDATE discovery_run SET status = 'failed', terminal_reason = 'lock_lost', "
                "finished_at = %s, "
                "detail = COALESCE(detail, '进程在运行结束前退出，已由下一次运行收尾。') "
                "WHERE status = 'running'",
                (now,),
            )
            return cur.rowcount

    def candidate_policy_ref(
        self, conn: Connection, candidate_id: UUID
    ) -> dict[str, Any] | None:
        """上一轮同一网址的候选提交到了哪条政策版本（「版本变化」提示用）。"""

        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT id, status, submitted_policy_id, submitted_version "
                "FROM policy_candidate WHERE id = %s",
                (candidate_id,),
            )
            return cur.fetchone()

    def find_page(self, conn: Connection, canonical_url: str) -> dict[str, Any] | None:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT canonical_url, last_content_hash, last_candidate_id, last_seen_at "
                "FROM discovery_page WHERE canonical_url = %s",
                (canonical_url,),
            )
            return cur.fetchone()

    def upsert_page(
        self,
        conn: Connection,
        *,
        canonical_url: str,
        content_hash: str,
        candidate_id: UUID | None,
        run_id: UUID,
        now: datetime,
    ) -> None:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO discovery_page "
                "(canonical_url, last_content_hash, last_candidate_id, last_seen_at, last_run_id) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (canonical_url) DO UPDATE SET "
                "last_content_hash = EXCLUDED.last_content_hash, "
                "last_candidate_id = EXCLUDED.last_candidate_id, "
                "last_seen_at = EXCLUDED.last_seen_at, "
                "last_run_id = EXCLUDED.last_run_id",
                (canonical_url, content_hash, candidate_id, now, run_id),
            )

    def insert_candidate(self, conn: Connection, values: dict[str, Any]) -> None:
        columns = ", ".join(values)
        placeholders = ", ".join(["%s"] * len(values))
        with conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO policy_candidate ({columns}) VALUES ({placeholders})",
                tuple(values.values()),
            )

    def update_extraction(
        self,
        conn: Connection,
        *,
        candidate_id: UUID,
        revision: UUID,
        status: str,
        criteria: list[dict[str, Any]],
        findings: list[dict[str, Any]],
        extraction: dict[str, Any] | None,
        missing_fields: list[str],
        now: datetime,
    ) -> None:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE policy_candidate SET status = %s, criteria = %s, "
                "validation_findings = %s, extraction = %s, missing_fields = %s, "
                "revision = %s, updated_at = %s WHERE id = %s",
                (
                    status,
                    json.dumps(criteria, ensure_ascii=False),
                    json.dumps(findings, ensure_ascii=False),
                    json.dumps(extraction, ensure_ascii=False) if extraction else None,
                    json.dumps(missing_fields, ensure_ascii=False),
                    revision,
                    now,
                    candidate_id,
                ),
            )

    # --------------------------------------------------- HTTP 层写入（连接内）

    def lock_candidate(self, conn: Connection, candidate_id: UUID) -> dict[str, Any] | None:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"SELECT {_CANDIDATE_COLUMNS} FROM policy_candidate WHERE id = %s FOR UPDATE",
                (candidate_id,),
            )
            return cur.fetchone()

    def edit_candidate(
        self,
        conn: Connection,
        *,
        candidate_id: UUID,
        revision: UUID,
        metadata: dict[str, Any],
        criteria: list[dict[str, Any]] | None,
        findings: list[dict[str, Any]] | None,
        missing_fields: list[str],
        now: datetime,
        status: str | None = None,
    ) -> dict[str, Any]:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "UPDATE policy_candidate SET policy_id = %s, version = %s, title = %s, "
                "policy_type = %s, issuer = %s, region = %s, document_number = %s, "
                "applicable_entities = %s, published_at = %s, effective_from = %s, "
                "effective_to = %s, application_window = %s, official_entry_url = %s, "
                "criteria = COALESCE(%s, criteria), "
                "validation_findings = COALESCE(%s, validation_findings), "
                "missing_fields = %s, status = COALESCE(%s, status), "
                "revision = gen_random_uuid(), updated_at = %s "
                f"WHERE id = %s RETURNING {_CANDIDATE_COLUMNS}",
                (
                    metadata.get("policy_id"),
                    metadata.get("version"),
                    metadata.get("title"),
                    metadata.get("policy_type"),
                    metadata.get("issuer"),
                    metadata.get("region"),
                    metadata.get("document_number"),
                    None
                    if metadata.get("applicable_entities") is None
                    else json.dumps(metadata["applicable_entities"], ensure_ascii=False),
                    metadata.get("published_at"),
                    metadata.get("effective_from"),
                    metadata.get("effective_to"),
                    None
                    if metadata.get("application_window") is None
                    else json.dumps(metadata["application_window"], ensure_ascii=False),
                    metadata.get("official_entry_url"),
                    None if criteria is None else json.dumps(criteria, ensure_ascii=False),
                    None if findings is None else json.dumps(findings, ensure_ascii=False),
                    json.dumps(missing_fields, ensure_ascii=False),
                    status,
                    now,
                    candidate_id,
                ),
            )
            row = cur.fetchone()
        if row is None:  # 行在 FOR UPDATE 之后消失：并发删除，按不存在处理
            raise ApiProblem(404, "CANDIDATE_NOT_FOUND", "候选不存在。")
        return row

    def mark_submitted(
        self,
        conn: Connection,
        *,
        candidate_id: UUID,
        policy_id: UUID,
        version: str,
        now: datetime,
    ) -> dict[str, Any]:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "UPDATE policy_candidate SET status = 'submitted', submitted_policy_id = %s, "
                "submitted_version = %s, submitted_at = %s, revision = gen_random_uuid(), "
                "updated_at = %s "
                f"WHERE id = %s RETURNING {_CANDIDATE_COLUMNS}",
                (policy_id, version, now, now, candidate_id),
            )
            row = cur.fetchone()
            if row is None:
                raise ApiProblem(404, "CANDIDATE_NOT_FOUND", "候选不存在。")
            cur.execute(
                "UPDATE policy_candidate_fingerprint SET outcome = 'submitted', "
                "submitted_policy_id = %s WHERE candidate_id = %s",
                (policy_id, candidate_id),
            )
        return row

    def mark_discarded(
        self,
        conn: Connection,
        *,
        candidate_id: UUID,
        reason: str | None,
        now: datetime,
    ) -> dict[str, Any]:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "UPDATE policy_candidate SET status = 'discarded', discarded_at = %s, "
                "discard_reason = %s, revision = gen_random_uuid(), updated_at = %s "
                f"WHERE id = %s RETURNING {_CANDIDATE_COLUMNS}",
                (now, reason, now, candidate_id),
            )
            row = cur.fetchone()
            if row is None:
                raise ApiProblem(404, "CANDIDATE_NOT_FOUND", "候选不存在。")
            cur.execute(
                "UPDATE policy_candidate_fingerprint SET outcome = 'discarded' "
                "WHERE candidate_id = %s",
                (candidate_id,),
            )
        return row

    # ---------------------------------------------------------------- 游标

    def _encode_cursor(self, sort_at: datetime, row_id: UUID, fingerprint: str) -> str:
        payload = json.dumps(
            {"t": sort_at.astimezone(timezone.utc).isoformat(), "i": str(row_id), "f": fingerprint},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        signature = hmac.new(self._secret, payload, hashlib.sha256).digest()
        return _b64(payload) + "." + _b64(signature)

    def _decode_cursor(self, cursor: str, fingerprint: str) -> tuple[datetime, UUID]:
        invalid = ApiProblem(400, "INVALID_QUERY", "分页游标无效，请从第一页重新开始。")
        parts = cursor.split(".")
        if len(parts) != 2:
            raise invalid
        try:
            payload = _unb64(parts[0])
            signature = _unb64(parts[1])
        except ValueError as exc:
            raise invalid from exc
        expected = hmac.new(self._secret, payload, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise invalid
        try:
            data = json.loads(payload)
            sort_at = datetime.fromisoformat(data["t"])
            row_id = UUID(data["i"])
            stored = str(data["f"])
        except (ValueError, KeyError, TypeError) as exc:
            raise invalid from exc
        if stored != fingerprint:
            raise ApiProblem(
                400,
                "INVALID_QUERY",
                "分页游标与当前筛选条件不匹配，请从第一页重新开始。",
            )
        if sort_at.tzinfo is None:
            sort_at = sort_at.replace(tzinfo=timezone.utc)
        return sort_at, row_id


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)
