"""PostgreSQL repository for policy drafts (API-04 / API-15 / API-16).

All SQL is parameterised. ``closed`` is never stored: a published version whose
效期 or 申报窗口 has passed is derived at read time so the stored status stays
an audit record.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any, Iterable, Sequence
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ..agents.ingestion import PreparedSource
from ..db import Database
from ..errors import ApiProblem
from ..schemas import (
    ApplicationWindow,
    Criterion,
    PolicyDraft,
    PolicyDraftList,
    PolicyImportMetadata,
    SourceDocument,
    SourceSpan,
    ValidationFinding,
)

_CLOSED_CONDITION = """(
        (pv.effective_to IS NOT NULL
         AND pv.effective_to < (now() AT TIME ZONE 'Asia/Shanghai')::date)
        OR (pv.application_window IS NOT NULL
            AND NULLIF(pv.application_window ->> 'closesAt', '')::timestamptz < now())
    )"""

_STATUS_EXPR = (
    "CASE WHEN pv.review_status = 'published' AND " + _CLOSED_CONDITION + " THEN 'closed' "
    "ELSE pv.review_status END"
)

_INSERT_VERSION = """
INSERT INTO policy_version (
    id, policy_id, version, title, policy_type, issuer, region, document_number,
    applicable_entities, published_at, effective_from, effective_to,
    application_window, source_url, official_entry_url, review_status,
    revision, imported_at, updated_at
) VALUES (
    %(id)s, %(policy_id)s, %(version)s, %(title)s, %(policy_type)s, %(issuer)s,
    %(region)s, %(document_number)s, %(applicable_entities)s, %(published_at)s,
    %(effective_from)s, %(effective_to)s, %(application_window)s,
    %(source_url)s, %(official_entry_url)s, %(review_status)s,
    %(revision)s, %(imported_at)s, %(imported_at)s
)
"""

_INSERT_SOURCE = """
INSERT INTO policy_source (
    id, policy_version_id, kind, ordinal, source_url, title, file_name,
    mime_type, fetched_at, content_hash, byte_size, text, storage_key
) VALUES (
    %(id)s, %(policy_version_id)s, %(kind)s, %(ordinal)s, %(source_url)s,
    %(title)s, %(file_name)s, %(mime_type)s, %(fetched_at)s, %(content_hash)s,
    %(byte_size)s, %(text)s, %(storage_key)s
)
"""

_INSERT_TRACE = """
INSERT INTO trace_step (
    id, trace_id, run_id, sequence, stage, status, summary,
    evidence_refs, model_or_rule_version, elapsed_ms
) VALUES (
    %(id)s, %(trace_id)s, NULL, %(sequence)s, %(stage)s, %(status)s, %(summary)s,
    %(evidence_refs)s, %(model_or_rule_version)s, %(elapsed_ms)s
)
"""

_VERSION_QUERY = f"""
SELECT pv.*, {_STATUS_EXPR} AS status
FROM policy_version pv
"""


class PolicyRepository:
    def __init__(self, db: Database, cursor_secret: str) -> None:
        self._db = db
        self._secret = cursor_secret.encode("utf-8")

    # ------------------------------------------------------------------ write

    def create_policy_version(
        self,
        *,
        metadata: PolicyImportMetadata,
        sources: Sequence[PreparedSource],
        storage_keys: Sequence[str],
        trace_id: str,
        started_at: datetime | None = None,
    ) -> PolicyDraft:
        if len(storage_keys) != len(sources):
            raise ValueError("storage_keys 与 sources 数量不一致")

        imported_at = datetime.now(timezone.utc)
        started = started_at or imported_at
        version_id = uuid4()
        revision = uuid4()
        source_ids = [uuid4() for _ in sources]

        with self._db.connection() as conn, conn.transaction():
            policy_id = metadata.policy_id
            if policy_id is None:
                policy_id = uuid4()
            else:
                row = self._fetchone(
                    conn, "SELECT id FROM policy WHERE id = %s FOR UPDATE", (policy_id,)
                )
                if row is None:
                    raise ApiProblem(
                        422,
                        "POLICY_NOT_FOUND",
                        "指定的政策不存在，无法为其新增版本。",
                        {"policyId": str(policy_id)},
                    )

            self._execute(
                conn,
                "INSERT INTO policy (id, created_at) VALUES (%(id)s, %(created_at)s) "
                "ON CONFLICT (id) DO NOTHING",
                {"id": policy_id, "created_at": imported_at},
            )

            window = metadata.application_window
            version_params = {
                "id": version_id,
                "policy_id": policy_id,
                "version": metadata.version,
                "title": metadata.title,
                "policy_type": metadata.policy_type,
                "issuer": metadata.issuer,
                "region": metadata.region,
                "document_number": metadata.document_number,
                "applicable_entities": Jsonb(list(metadata.applicable_entities)),
                "published_at": metadata.published_at,
                "effective_from": metadata.effective_from,
                "effective_to": metadata.effective_to,
                "application_window": Jsonb(
                    {
                        "opensAt": window.opens_at.isoformat(),
                        "closesAt": window.closes_at.isoformat(),
                    }
                )
                if window
                else None,
                "source_url": metadata.source_url,
                "official_entry_url": metadata.official_entry_url,
                "review_status": "pending_parse",
                "revision": revision,
                "imported_at": imported_at,
            }
            try:
                with conn.cursor() as cur:
                    cur.execute(_INSERT_VERSION, version_params)
            except UniqueViolation as exc:
                raise ApiProblem(
                    409,
                    "VERSION_CONFLICT",
                    f"政策版本 {metadata.version} 已存在，请先核对后再导入。",
                    {"policyId": str(policy_id), "version": metadata.version},
                ) from exc

            for ordinal, (source, source_id, storage_key) in enumerate(
                zip(sources, source_ids, storage_keys)
            ):
                self._execute(
                    conn,
                    _INSERT_SOURCE,
                    {
                        "id": source_id,
                        "policy_version_id": version_id,
                        "kind": source.kind,
                        "ordinal": ordinal,
                        "source_url": metadata.source_url,
                        "title": source.file_name,
                        "file_name": source.file_name,
                        "mime_type": source.mime_type,
                        "fetched_at": imported_at,
                        "content_hash": source.content_hash,
                        "byte_size": source.byte_size,
                        "text": source.text,
                        "storage_key": storage_key,
                    },
                )

            self._execute(
                conn,
                _INSERT_TRACE,
                {
                    "id": uuid4(),
                    "trace_id": trace_id,
                    "sequence": 1,
                    "stage": "policy_import",
                    "status": "succeeded",
                    "summary": f"导入政策版本 {metadata.version}，来源 {len(sources)} 个",
                    "evidence_refs": Jsonb(
                        [{"type": "policyVersion", "id": str(version_id)}]
                    ),
                    "model_or_rule_version": "rule:policy-import@1",
                    "elapsed_ms": max(
                        0, int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
                    ),
                },
            )

            return self._load_draft(conn, version_id)

    # ------------------------------------------------------------------- read

    def list_admin_policies(
        self,
        *,
        status: str | None,
        policy_type: str | None,
        cursor: str | None,
        limit: int,
    ) -> PolicyDraftList:
        fingerprint = _filter_fingerprint(status, policy_type)
        after = self._decode_cursor(cursor, fingerprint) if cursor else None
        after_ts, after_id = after if after else (None, None)

        sql = f"""
        WITH page AS (
            SELECT pv.*, {_STATUS_EXPR} AS status
            FROM policy_version pv
            WHERE (%(status)s::text IS NULL OR {_STATUS_EXPR}::text = %(status)s)
              AND (%(policy_type)s::text IS NULL OR pv.policy_type::text = %(policy_type)s)
              AND (
                    %(after_ts)s::timestamptz IS NULL
                    OR (pv.imported_at, pv.id) < (%(after_ts)s::timestamptz, %(after_id)s::uuid)
                  )
        )
        SELECT * FROM page
        ORDER BY imported_at DESC, id DESC
        LIMIT %(limit)s
        """
        params = {
            "status": status,
            "policy_type": policy_type,
            "after_ts": after_ts,
            "after_id": after_id,
            "limit": limit + 1,
        }

        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            has_more = len(rows) > limit
            rows = rows[:limit]
            children = self._load_children(cur, [row["id"] for row in rows])

        items = [
            _draft_from_row(row, *children.get(row["id"], ([], [], []))) for row in rows
        ]
        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = self._encode_cursor(last["imported_at"], last["id"], fingerprint)
        return PolicyDraftList(items=items, next_cursor=next_cursor)

    def get_admin_policy(self, policy_id: UUID, version: str | None) -> PolicyDraft:
        sql = _VERSION_QUERY + " WHERE pv.policy_id = %s"
        params: list[Any] = [policy_id]
        if version is not None:
            sql += " AND pv.version = %s"
            params.append(version)
        sql += " ORDER BY pv.imported_at DESC, pv.id DESC LIMIT 1"

        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            if row is None:
                raise ApiProblem(404, "POLICY_NOT_FOUND", "没有找到对应的政策草稿。")
            children = self._load_children(cur, [row["id"]])
        return _draft_from_row(row, *children.get(row["id"], ([], [], [])))

    # ---------------------------------------------------------------- helpers

    def _load_draft(self, conn: Connection, version_id: UUID) -> PolicyDraft:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(_VERSION_QUERY + " WHERE pv.id = %s", (version_id,))
            row = cur.fetchone()
            if row is None:  # pragma: no cover - would mean the insert vanished
                raise ApiProblem(500, "INTERNAL_ERROR", "政策草稿写入后无法读回。")
            children = self._load_children(cur, [version_id])
        return _draft_from_row(row, *children.get(version_id, ([], [], [])))

    def _load_children(
        self, cur, version_ids: Sequence[UUID]
    ) -> dict[UUID, tuple[list[SourceDocument], list[Criterion], list[ValidationFinding]]]:
        sources: dict[UUID, list[SourceDocument]] = defaultdict(list)
        criteria: dict[UUID, list[Criterion]] = defaultdict(list)
        findings: dict[UUID, list[ValidationFinding]] = defaultdict(list)
        if not version_ids:
            return {}

        cur.execute(
            """
            SELECT policy_version_id, id, kind, source_url, title, fetched_at,
                   content_hash, text
            FROM policy_source
            WHERE policy_version_id = ANY(%s)
            ORDER BY policy_version_id, ordinal
            """,
            (list(version_ids),),
        )
        for row in cur.fetchall():
            sources[row["policy_version_id"]].append(
                SourceDocument(
                    id=row["id"],
                    kind=row["kind"],
                    source_url=row["source_url"],
                    title=row["title"],
                    fetched_at=row["fetched_at"],
                    content_hash=row["content_hash"],
                    text=row["text"],
                )
            )

        cur.execute(
            """
            SELECT policy_version_id, criterion_id, title, expression,
                   source_spans, review_finding, validation_status
            FROM policy_criterion
            WHERE policy_version_id = ANY(%s)
            ORDER BY policy_version_id, criterion_id
            """,
            (list(version_ids),),
        )
        for row in cur.fetchall():
            criteria[row["policy_version_id"]].append(
                Criterion(
                    id=row["criterion_id"],
                    title=row["title"],
                    expression=row["expression"],
                    source_spans=[SourceSpan.model_validate(span) for span in row["source_spans"]],
                    review_finding=row["review_finding"],
                    validation_status=row["validation_status"],
                )
            )

        cur.execute(
            """
            SELECT policy_version_id, criterion_id, code, message, source_spans
            FROM policy_validation_finding
            WHERE policy_version_id = ANY(%s)
            ORDER BY policy_version_id, ordinal
            """,
            (list(version_ids),),
        )
        for row in cur.fetchall():
            findings[row["policy_version_id"]].append(
                ValidationFinding(
                    criterion_id=row["criterion_id"],
                    code=row["code"],
                    message=row["message"],
                    source_spans=[SourceSpan.model_validate(span) for span in row["source_spans"]],
                )
            )

        return {
            version_id: (
                sources.get(version_id, []),
                criteria.get(version_id, []),
                findings.get(version_id, []),
            )
            for version_id in version_ids
        }

    @staticmethod
    def _fetchone(conn: Connection, sql: str, params: Iterable[Any]):
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            return cur.fetchone()

    @staticmethod
    def _execute(conn: Connection, sql: str, params: dict[str, Any]) -> None:
        with conn.cursor() as cur:
            cur.execute(sql, params)

    # ----------------------------------------------------------------- cursor

    def _encode_cursor(self, imported_at: datetime, version_id: UUID, fingerprint: str) -> str:
        payload = json.dumps(
            {"t": imported_at.isoformat(), "i": str(version_id), "f": fingerprint},
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
            imported_at = datetime.fromisoformat(data["t"])
            version_id = UUID(data["i"])
            stored_fingerprint = str(data["f"])
        except (ValueError, KeyError, TypeError) as exc:
            raise invalid from exc
        if stored_fingerprint != fingerprint:
            raise ApiProblem(
                400,
                "INVALID_QUERY",
                "分页游标与当前筛选条件不匹配，请从第一页重新开始。",
            )
        if imported_at.tzinfo is None:
            imported_at = imported_at.replace(tzinfo=timezone.utc)
        return imported_at, version_id


def _filter_fingerprint(status: str | None, policy_type: str | None) -> str:
    raw = f"status={status or ''};type={policy_type or ''}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _window_of(value: Any) -> ApplicationWindow | None:
    if not value:
        return None
    if "opensAt" in value or "closesAt" in value:
        return ApplicationWindow.model_validate(value)
    return ApplicationWindow(opens_at=value["opens_at"], closes_at=value["closes_at"])


def _draft_from_row(
    row: dict[str, Any],
    sources: list[SourceDocument],
    criteria: list[Criterion],
    findings: list[ValidationFinding],
) -> PolicyDraft:
    return PolicyDraft(
        id=row["policy_id"],
        version=row["version"],
        title=row["title"],
        policy_type=row["policy_type"],
        issuer=row["issuer"],
        region=row["region"],
        document_number=row["document_number"],
        applicable_entities=list(row["applicable_entities"] or []),
        published_at=_as_date(row["published_at"]),
        effective_from=_as_date(row["effective_from"]),
        effective_to=_as_date(row["effective_to"]),
        application_window=_window_of(row["application_window"]),
        source_url=row["source_url"],
        official_entry_url=row["official_entry_url"],
        status=row["status"],
        revision=row["revision"],
        sources=sources,
        criteria=criteria,
        validation_findings=findings,
    )


def _as_date(value: Any) -> date | None:
    return value if value is None or isinstance(value, date) else date.fromisoformat(str(value))
