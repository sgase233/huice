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
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping, Sequence
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ..agents.contracts import PolicySnapshot, SnapshotCriterion
from ..agents.ingestion import PreparedSource
from ..agents.profile import (
    NormalizedFact,
    NormalizedProfile,
    fact_version_of,
    to_wire_fact,
)
from ..db import Database
from ..domain import versioning
from ..domain.rules import CriterionRule
from ..domain.versioning import ChangedEvent, PolicyBoundary
from ..errors import ApiProblem
from ..schemas import (
    ApplicationWindow,
    AssessmentRun,
    Criterion,
    Enterprise,
    Period,
    PolicyAssessment,
    PolicyDetail,
    PolicyDraft,
    PolicyDraftList,
    PolicyImportMetadata,
    PolicyList,
    PolicySummary,
    PreparationTemplate,
    SourceDocument,
    SourceSpan,
    TraceResponse,
    TraceStep,
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
    evidence_refs, model_or_rule_version, elapsed_ms,
    input_summary, output_summary, policy_id, policy_version, error_code, human_action
) VALUES (
    %(id)s, %(trace_id)s, NULL, %(sequence)s, %(stage)s, %(status)s, %(summary)s,
    %(evidence_refs)s, %(model_or_rule_version)s, %(elapsed_ms)s,
    %(input_summary)s, %(output_summary)s, %(policy_id)s, %(policy_version)s,
    %(error_code)s, %(human_action)s
)
"""

_INSERT_CRITERION = """
INSERT INTO policy_criterion (
    policy_version_id, criterion_id, title, expression, source_spans,
    review_finding, validation_status
) VALUES (
    %(policy_version_id)s, %(criterion_id)s, %(title)s, %(expression)s,
    %(source_spans)s, %(review_finding)s, %(validation_status)s
)
"""

_INSERT_FINDING = """
INSERT INTO policy_validation_finding (
    policy_version_id, ordinal, criterion_id, code, message, source_spans
) VALUES (
    %(policy_version_id)s, %(ordinal)s, %(criterion_id)s, %(code)s, %(message)s,
    %(source_spans)s
)
"""

_INSERT_IDEMPOTENT = """
INSERT INTO idempotency_record (
    scope, idempotency_key, request_fingerprint, response, revision
) VALUES (
    %(scope)s, %(key)s, %(fingerprint)s, %(response)s, %(revision)s
)
ON CONFLICT (scope, idempotency_key) DO NOTHING
"""

_UPSERT_FACT = """
INSERT INTO enterprise_fact (
    enterprise_id, key, period_start, period_end, unit, source, verified,
    value_code, value_date, value_amount, value_boolean
) VALUES (
    %(enterprise_id)s, %(key)s, %(period_start)s, %(period_end)s, %(unit)s, %(source)s,
    false, %(value_code)s, %(value_date)s, %(value_amount)s, %(value_boolean)s
)
ON CONFLICT (enterprise_id, key, period_start, period_end) DO UPDATE SET
    unit = EXCLUDED.unit,
    source = EXCLUDED.source,
    value_code = EXCLUDED.value_code,
    value_date = EXCLUDED.value_date,
    value_amount = EXCLUDED.value_amount,
    value_boolean = EXCLUDED.value_boolean,
    updated_at = now()
"""

#: 写过这条事实的 run 记一条失效事件；只影响真正读过该事实的政策。
#: 事件必须连期间一起记：读取时按 key + 期间 匹配，否则同键不同期间会被一锅端。
_INSERT_FACT_EVENTS = """
INSERT INTO invalidation_event
    (id, run_id, reason, policy_id, fact_key, fact_period_start, fact_period_end, created_at)
SELECT gen_random_uuid(), d.run_id, 'fact_changed', d.policy_id, %(key)s,
       d.fact_period_start, d.fact_period_end, now()
FROM assessment_dependency d
JOIN assessment_run r ON r.id = d.run_id
WHERE r.enterprise_id = %(enterprise_id)s
  AND d.fact_key = %(key)s
  AND d.fact_period_start = %(start)s
  AND d.fact_period_end = %(end)s
"""

#: 发布新版本使引用旧版本的判断失效（与发布同事务）。
_INSERT_POLICY_EVENTS = """
INSERT INTO invalidation_event (id, run_id, reason, policy_id, created_at)
SELECT gen_random_uuid(), a.run_id, 'policy_version_changed', a.policy_id, now()
FROM assessment a
WHERE a.policy_id = %(policy_id)s AND a.policy_version_id <> %(version_id)s
"""

_VERSION_QUERY = f"""
SELECT pv.*, {_STATUS_EXPR} AS status
FROM policy_version pv
"""


@dataclass(frozen=True, slots=True)
class CriterionRecord:
    """一版条款的写入形态；``spans`` 是已序列化的契约 ``SourceSpan``。"""

    id: UUID
    title: str
    expression: dict[str, Any]
    spans: list[dict[str, Any]]
    review_finding: str
    validation_status: str

    def to_params(self, policy_version_id: UUID) -> dict[str, Any]:
        return {
            "policy_version_id": policy_version_id,
            "criterion_id": self.id,
            "title": self.title,
            "expression": Jsonb(self.expression),
            "source_spans": Jsonb(self.spans),
            "review_finding": self.review_finding,
            "validation_status": self.validation_status,
        }


@dataclass(frozen=True, slots=True)
class FindingRecord:
    criterion_id: UUID | None
    code: str
    message: str
    spans: list[dict[str, Any]]

    def to_params(self, policy_version_id: UUID, ordinal: int) -> dict[str, Any]:
        return {
            "policy_version_id": policy_version_id,
            "ordinal": ordinal,
            "criterion_id": self.criterion_id,
            "code": self.code,
            "message": self.message,
            "source_spans": Jsonb(self.spans),
        }


def _elapsed_ms(started: datetime) -> int:
    return max(0, int((datetime.now(timezone.utc) - started).total_seconds() * 1000))


def _preparation_template(draft: PolicyDraft) -> PreparationTemplate:
    """准备清单模板：类型与办理方式由政策类型决定，办理说明留待审核材料补充。"""

    return PreparationTemplate(
        policy_type=draft.policy_type,
        handling_mode="tax_self_assessment" if draft.policy_type == "tax" else "grant_application",
        official_entry_url=draft.official_entry_url,
        deadline=draft.application_window.closes_at if draft.application_window else None,
        items=[],
    )


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
                    "stage": "ingestion",
                    "status": "ok",
                    "summary": f"导入政策版本 {metadata.version}，来源 {len(sources)} 个",
                    "evidence_refs": Jsonb(
                        [{"type": "policyVersion", "id": str(version_id)}]
                    ),
                    "model_or_rule_version": "rule:policy-import@1",
                    "elapsed_ms": max(
                        0, int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
                    ),
                    "input_summary": f"导入政策版本 {metadata.version}（{metadata.title}）",
                    "output_summary": f"保存 {len(sources)} 个来源，状态 pending_parse",
                    "policy_id": policy_id,
                    "policy_version": metadata.version,
                    "error_code": None,
                    "human_action": None,
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

    # ------------------------------------------------------- 公开政策（API-02/03）

    def list_public_policies(
        self,
        *,
        status: str | None,
        policy_type: str | None,
        region: str | None,
        as_of: date | None,
        cursor: str | None,
        limit: int,
    ) -> PolicyList:
        """只列已审核发布的政策，每项政策给最新发布版本。

        ``asOf`` 只是元数据过滤（当天是否处于适用期），不用于挑历史版本；关闭状态
        由 :data:`_STATUS_EXPR` 按当前时刻派生，不落库。游标带上全部筛选条件，
        条件一变游标即失效，避免把上一组筛选的页边界套到新结果上。
        """

        fingerprint = _public_filter_fingerprint(status, policy_type, region, as_of)
        after = self._decode_cursor(cursor, fingerprint) if cursor else None
        after_ts, after_id = after if after else (None, None)

        sql = f"""
        WITH latest AS (
            SELECT DISTINCT ON (pv.policy_id)
                   pv.policy_id, pv.id, pv.version, pv.title, pv.policy_type, pv.issuer,
                   pv.region, pv.effective_from, pv.effective_to, pv.application_window,
                   pv.source_url, pv.imported_at, {_STATUS_EXPR} AS status
            FROM policy_version pv
            WHERE pv.review_status = 'published'
              AND (%(status)s::text IS NULL OR {_STATUS_EXPR}::text = %(status)s)
              AND (%(policy_type)s::text IS NULL OR pv.policy_type::text = %(policy_type)s)
              AND (%(region)s::text IS NULL OR pv.region = %(region)s)
              AND (
                    %(as_of)s::date IS NULL
                    OR (pv.effective_from <= %(as_of)s::date
                        AND (pv.effective_to IS NULL OR pv.effective_to >= %(as_of)s::date))
                  )
            ORDER BY pv.policy_id, pv.reviewed_at DESC NULLS LAST, pv.id DESC
        )
        SELECT * FROM latest
        WHERE (
                %(after_ts)s::timestamptz IS NULL
                OR (imported_at, id) < (%(after_ts)s::timestamptz, %(after_id)s::uuid)
              )
        ORDER BY imported_at DESC, id DESC
        LIMIT %(limit)s
        """
        params = {
            "status": status,
            "policy_type": policy_type,
            "region": region,
            "as_of": as_of,
            "after_ts": after_ts,
            "after_id": after_id,
            "limit": limit + 1,
        }

        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

        has_more = len(rows) > limit
        rows = rows[:limit]
        items = [
            PolicySummary(
                id=row["policy_id"],
                title=row["title"],
                policy_type=row["policy_type"],
                issuer=row["issuer"],
                region=row["region"],
                version=row["version"],
                status=row["status"],
                effective_from=_as_date(row["effective_from"]),
                effective_to=_as_date(row["effective_to"]),
                application_window=_window_of(row.get("application_window")),
                source_url=row["source_url"],
            )
            for row in rows
        ]
        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = self._encode_cursor(last["imported_at"], last["id"], fingerprint)
        return PolicyList(items=items, next_cursor=next_cursor)

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

    # -------------------------------------------------------- 条款与发布

    def get_version_for_edit(self, policy_id: UUID, version: str) -> PolicyDraft:
        """读取指定版本用于写入；版本不存在时 404。"""
        return self.get_admin_policy(policy_id, version)

    def save_criteria(
        self,
        *,
        policy_id: UUID,
        version: str,
        expected_revision: UUID | None,
        criteria: Sequence["CriterionRecord"],
        findings: Sequence["FindingRecord"],
        status: str,
        trace_id: str,
        started_at: datetime | None = None,
    ) -> PolicyDraft:
        """整体替换某版本的条款与校验发现（compare-and-swap）。

        ``expected_revision`` 来自请求的 If-Match；与库中当前 revision 不一致说明有人
        先改过，返回 409 并带 ``currentVersion``，绝不覆盖并发的人工修改。
        """

        started = started_at or datetime.now(timezone.utc)
        new_revision = uuid4()

        with self._db.connection() as conn, conn.transaction():
            row = self._lock_version(conn, policy_id, version)
            self._assert_editable(row, expected_revision)

            self._execute(
                conn,
                "DELETE FROM policy_validation_finding WHERE policy_version_id = %(id)s",
                {"id": row["id"]},
            )
            self._execute(
                conn,
                "DELETE FROM policy_criterion WHERE policy_version_id = %(id)s",
                {"id": row["id"]},
            )
            for criterion in criteria:
                self._execute(conn, _INSERT_CRITERION, criterion.to_params(row["id"]))
            for ordinal, finding in enumerate(findings):
                self._execute(
                    conn,
                    _INSERT_FINDING,
                    finding.to_params(row["id"], ordinal),
                )

            self._execute(
                conn,
                """
                UPDATE policy_version
                SET review_status = %(status)s, revision = %(revision)s, updated_at = now()
                WHERE id = %(id)s
                """,
                {"status": status, "revision": new_revision, "id": row["id"]},
            )
            self._insert_trace(
                conn,
                trace_id=trace_id,
                stage="citation_validation",
                input_summary=f"保存 {len(criteria)} 条条款",
                output_summary=f"引用校验发现 {len(findings)} 条，状态 {status}",
                policy_version_id=row["id"],
                policy_id=row["policy_id"],
                policy_version=row["version"],
                model_or_rule_version="rule:citations@1",
                elapsed_ms=_elapsed_ms(started),
            )
            return self._load_draft(conn, row["id"])

    def publish(
        self,
        *,
        policy_id: UUID,
        version: str,
        expected_revision: UUID | None,
        trace_id: str,
        started_at: datetime | None = None,
    ) -> PolicyDraft:
        """人工审核通过后发布：同一事务里校验并切换状态，失败保留草稿。"""

        started = started_at or datetime.now(timezone.utc)
        new_revision = uuid4()

        with self._db.connection() as conn, conn.transaction():
            row = self._lock_version(conn, policy_id, version)
            if row["review_status"] == "published":
                raise ApiProblem(
                    409,
                    "POLICY_ALREADY_PUBLISHED",
                    "该版本已发布，不能重复发布；如需修改请导入新版本。",
                    {"currentVersion": str(row["revision"])},
                )
            self._assert_editable(row, expected_revision)

            criteria = self._fetchall(
                conn,
                "SELECT criterion_id, validation_status FROM policy_criterion "
                "WHERE policy_version_id = %s ORDER BY criterion_id",
                (row["id"],),
            )
            if not criteria:
                raise ApiProblem(
                    422,
                    "CRITERIA_REQUIRED",
                    "该版本还没有条款，不能发布。",
                )
            blocked = [str(item["criterion_id"]) for item in criteria if item["validation_status"] != "passed"]
            if blocked:
                raise ApiProblem(
                    422,
                    "CRITERIA_NOT_VALIDATED",
                    "存在引用校验未通过的条件，不能发布。",
                    {"criterionIds": blocked},
                )
            source_count = self._fetchone(
                conn, "SELECT count(*) AS n FROM policy_source WHERE policy_version_id = %s", (row["id"],)
            )
            if not source_count or int(source_count["n"]) == 0:
                raise ApiProblem(
                    422,
                    "SOURCE_REQUIRED",
                    "该版本没有已保存的原文，不能发布。",
                )

            self._execute(
                conn,
                """
                UPDATE policy_version
                SET review_status = 'published', reviewed_at = now(),
                    revision = %(revision)s, updated_at = now()
                WHERE id = %(id)s
                """,
                {"revision": new_revision, "id": row["id"]},
            )
            # 发布与失效事件同一事务提交：引用旧版本的判断立刻可读到 policy_changed。
            self._execute(
                conn,
                _INSERT_POLICY_EVENTS,
                {"policy_id": row["policy_id"], "version_id": row["id"]},
            )
            self._insert_trace(
                conn,
                trace_id=trace_id,
                stage="human_review",
                input_summary=f"人工确认发布政策版本 {version}",
                output_summary=f"已发布，条件 {len(criteria)} 条，判为当前版本",
                policy_version_id=row["id"],
                policy_id=row["policy_id"],
                policy_version=row["version"],
                model_or_rule_version="rule:publish@1",
                elapsed_ms=_elapsed_ms(started),
                human_action="approve",
            )
            return self._load_draft(conn, row["id"])

    # ------------------------------------------------------------ idempotency

    def read_idempotent(self, scope: str, key: UUID) -> dict[str, Any] | None:
        """取回同一作用域下同键的已完成结果；没有则返回 ``None``。"""

        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT request_fingerprint, response, revision "
                "FROM idempotency_record WHERE scope = %s AND idempotency_key = %s",
                (scope, key),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return {
            "fingerprint": row["request_fingerprint"],
            "response": row["response"],
            "revision": row["revision"],
        }

    def save_idempotent(
        self,
        scope: str,
        key: UUID,
        fingerprint: str,
        response: dict[str, Any],
        revision: UUID,
    ) -> None:
        """记录一次抽取结果。表只追加，重复键保留首次写入的那条。"""

        with self._db.connection() as conn, conn.transaction():
            self._execute(
                conn,
                _INSERT_IDEMPOTENT,
                {
                    "scope": scope,
                    "key": key,
                    "fingerprint": fingerprint,
                    "response": Jsonb(response),
                    "revision": revision,
                },
            )

    # ------------------------------------------------------------- 企业画像

    def create_enterprise(self, *, profile: NormalizedProfile, trace_id: str) -> Enterprise:
        enterprise_id = uuid4()
        with self._db.connection() as conn, conn.transaction():
            self._execute(
                conn,
                "INSERT INTO enterprise (id, name_synthetic, period_start, period_end) "
                "VALUES (%(id)s, %(name)s, %(start)s, %(end)s)",
                {
                    "id": enterprise_id,
                    "name": profile.name_synthetic,
                    "start": profile.period_start,
                    "end": profile.period_end,
                },
            )
            for fact in profile.facts:
                self._execute(conn, _UPSERT_FACT, self._fact_params(enterprise_id, fact))
            self._insert_trace(
                conn,
                trace_id=trace_id,
                stage="profile",
                input_summary="创建企业画像",
                output_summary=f"事实 {len(profile.facts)} 条，版本 {profile.fact_version[:12]}",
                policy_version_id=None,
                model_or_rule_version="rule:profile@1",
                elapsed_ms=0,
            )
            facts = self._load_facts(conn, enterprise_id)
        return _enterprise_of(enterprise_id, profile.name_synthetic, profile.period_start, profile.period_end, facts)

    def get_enterprise(self, enterprise_id: UUID) -> Enterprise:
        with self._db.connection() as conn:
            row = self._fetchone(
                conn,
                "SELECT id, name_synthetic, period_start, period_end FROM enterprise WHERE id = %s",
                (enterprise_id,),
            )
            if row is None:
                raise ApiProblem(404, "ENTERPRISE_NOT_FOUND", "没有找到对应的企业画像。")
            facts = self._load_facts(conn, enterprise_id)
        return _enterprise_of(
            row["id"], row["name_synthetic"], row["period_start"], row["period_end"], facts
        )

    def update_fact(
        self,
        *,
        enterprise_id: UUID,
        fact: NormalizedFact,
        expected_fact_version: str,
        trace_id: str,
    ) -> Enterprise:
        """按 key+期间 upsert 一条事实；``factVersion`` 做 compare-and-swap。"""

        with self._db.connection() as conn, conn.transaction():
            row = self._fetchone(
                conn,
                "SELECT id, name_synthetic, period_start, period_end FROM enterprise "
                "WHERE id = %s FOR UPDATE",
                (enterprise_id,),
            )
            if row is None:
                raise ApiProblem(404, "ENTERPRISE_NOT_FOUND", "没有找到对应的企业画像。")

            current = fact_version_of(self._load_facts(conn, enterprise_id))
            if expected_fact_version != current:
                raise ApiProblem(
                    409,
                    "VERSION_CONFLICT",
                    "画像已被他人更新，请重新读取后再提交。",
                    {"currentVersion": current},
                )

            self._execute(conn, _UPSERT_FACT, self._fact_params(enterprise_id, fact))
            # 事实变更与失效事件在同一事务里提交：读到过这条事实的 run 立刻标 stale。
            self._execute(
                conn,
                _INSERT_FACT_EVENTS,
                {
                    "enterprise_id": enterprise_id,
                    "key": fact.key,
                    "start": fact.period_start,
                    "end": fact.period_end,
                },
            )
            self._insert_trace(
                conn,
                trace_id=trace_id,
                stage="profile",
                input_summary=f"更新事实 {fact.key}（{fact.period_start}~{fact.period_end}）",
                output_summary="画像版本已更新" if fact.known else "值已清空，按未知处理",
                policy_version_id=None,
                model_or_rule_version="rule:profile@1",
                elapsed_ms=0,
            )
            facts = self._load_facts(conn, enterprise_id)
        return _enterprise_of(
            row["id"], row["name_synthetic"], row["period_start"], row["period_end"], facts
        )

    @staticmethod
    def _fact_params(enterprise_id: UUID, fact: NormalizedFact) -> dict[str, Any]:
        params = {
            "enterprise_id": enterprise_id,
            "key": fact.key,
            "period_start": fact.period_start,
            "period_end": fact.period_end,
            "unit": fact.unit,
            "source": fact.source,
            "value_code": None,
            "value_date": None,
            "value_amount": None,
            "value_boolean": None,
        }
        if fact.value is None:
            return params
        if fact.unit == "code":
            params["value_code"] = fact.value
        elif fact.unit == "date":
            params["value_date"] = fact.value
        elif fact.unit == "boolean":
            params["value_boolean"] = fact.value
        else:
            params["value_amount"] = fact.value
        return params

    def _load_facts(self, conn: Connection, enterprise_id: UUID) -> list[NormalizedFact]:
        rows = self._fetchall(
            conn,
            "SELECT key, unit, source, period_start, period_end, "
            "value_code, value_date, value_amount, value_boolean "
            "FROM enterprise_fact WHERE enterprise_id = %s "
            "ORDER BY key, period_start, period_end",
            (enterprise_id,),
        )
        return [_normalized_fact(row) for row in rows]

    # ------------------------------------------------------- 已发布政策快照

    def published_policy_snapshots(self, policy_ids: Sequence[UUID]) -> list[PolicySnapshot]:
        """取每项政策最新的**已发布**版本；任一项未发布即整体 422。"""

        if not policy_ids:
            raise ApiProblem(422, "VALIDATION_ERROR", "至少需要一项政策。")
        sql = _VERSION_QUERY + (
            " WHERE pv.policy_id = ANY(%s) AND pv.review_status = 'published'"
            " ORDER BY pv.policy_id, pv.reviewed_at DESC NULLS LAST, pv.id DESC"
        )
        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, (list(policy_ids),))
            rows = cur.fetchall()
            latest: dict[UUID, Mapping[str, Any]] = {}
            for row in rows:
                latest.setdefault(row["policy_id"], row)
            missing = [str(item) for item in policy_ids if item not in latest]
            if missing:
                raise ApiProblem(
                    422,
                    "POLICY_NOT_PUBLISHED",
                    "存在尚未审核发布的政策，不能参与判断；请先完成后台审核发布。",
                    {"policyIds": missing},
                )
            children = self._load_children(cur, [row["id"] for row in latest.values()])

        snapshots: list[PolicySnapshot] = []
        for policy_id in policy_ids:
            row = latest[policy_id]
            _, criteria, _ = children.get(row["id"], ([], [], []))
            if not criteria:
                raise ApiProblem(
                    422,
                    "POLICY_NOT_PUBLISHED",
                    f"政策版本 {row['version']} 没有已审核条款，不能参与判断。",
                    {"policyIds": [str(policy_id)]},
                )
            blocked = [item.id for item in criteria if item.validation_status != "passed"]
            if blocked:
                raise ApiProblem(
                    422,
                    "CRITERIA_NOT_VALIDATED",
                    "存在引用校验未通过的条件，不能参与判断。",
                    {"criterionIds": [str(item) for item in blocked]},
                )
            window = _window_of(row.get("application_window"))
            snapshots.append(
                PolicySnapshot(
                    id=row["policy_id"],
                    version_id=row["id"],
                    version=row["version"],
                    title=row["title"],
                    policy_type=row["policy_type"],
                    official_entry_url=row["official_entry_url"],
                    effective_to=_as_date(row.get("effective_to")),
                    window_closes_at=window.closes_at if window else None,
                    criteria=tuple(
                        SnapshotCriterion(
                            rule=CriterionRule(
                                id=item.id,
                                title=item.title,
                                expression=item.expression,
                                review_finding=item.review_finding,
                            ),
                            spans=tuple(item.source_spans),
                        )
                        for item in criteria
                    ),
                )
            )
        return snapshots

    # --------------------------------------------------------------- 判断运行

    def get_run_header(self, run_id: UUID) -> dict[str, Any] | None:
        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT id, enterprise_id, period_start, period_end FROM assessment_run WHERE id = %s",
                (run_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            cur.execute(
                "SELECT policy_id FROM assessment WHERE run_id = %s ORDER BY policy_id", (run_id,)
            )
            policies = [item["policy_id"] for item in cur.fetchall()]
        return {
            "enterprise_id": row["enterprise_id"],
            "period_start": row["period_start"],
            "period_end": row["period_end"],
            "policy_ids": policies,
        }

    def save_run(
        self,
        *,
        run: AssessmentRun,
        dependencies: Mapping[UUID, Sequence[tuple[str, Any, Any]]],
        trace_steps: Sequence[Any],
        trace_id: str,
        policy_version_ids: Mapping[UUID, UUID],
    ) -> None:
        """一次事务写入 run、每项政策的结果快照、依赖与在线轨迹。"""

        with self._db.connection() as conn, conn.transaction():
            self._execute(
                conn,
                """
                INSERT INTO assessment_run (
                    id, enterprise_id, as_of, status, fact_version, model_or_rule_version,
                    created_at, finished_at, period_start, period_end, supersedes_run_id
                ) VALUES (
                    %(id)s, %(enterprise_id)s, %(as_of)s, 'succeeded', %(fact_version)s,
                    %(rule_version)s, %(created_at)s, %(created_at)s, %(period_start)s,
                    %(period_end)s, %(supersedes_run_id)s
                )
                """,
                {
                    "id": run.id,
                    "enterprise_id": run.enterprise_id,
                    "as_of": run.as_of,
                    "fact_version": run.fact_version,
                    "rule_version": run.rule_version,
                    "created_at": run.created_at,
                    "period_start": run.period.start_date,
                    "period_end": run.period.end_date,
                    "supersedes_run_id": run.supersedes_run_id,
                },
            )
            for assessment in run.assessments:
                version_id = policy_version_ids.get(assessment.policy_id)
                if version_id is None:  # pragma: no cover - 快照与结果一一对应
                    raise ApiProblem(500, "INTERNAL_ERROR", "判断结果缺少对应的政策版本。")
                self._execute(
                    conn,
                    """
                    INSERT INTO assessment (
                        run_id, policy_id, policy_version_id, result, citation, snapshot, created_at
                    ) VALUES (
                        %(run_id)s, %(policy_id)s, %(policy_version_id)s, %(result)s,
                        %(citation)s, %(snapshot)s, %(created_at)s
                    )
                    """,
                    {
                        "run_id": run.id,
                        "policy_id": assessment.policy_id,
                        "policy_version_id": version_id,
                        "result": assessment.result,
                        "citation": Jsonb(
                            [
                                evidence.model_dump(by_alias=True, mode="json")
                                for criterion in assessment.criteria_results
                                for evidence in criterion.evidence_refs
                            ]
                        ),
                        "snapshot": Jsonb(
                            assessment.model_dump(
                                by_alias=True, mode="json", exclude={"policy_id", "result"}
                            )
                        ),
                        "created_at": run.created_at,
                    },
                )
                for key, start, end in dependencies.get(assessment.policy_id, ()):
                    self._execute(
                        conn,
                        "INSERT INTO assessment_dependency "
                        "(run_id, policy_id, fact_key, fact_period_start, fact_period_end) "
                        "VALUES (%(run_id)s, %(policy_id)s, %(key)s, %(start)s, %(end)s) "
                        "ON CONFLICT DO NOTHING",
                        {
                            "run_id": run.id,
                            "policy_id": assessment.policy_id,
                            "key": key,
                            "start": start,
                            "end": end,
                        },
                    )

            for step in trace_steps:
                self._execute(
                    conn,
                    """
                    INSERT INTO trace_step (
                        id, trace_id, run_id, sequence, stage, status, summary,
                        evidence_refs, model_or_rule_version, elapsed_ms,
                        input_summary, output_summary, policy_id, policy_version,
                        error_code, human_action
                    ) VALUES (
                        %(id)s, %(trace_id)s, %(run_id)s, %(sequence)s, %(stage)s, %(status)s,
                        %(summary)s, %(evidence_refs)s, %(model_or_rule_version)s, %(elapsed_ms)s,
                        %(input_summary)s, %(output_summary)s, %(policy_id)s, %(policy_version)s,
                        %(error_code)s, %(human_action)s
                    )
                    """,
                    {
                        "id": uuid4(),
                        "trace_id": trace_id,
                        "run_id": run.id,
                        "sequence": step.sequence,
                        "stage": step.stage,
                        "status": step.status,
                        "summary": f"{step.input_summary} → {step.output_summary}",
                        "evidence_refs": Jsonb(list(step.evidence_refs)),
                        "model_or_rule_version": step.model_or_rule_version,
                        "elapsed_ms": step.elapsed_ms,
                        "input_summary": step.input_summary,
                        "output_summary": step.output_summary,
                        "policy_id": step.policy_id,
                        "policy_version": step.policy_version,
                        "error_code": step.error_code,
                        "human_action": step.human_action,
                    },
                )

    def get_run(self, run_id: UUID) -> AssessmentRun:
        """读取 run 并**重算**有效性视图；四态与解释等快照内容原样返回。"""

        now = datetime.now(timezone.utc)
        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM assessment_run WHERE id = %s", (run_id,))
            header = cur.fetchone()
            if header is None:
                raise ApiProblem(404, "RUN_NOT_FOUND", "没有找到对应的判断运行。")
            cur.execute(
                """
                SELECT a.policy_id, a.result, a.snapshot, pv.version,
                       pv.effective_to, pv.application_window
                FROM assessment a JOIN policy_version pv ON pv.id = a.policy_version_id
                WHERE a.run_id = %s ORDER BY a.policy_id
                """,
                (run_id,),
            )
            rows = cur.fetchall()
            cur.execute(
                "SELECT reason, policy_id, fact_key, fact_period_start, fact_period_end, "
                "created_at FROM invalidation_event WHERE run_id = %s ORDER BY created_at",
                (run_id,),
            )
            event_rows = cur.fetchall()

        events = [
            ChangedEvent(
                reason=row["reason"],
                occurred_at=row["created_at"],
                policy_id=row["policy_id"],
                fact_key=row["fact_key"],
                fact_period=_period_of(row["fact_period_start"], row["fact_period_end"]),
            )
            for row in event_rows
        ]

        assessments: list[PolicyAssessment] = []
        version_map: dict[str, str] = {}
        for row in rows:
            payload = dict(row["snapshot"] or {})
            window = _window_of(row.get("application_window"))
            boundary = PolicyBoundary(
                effective_to=_as_date(row.get("effective_to")),
                window_closes_at=window.closes_at if window else None,
            )
            deadline = versioning.valid_until(boundary=boundary, now=now)
            view = versioning.resolve_validity(
                boundary=boundary,
                events=[event for event in events if event.policy_id in (None, row["policy_id"])],
                criterion_dependencies=_criterion_dependencies(payload),
                run_created_at=header["created_at"],
                now=now,
                valid_until_at=deadline,
            )
            assessments.append(
                PolicyAssessment.model_validate(
                    {
                        **payload,
                        "policyId": str(row["policy_id"]),
                        "result": row["result"],
                        "validity": view.validity,
                        "staleReasons": view.stale_reasons,
                        "validUntil": deadline.isoformat() if deadline else None,
                    }
                )
            )
            version_map[str(row["policy_id"])] = row["version"]

        if not assessments:  # pragma: no cover - run 至少有一项政策
            raise ApiProblem(404, "RUN_NOT_FOUND", "该判断运行没有结果。")

        return AssessmentRun(
            id=header["id"],
            enterprise_id=header["enterprise_id"],
            period=Period(start_date=header["period_start"], end_date=header["period_end"]),
            as_of=header["as_of"],
            created_at=header["created_at"],
            policy_version_map=version_map,
            fact_version=header["fact_version"],
            rule_version=header["model_or_rule_version"],
            supersedes_run_id=header["supersedes_run_id"],
            assessments=assessments,
        )

    # ---------------------------------------------------------------- helpers

    def get_trace(self, run_id: UUID) -> TraceResponse:
        """API-14：先列入库/审核轨迹，再列本次在线轨迹，``sequence`` 按返回顺序重排。

        入库轨迹与 run 的关联靠 ``evidenceRefs`` 里的 ``policyVersion`` 引用：run 用到的
        每个政策版本，其 ingestion/extraction/citation_validation/human_review 步骤都
        带同一个版本 ID。``sequence`` 在原表里按 trace_id 各自递增，两组拼接后重排，
        避免出现重复序号（原始序号仍在库里）。
        """

        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT id FROM assessment_run WHERE id = %s", (run_id,))
            if cur.fetchone() is None:
                raise ApiProblem(404, "RUN_NOT_FOUND", "没有找到对应的判断运行。")
            cur.execute(
                "SELECT a.policy_id, a.policy_version_id, pv.version "
                "FROM assessment a JOIN policy_version pv ON pv.id = a.policy_version_id "
                "WHERE a.run_id = %s ORDER BY a.policy_id",
                (run_id,),
            )
            links = cur.fetchall()
            cur.execute(
                "SELECT * FROM trace_step WHERE run_id = %s ORDER BY sequence, created_at",
                (run_id,),
            )
            online_rows = cur.fetchall()

            upstream_rows: list[dict[str, Any]] = []
            if links:
                cur.execute(
                    "SELECT * FROM trace_step "
                    "WHERE run_id IS NULL AND evidence_refs @> ANY(%s::jsonb[]) "
                    "ORDER BY created_at, sequence",
                    ([Jsonb([{"type": "policyVersion", "id": str(row["policy_version_id"])}]) for row in links],),
                )
                upstream_rows = cur.fetchall()

        by_version = {str(row["policy_version_id"]): row for row in links}
        ordered = [*upstream_rows, *online_rows]
        steps: list[TraceStep] = []
        for index, row in enumerate(ordered, start=1):
            policy_id = row["policy_id"]
            policy_version = row["policy_version"]
            for ref in _list_of(row["evidence_refs"]):
                version_id = by_version.get(str(ref.get("id") or ""))
                if ref.get("type") == "policyVersion" and version_id is not None:
                    policy_id = policy_id or version_id["policy_id"]
                    policy_version = policy_version or version_id["version"]
                    break
            steps.append(
                TraceStep(
                    sequence=index,
                    stage=row["stage"],
                    status=row["status"],
                    input_summary=row["input_summary"] or row["summary"],
                    output_summary=row["output_summary"] or "",
                    evidence_refs=_list_of(row["evidence_refs"]),
                    policy_id=policy_id,
                    policy_version=policy_version,
                    model_or_rule_version=row["model_or_rule_version"],
                    elapsed_ms=row["elapsed_ms"],
                    error_code=row["error_code"],
                    human_action=row["human_action"],
                )
            )
        return TraceResponse(steps=steps)

    def get_policy_detail(self, policy_id: UUID, version: str | None = None) -> PolicyDetail:
        """读取已发布（含派生的 closed）政策详情；未发布或不存在一律 404。"""
        sql = _VERSION_QUERY + " WHERE pv.policy_id = %s AND pv.review_status = 'published'"
        params: list[Any] = [policy_id]
        if version is not None:
            sql += " AND pv.version = %s"
            params.append(version)
        sql += " ORDER BY pv.reviewed_at DESC NULLS LAST, pv.id DESC LIMIT 1"

        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            if row is None:
                raise ApiProblem(404, "POLICY_NOT_FOUND", "没有找到对应的已发布政策。")
            children = self._load_children(cur, [row["id"]])

        sources, criteria, _ = children.get(row["id"], ([], [], []))
        if not sources or not criteria:
            raise ApiProblem(404, "POLICY_NOT_FOUND", "该政策版本的公开预览尚未就绪。")
        draft = _draft_from_row(row, sources, criteria, [])
        return PolicyDetail(
            id=draft.id,
            title=draft.title,
            policy_type=draft.policy_type,
            issuer=draft.issuer,
            region=draft.region,
            version=draft.version,
            status=draft.status,
            effective_from=draft.effective_from,
            effective_to=draft.effective_to,
            application_window=draft.application_window,
            source_url=draft.source_url,
            document_number=draft.document_number,
            applicable_entities=draft.applicable_entities,
            published_at=draft.published_at,
            official_entry_url=draft.official_entry_url,
            sources=sources,
            criteria=criteria,
            preparation_template=_preparation_template(draft),
        )

    def _lock_version(self, conn: Connection, policy_id: UUID, version: str) -> dict[str, Any]:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT * FROM policy_version WHERE policy_id = %s AND version = %s FOR UPDATE",
                (policy_id, version),
            )
            row = cur.fetchone()
        if row is None:
            raise ApiProblem(404, "POLICY_NOT_FOUND", "没有找到对应的政策版本。")
        return row

    @staticmethod
    def _assert_editable(row: Mapping[str, Any], expected_revision: UUID | None) -> None:
        if row["review_status"] == "published":
            raise ApiProblem(
                409,
                "POLICY_ALREADY_PUBLISHED",
                "已发布的版本不能再修改；如需修订请导入新版本。",
                {"currentVersion": str(row["revision"])},
            )
        if expected_revision is not None and expected_revision != row["revision"]:
            raise ApiProblem(
                409,
                "VERSION_CONFLICT",
                "数据已被他人更新，请重新读取后再提交。",
                {"currentVersion": str(row["revision"])},
            )

    def _insert_trace(
        self,
        conn: Connection,
        *,
        trace_id: str,
        stage: str,
        policy_version_id: UUID | None,
        model_or_rule_version: str,
        elapsed_ms: int,
        input_summary: str,
        output_summary: str,
        policy_id: UUID | None = None,
        policy_version: str | None = None,
        status: str = "ok",
        error_code: str | None = None,
        human_action: str | None = None,
    ) -> None:
        """写入一条脱敏轨迹；摘要分输入/输出两列存，供 API-14 原样回读。"""

        next_sequence = self._fetchone(
            conn,
            "SELECT COALESCE(max(sequence), 0) + 1 AS n FROM trace_step WHERE trace_id = %s",
            (trace_id,),
        )
        refs = (
            [{"type": "policyVersion", "id": str(policy_version_id)}]
            if policy_version_id is not None
            else []
        )
        self._execute(
            conn,
            _INSERT_TRACE,
            {
                "id": uuid4(),
                "trace_id": trace_id,
                "sequence": int(next_sequence["n"]) if next_sequence else 1,
                "stage": stage,
                "status": status,
                "summary": f"{input_summary} → {output_summary}",
                "evidence_refs": Jsonb(refs),
                "model_or_rule_version": model_or_rule_version,
                "elapsed_ms": max(0, elapsed_ms),
                "input_summary": input_summary,
                "output_summary": output_summary,
                "policy_id": policy_id,
                "policy_version": policy_version,
                "error_code": error_code,
                "human_action": human_action,
            },
        )

    @staticmethod
    def _fetchall(conn: Connection, sql: str, params: Iterable[Any]):
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, tuple(params))
            return cur.fetchall()


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
        with conn.cursor(row_factory=dict_row) as cur:
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


def _public_filter_fingerprint(
    status: str | None,
    policy_type: str | None,
    region: str | None,
    as_of: date | None,
) -> str:
    """公开列表的游标指纹：公开接口多了 region/asOf 两个筛选条件，必须一并绑定。"""

    raw = "public;{};{};{};{}".format(
        status or "", policy_type or "", region or "", as_of.isoformat() if as_of else ""
    ).encode("utf-8")
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


def _period_of(start: Any, end: Any) -> tuple[date, date] | None:
    """失效事件的期间；两列都为空的旧行返回 ``None``（按整键保守匹配）。"""

    first, second = _as_date(start), _as_date(end)
    return (first, second) if first is not None and second is not None else None


def _list_of(value: Any) -> list[dict[str, Any]]:
    """jsonb 数组列可能是 list、None 或字符串；统一成可迭代的 dict 列表。"""

    if value is None:
        return []
    if isinstance(value, str):
        value = json.loads(value)
    return [item for item in value if isinstance(item, dict)]


def _normalized_fact(row: Mapping[str, Any]) -> NormalizedFact:
    """把一行事实还原成可求值的形态：金额/人数是 Decimal，日期是 date。"""

    unit = row["unit"]
    if unit == "code":
        value: Any = row["value_code"]
    elif unit == "date":
        value = _as_date(row["value_date"])
    elif unit == "boolean":
        value = row["value_boolean"]
    else:
        value = row["value_amount"]
    return NormalizedFact(
        key=row["key"],
        unit=unit,
        period_start=row["period_start"],
        period_end=row["period_end"],
        source=row["source"],
        value=value,
    )


def _enterprise_of(
    enterprise_id: UUID,
    name_synthetic: str,
    period_start: date,
    period_end: date,
    facts: Sequence[NormalizedFact],
) -> Enterprise:
    return Enterprise(
        id=enterprise_id,
        name_synthetic=name_synthetic,
        period=Period(start_date=period_start, end_date=period_end),
        facts=[to_wire_fact(fact) for fact in facts],
        fact_version=fact_version_of(facts),
    )


def _criterion_dependencies(payload: Mapping[str, Any]) -> dict[UUID, list[tuple[str, Any, Any]]]:
    """从 run 快照里还原条件级依赖，供读取时判断「哪条条件被哪次事实变更影响」。"""

    dependencies: dict[UUID, list[tuple[str, Any, Any]]] = {}
    for criterion in payload.get("criteriaResults") or []:
        criterion_id = criterion.get("criterionId")
        if criterion_id is None:
            continue
        refs: list[tuple[str, Any, Any]] = []
        for ref in criterion.get("factRefs") or []:
            period = ref.get("period") or {}
            refs.append(
                (
                    ref.get("key"),
                    _as_date(period.get("startDate")),
                    _as_date(period.get("endDate")),
                )
            )
        dependencies[UUID(str(criterion_id))] = refs
    return dependencies
