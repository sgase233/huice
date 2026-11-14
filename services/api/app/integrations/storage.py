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
import logging
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable, Iterator, Mapping, Sequence
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ..agents.contracts import MaterialRequirement, PolicySnapshot, SnapshotCriterion
from ..agents.ingestion import PreparedSource
from ..agents.profile import (
    NormalizedFact,
    NormalizedProfile,
    fact_version_of,
    to_wire_fact,
)
from ..db import Database
from ..demo_quota import DemoQuota
from ..domain import versioning
from ..domain.findings import PRESERVED_SOURCE_FINDING_CODES
from ..domain.rules import CriterionRule
from ..domain.versioning import ChangedEvent, PolicyBoundary
from ..errors import ApiProblem
from ..materials.catalog import MaterialError, MaterialRow
from ..schemas import (
    ApplicationWindow,
    AssessmentRun,
    Criterion,
    Enterprise,
    EnterpriseSummary,
    EnterpriseSummaryList,
    Period,
    PolicyAssessment,
    PolicyDetail,
    PolicyDraft,
    PolicyDraftList,
    PolicyImportMetadata,
    PolicyList,
    PolicySummary,
    PreparationTemplate,
    RunSummary,
    RunSummaryList,
    SourceDocument,
    SourceSpan,
    TraceResponse,
    TraceStep,
    ValidationFinding,
)

logger = logging.getLogger("huice.api")

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

#: 全国性政策 ``policy_version.region`` 的取值。行政区划码只覆盖地方，国家层面的
#: 政策没有对应区划码，M3 在 `source_register.csv` 里统一记 ``CN``。
#:
#: API-02 按 region 过滤时的口径是「该区划 + 全国」，见 :meth:`list_public_policies`。
#: 这是一个约定值而不是国标码，改它必须同时改 `source_register.csv` 与契约描述。
NATIONAL_REGION_CODE = "CN"

#: AUTH-06/07 的可见范围。路由按身份选一个字面量，SQL 片段只从这里取，
#: 调用方给不了任意拼接的谓词；``owned`` 用 ``%(viewer)s`` 绑当前账号。
_ENTERPRISE_SCOPE_SQL = {
    "all": "TRUE",
    "owned": "e.owner_user_id = %(viewer)s::uuid",
    "unowned": "e.owner_user_id IS NULL",
}

_RUN_SCOPE_SQL = {
    "all": "TRUE",
    "owned": "r.user_id = %(viewer)s::uuid",
    "unowned": "r.user_id IS NULL",
}


def _ownership_of(owner_user_id: UUID | None, viewer_user_id: UUID | None) -> str:
    """列表里的 ``ownership``：相对**当前调用者**而言，不是绝对归属。"""

    if owner_user_id is None:
        return "unowned"
    return "self" if owner_user_id == viewer_user_id else "other"

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
    scope, idempotency_key, request_fingerprint, response, revision, usage_semantics
) VALUES (
    %(scope)s, %(key)s, %(fingerprint)s, %(response)s, %(revision)s, %(usage_semantics)s
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

#: 一次判断要用的材料要求：显式挂在这几项政策上的，加同类型「全部适用」的
#: （`applies_to_policy_codes` 为空 = 该 policy_type 下全部政策，见 MATERIAL_REQUIREMENTS §1）。
#: `bound_policy_id` 为空表示后者，由 :meth:`PolicyRepository._load_materials` 按类型摊到每项政策。
_MATERIALS_QUERY = """
SELECT mr.material_id, mr.policy_type, mr.title, mr.is_required, mr.condition_desc,
       mr.description, mr.proves_fact_keys, mp.policy_id AS bound_policy_id
FROM material_requirement mr
LEFT JOIN material_requirement_policy mp ON mp.material_id = mr.material_id
WHERE mp.policy_id = ANY(%s) OR mr.applies_to_policy_codes = '[]'::jsonb
ORDER BY mr.material_id, mp.policy_id
"""

#: 业务代号 → 政策：材料表的 `applies_to_policy_ids` 是 `source_register.csv` 的代号，
#: 库里只有 UUID，所以按文号在**已发布**版本里找。同一文号对应多项政策时算歧义，不猜。
_POLICY_BY_DOCUMENT_NUMBER = """
SELECT document_number, policy_id, policy_type, version
FROM policy_version
WHERE document_number = ANY(%s) AND review_status = 'published'
ORDER BY imported_at DESC, id DESC
"""

_INSERT_MATERIAL = """
INSERT INTO material_requirement (
    material_id, policy_type, title, is_required, condition_desc, description,
    proves_fact_keys, applies_to_policy_codes, source_basis, review_status, imported_at
) VALUES (
    %(material_id)s, %(policy_type)s, %(title)s, %(is_required)s, %(condition_desc)s,
    %(description)s, %(proves_fact_keys)s, %(applies_to_policy_codes)s, %(source_basis)s,
    %(review_status)s, %(imported_at)s
)
ON CONFLICT (material_id) DO UPDATE SET
    policy_type             = EXCLUDED.policy_type,
    title                   = EXCLUDED.title,
    is_required             = EXCLUDED.is_required,
    condition_desc          = EXCLUDED.condition_desc,
    description             = EXCLUDED.description,
    proves_fact_keys        = EXCLUDED.proves_fact_keys,
    applies_to_policy_codes = EXCLUDED.applies_to_policy_codes,
    source_basis            = EXCLUDED.source_basis,
    review_status           = EXCLUDED.review_status,
    imported_at             = EXCLUDED.imported_at
"""


def insert_enterprise_profile(
    conn: Connection,
    *,
    enterprise_id: UUID,
    profile: NormalizedProfile,
    owner_user_id: UUID | None = None,
) -> None:
    """在调用方的事务里插入企业画像与全部事实。

    ``owner_user_id`` 为 ``None`` 表示无主（演示样本与历史合成企业）；注册事务传
    账号 ID 来绑定归属（#54 D1.3）。企业表的唯一索引保证「一家企业至多一个账号」，
    所以这里不做「先查后插」。
    """

    conn.execute(
        "INSERT INTO enterprise (id, name_synthetic, period_start, period_end, owner_user_id) "
        "VALUES (%(id)s, %(name)s, %(start)s, %(end)s, %(owner)s)",
        {
            "id": enterprise_id,
            "name": profile.name_synthetic,
            "start": profile.period_start,
            "end": profile.period_end,
            "owner": owner_user_id,
        },
    )
    for fact in profile.facts:
        conn.execute(_UPSERT_FACT, _fact_params(enterprise_id, fact))


def _fact_params(enterprise_id: UUID, fact: NormalizedFact) -> dict[str, Any]:
    params: dict[str, Any] = {
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


#: 失败分类：只放分类，不放消息。详细原因仍走日志，库里的列不承载堆栈或连接串。
ERROR_CATEGORIES = (
    "policy_not_published",
    "model_unavailable",
    "dependency_unavailable",
    "internal",
)


@dataclass(frozen=True)
class RunHeader:
    """T1 要写的身份信息。评估开始前就固定下来，之后任何一步都不许改写。"""

    id: UUID
    enterprise_id: UUID
    as_of: datetime
    created_at: datetime
    period_start: date
    period_end: date
    fact_version: str
    model_or_rule_version: str
    supersedes_run_id: UUID | None
    user_id: UUID | None


class RunSession:
    """``begin_run`` 打开的一次运行：T2 成功、T3 失败，各自一个事务。

    两条路径都不改身份列——库里的触发器会拦（012 的 ``guard_assessment_run_update``）。
    """

    def __init__(self, repository: "PolicyRepository", conn: Connection, header: RunHeader) -> None:
        self._repository = repository
        self._conn = conn
        self._header = header

    def succeed(
        self,
        *,
        run: AssessmentRun,
        dependencies: Mapping[UUID, Sequence[tuple[str, Any, Any]]],
        trace_steps: Sequence[Any],
        trace_id: str,
        policy_version_ids: Mapping[UUID, UUID],
    ) -> None:
        """T2：结果行与 ``succeeded`` 在同一个事务里提交。

        只提交一次：结果已写、状态还停在 ``running`` 的中间态不允许被看到。
        """

        with self._conn.transaction():
            self._repository.save_run(
                run=run,
                dependencies=dependencies,
                trace_steps=trace_steps,
                trace_id=trace_id,
                policy_version_ids=policy_version_ids,
                user_id=self._header.user_id,
                connection=self._conn,
                insert=False,
            )
            self._conn.execute(
                "UPDATE assessment_run SET status = 'succeeded', finished_at = now() "
                "WHERE id = %s",
                (self._header.id,),
            )

    def fail(self, error_category: str) -> None:
        """T3：失败只写状态与分类，不写任何 ``assessment`` 行。"""

        if error_category not in ERROR_CATEGORIES:  # pragma: no cover - 调用方只传常量
            error_category = "internal"
        with self._conn.transaction():
            self._conn.execute(
                "UPDATE assessment_run SET status = 'failed', finished_at = now(), "
                "error_category = %s WHERE id = %s",
                (error_category, self._header.id),
            )


class PolicyRepository:
    def __init__(self, db: Database, cursor_secret: str) -> None:
        self._db = db
        self._secret = cursor_secret.encode("utf-8")

    # ------------------------------------------------------------------ write

    @contextmanager
    def _transaction(self, connection: Connection | None) -> Iterator[Connection]:
        """在自己的事务里跑，或并入调用方已经打开的那个事务。

        E2 的候选提交要求「建草稿、存条件、标记候选已提交」三件事事务一致
        （EXTENSION_INTERFACES §4.3.6）。如果本方法每次都自带事务，调用方就只能
        在提交之后再补一步，失败时留下孤儿草稿——所以这里允许传入外部连接，事务
        边界由调用方划。
        """

        if connection is not None:
            yield connection
            return
        with self._db.connection() as own, own.transaction():
            yield own

    def create_policy_version(
        self,
        *,
        metadata: PolicyImportMetadata,
        sources: Sequence[PreparedSource],
        storage_keys: Sequence[str],
        trace_id: str,
        started_at: datetime | None = None,
        connection: Connection | None = None,
    ) -> PolicyDraft:
        if len(storage_keys) != len(sources):
            raise ValueError("storage_keys 与 sources 数量不一致")

        imported_at = datetime.now(timezone.utc)
        started = started_at or imported_at
        version_id = uuid4()
        revision = uuid4()
        source_ids = [uuid4() for _ in sources]

        with self._transaction(connection) as conn:
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
                        # 每份来源优先用它自己的链接；上传路径下为 None，沿用整份
                        # 政策登记的入口，行为与之前一致。
                        "source_url": source.source_url or metadata.source_url,
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

        ``region`` 按「该区划 + 全国性政策」匹配：地方区划码只覆盖地方，全国性政策
        记 :data:`NATIONAL_REGION_CODE`，只做等值会把深圳企业该适用的国家税收政策
        藏掉。``asOf`` 只是元数据过滤（当天是否处于适用期），不用于挑历史版本；关闭状态
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
              -- 按 region 筛 = 该区划 + 全国性政策。区划码只覆盖地方，全国性政策记
              -- NATIONAL_REGION_CODE；只做等值会把深圳企业该适用的国家税收政策全部
              -- 藏掉（2026-10-29 按 M3 数据口径修）。方向只放宽不收紧：不会因为多加了
              -- 全国政策而漏掉本来命中的地方政策。
              AND (
                    %(region)s::text IS NULL
                    OR pv.region = %(region)s
                    OR pv.region = %(national_region)s
                  )
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
            "national_region": NATIONAL_REGION_CODE,
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
        extraction_model: str | None = None,
        extraction_elapsed_ms: int = 0,
        extraction_input_chars: int = 0,
        connection: Connection | None = None,
    ) -> PolicyDraft:
        """整体替换某版本的条款与校验发现（compare-and-swap）。

        ``expected_revision`` 来自请求的 If-Match；与库中当前 revision 不一致说明有人
        先改过，返回 409 并带 ``currentVersion``，绝不覆盖并发的人工修改。

        ``extraction_model`` 只有「本次真的做了抽取」的调用方才会传：传了就在同一事务
        里、引用校验步骤之前补一条 ``extraction`` 轨迹。手工保存条款（API-06）不传，
        因此不会凭空多出一条抽取记录。幂等重放不会走到这里，也就不会重复留痕。

        调用方传进来的 ``findings`` 是**重新计算**出来的条件级结论，本方法整体替换
        该版本的 findings；唯一的例外是 :data:`PRESERVED_SOURCE_FINDING_CODES` 里的
        来源级码（如 ``ATTACHMENT_NOT_EXTRACTED``）——它们描述来源本身，与条件校验
        无关，重算时从库里原样保留。保留只按服务端已有记录，不看客户端传了什么，
        也不会把过时的条件错误一起留下。
        """

        started = started_at or datetime.now(timezone.utc)
        new_revision = uuid4()

        with self._transaction(connection) as conn:
            row = self._lock_version(conn, policy_id, version)
            self._assert_editable(row, expected_revision)

            preserved = self._preserved_source_findings(conn, row["id"], findings)

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
            for ordinal, finding in enumerate([*findings, *preserved]):
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
            if extraction_model is not None:
                self._insert_trace(
                    conn,
                    trace_id=trace_id,
                    stage="extraction",
                    input_summary=f"抽取正文 {max(0, int(extraction_input_chars))} 字",
                    output_summary=f"得到条件 {len(criteria)} 条、校验发现 {len(findings)} 条",
                    policy_version_id=row["id"],
                    policy_id=row["policy_id"],
                    policy_version=row["version"],
                    model_or_rule_version=extraction_model,
                    elapsed_ms=max(0, int(extraction_elapsed_ms)),
                    status="needs_review" if findings else "ok",
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

    def record_extraction_failure(
        self,
        *,
        policy_id: UUID,
        version: str,
        trace_id: str,
        status: str,
        error_code: str,
        model_or_rule_version: str,
        elapsed_ms: int,
        usage_unknown: bool,
    ) -> None:
        """把一次失败的抽取记成一条 ``extraction`` 轨迹步骤。

        **独立事务**，只挂既有的政策版本，不改草稿、不动 revision：调用已经在
        草稿保存之前发生，模型可能已经计费，不能因为后面的保存失败或进程中断就
        让这次调用从库里消失。摘要只有错误码与用量口径，不写正文、提示词与凭据。

        ``status`` 由调用方按可重试性决定（``retryable_error`` / ``fatal_error``）。
        版本不存在等自身失败按异常抛给调用方，由调用方兜底记日志、不替换原错误。
        """

        with self._db.connection() as conn, conn.transaction():
            row = self._fetchone(
                conn,
                "SELECT id, policy_id, version FROM policy_version "
                "WHERE policy_id = %s AND version = %s",
                (policy_id, version),
            )
            if row is None:
                raise ApiProblem(404, "POLICY_NOT_FOUND", "没有找到对应的政策版本。")
            usage = (
                "上游是否计费无法确认，用量按未知记录"
                if usage_unknown
                else "未发起模型调用，没有用量"
            )
            self._insert_trace(
                conn,
                trace_id=trace_id,
                stage="extraction",
                input_summary="模型抽取失败",
                output_summary=f"错误码 {error_code}；{usage}",
                policy_version_id=row["id"],
                policy_id=row["policy_id"],
                policy_version=row["version"],
                model_or_rule_version=model_or_rule_version,
                elapsed_ms=max(0, int(elapsed_ms)),
                status=status,
                error_code=error_code,
            )

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

    def read_idempotent(
        self,
        scope: str,
        key: UUID,
        *,
        connection: Connection | None = None,
    ) -> dict[str, Any] | None:
        """取回同一作用域下同键的已完成结果；没有则返回 ``None``。

        传入 ``connection`` 时在调用方的事务里读，看到的是该事务此刻的快照——
        候选提交要在**同一把键锁**下先读后写，不能各读各的连接（见
        :meth:`lock_idempotency_key`）。
        """

        with self._transaction(connection) as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT request_fingerprint, response, revision, usage_semantics "
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
            "usage_semantics": row["usage_semantics"],
        }

    def lock_idempotency_key(self, conn: Connection, scope: str, key: UUID) -> None:
        """按 (scope, key) 取事务级 advisory lock，串行化同键请求。

        幂等要求「读记录 → 决定重放还是执行 → 写记录」是一个原子动作。只在事务外
        先读一次不够：两个并发请求会同时读到空，各自把活干完，而 ``save_idempotent``
        的 ``ON CONFLICT DO NOTHING`` 只保留其中一条——另一个客户端拿到的结果没有任何
        幂等记录，重试时会变成「键被复用」。

        锁随事务结束自动释放，等待方拿到锁时前者已提交，随后的一次读就能看到那条记录。
        """

        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))",
            (scope, str(key)),
        )

    def save_idempotent(
        self,
        scope: str,
        key: UUID,
        fingerprint: str,
        response: dict[str, Any],
        revision: UUID,
        *,
        usage_semantics: str,
        connection: Connection | None = None,
    ) -> None:
        """记录一次抽取结果。表只追加，重复键保留首次写入的那条。

        ``usage_semantics`` 说明这条记录的用量从哪一代服务端来（迁移 007）：
        ``'explicit'`` = rc.7 起，未知一定带 ``tokensUnknown: true``；``'legacy'``
        = 更早的写入，两个计数曾被各自 ``or 0`` 兜底。重放时按它决定要不要把用量
        视为未确认——只看数值判不出来（见 ``routes/admin_policies.py``）。
        """

        with self._transaction(connection) as conn:
            self._execute(
                conn,
                _INSERT_IDEMPOTENT,
                {
                    "scope": scope,
                    "key": key,
                    "fingerprint": fingerprint,
                    "response": Jsonb(response),
                    "revision": revision,
                    "usage_semantics": usage_semantics,
                },
            )

    # ------------------------------------------------------------- 企业画像

    def create_enterprise(
        self,
        *,
        profile: NormalizedProfile,
        trace_id: str,
        owner_user_id: UUID | None = None,
        demo_quota: DemoQuota | None = None,
    ) -> Enterprise:
        """建一家企业；演示身份的配额与这家企业在**同一个事务**里。

        ``demo_quota`` 非空时先占名额再 INSERT：任何一步失败（含并发绑定撞唯一索引）
        整个事务回滚，名额也一起退回，不会出现「扣了名额、样本没建成」。占额仍是
        ``INSERT … ON CONFLICT DO UPDATE … WHERE`` 的单条语句，并发下只会有一个请求
        拿到最后一个名额。
        """

        enterprise_id = uuid4()
        try:
            with self._db.connection() as conn, conn.transaction():
                if demo_quota is not None:
                    self._claim_demo_sample(conn, demo_quota)
                insert_enterprise_profile(
                    conn,
                    enterprise_id=enterprise_id,
                    profile=profile,
                    owner_user_id=owner_user_id,
                )
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
        except UniqueViolation:
            # 路由层已经先查过 ``identity.enterprise_id``；两个请求同时到达时都会看到
            # NULL，唯一索引才是真正的判定点。这里把它翻译成与串行路径同一个 409，
            # 而不是漏成 500——「一家企业一个账号」的规则对并发一样成立。
            raise self._already_bound_problem(owner_user_id) from None
        return _enterprise_of(enterprise_id, profile.name_synthetic, profile.period_start, profile.period_end, facts)

    def _claim_demo_sample(self, conn: Connection, quota: DemoQuota) -> None:
        """在调用方的事务里占一个名额；无名额时抛 429，让整个事务回滚。

        首建与计数是一条语句：两条（先查后插）在并发下会丢计数，上限就形同虚设。
        """

        row = self._fetchone(
            conn,
            """
            INSERT INTO demo_quota (sid_hash, created, expires_at)
            VALUES (%s, 1, %s)
            ON CONFLICT (sid_hash) DO UPDATE
               SET created = demo_quota.created + 1
             WHERE demo_quota.created < %s
               AND demo_quota.expires_at > now()
            RETURNING created
            """,
            (quota.sid_hash, quota.expires_at, quota.limit),
        )
        if row is None:
            raise ApiProblem(
                429,
                "DEMO_QUOTA_EXCEEDED",
                "本次演示会话可创建的样本数已用完，请从演示入口重新进入。",
                {"limit": quota.limit},
            )

    def _already_bound_problem(self, owner_user_id: UUID | None) -> ApiProblem:
        bound_id = None
        if owner_user_id is not None:
            with self._db.connection() as conn:
                row = self._fetchone(
                    conn,
                    "SELECT id FROM enterprise WHERE owner_user_id = %s",
                    (owner_user_id,),
                )
                bound_id = None if row is None else row["id"]
        details = {"enterpriseId": str(bound_id)} if bound_id is not None else {}
        return ApiProblem(
            409,
            "ENTERPRISE_ALREADY_BOUND",
            "当前账号已绑定企业，不能重复创建；如需更新请调用 PATCH 事实接口。",
            details,
        )

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

    def enterprise_owner(self, enterprise_id: UUID) -> UUID | None:
        """归属账号；``None`` 表示无主（演示样本与历史合成企业）。

        对象不存在与「存在但无权访问」在路由层返回同一个 404 与同一文案，所以这里
        只用与 :meth:`get_enterprise` 相同的码与文案。
        """

        with self._db.connection() as conn:
            row = self._fetchone(
                conn, "SELECT owner_user_id FROM enterprise WHERE id = %s", (enterprise_id,)
            )
        if row is None:
            raise ApiProblem(404, "ENTERPRISE_NOT_FOUND", "没有找到对应的企业画像。")
        return row["owner_user_id"]

    def list_enterprises(
        self,
        *,
        scope: str,
        viewer_user_id: UUID | None,
        cursor: str | None,
        limit: int,
    ) -> EnterpriseSummaryList:
        """AUTH-06：按身份定可见集，再分页。游标绑定身份与可见范围。

        ``scope`` 只接受三个字面量（``all`` / ``owned`` / ``unowned``），由路由按身份
        决定；SQL 片段从白名单字典里取，不接受调用方拼字符串。
        """

        predicate = _ENTERPRISE_SCOPE_SQL.get(scope)
        if predicate is None:  # pragma: no cover - 路由只传三个字面量
            raise ValueError(f"未知的企业列表范围：{scope}")
        fingerprint = f"enterprises:{scope}:{viewer_user_id}"
        after = self._decode_cursor(cursor, fingerprint) if cursor else None
        after_ts, after_id = after if after else (None, None)

        sql = f"""
        SELECT e.id, e.name_synthetic, e.period_start, e.period_end,
               e.owner_user_id, e.created_at
        FROM enterprise e
        WHERE {predicate}
          AND (
                %(after_ts)s::timestamptz IS NULL
                OR (e.created_at, e.id) < (%(after_ts)s::timestamptz, %(after_id)s::uuid)
              )
        ORDER BY e.created_at DESC, e.id DESC
        LIMIT %(limit)s
        """
        params = {
            "viewer": viewer_user_id,
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
            EnterpriseSummary(
                id=row["id"],
                name_synthetic=row["name_synthetic"],
                period=Period(
                    start_date=row["period_start"], end_date=row["period_end"]
                ),
                ownership=_ownership_of(row["owner_user_id"], viewer_user_id),
            )
            for row in rows
        ]
        next_cursor = (
            self._encode_cursor(rows[-1]["created_at"], rows[-1]["id"], fingerprint)
            if has_more and rows
            else None
        )
        return EnterpriseSummaryList(items=items, next_cursor=next_cursor)

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
        """保留为方法名是为了不动 ``update_fact`` 的调用点；实现在模块级（注册事务共用）。"""

        return _fact_params(enterprise_id, fact)

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
            materials = self._load_materials(
                cur,
                [(policy_id, latest[policy_id]["policy_type"]) for policy_id in policy_ids],
            )

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
                    materials=materials.get(policy_id, ()),
                    closed=row["status"] == "closed",
                )
            )
        return snapshots

    def _load_materials(
        self,
        cur,
        targets: Sequence[tuple[UUID, str]],
    ) -> dict[UUID, tuple[MaterialRequirement, ...]]:
        """按政策取回已仲裁的材料要求：显式挂在这项政策上的，加同类型「全部适用」的。

        只读已导入的表；CSV 不参与判断链路。行的顺序按材料代号，接口输出因此稳定。
        """

        if not targets:
            return {}
        cur.execute(_MATERIALS_QUERY, ([item[0] for item in targets],))
        rows = cur.fetchall()
        wanted = {policy_id: policy_type for policy_id, policy_type in targets}
        by_policy: dict[UUID, list[MaterialRequirement]] = {item[0]: [] for item in targets}
        for row in rows:
            material = MaterialRequirement(
                material_id=row["material_id"],
                policy_type=row["policy_type"],
                title=row["title"],
                is_required=row["is_required"],
                condition_desc=row["condition_desc"],
                description=row["description"],
                proves_fact_keys=tuple(str(key) for key in row["proves_fact_keys"]),
            )
            for policy_id, policy_type in wanted.items():
                # 挂错类型是导入侧的错，不该在判断时悄悄混进清单
                if material.policy_type != policy_type:
                    continue
                bound = row["bound_policy_id"]
                if bound is None or bound == policy_id:
                    by_policy[policy_id].append(material)
        return {
            policy_id: tuple(sorted(items, key=lambda item: item.material_id))
            for policy_id, items in by_policy.items()
        }

    # ------------------------------------------------------------ 材料要求导入

    def import_material_requirements(
        self,
        rows: Sequence[MaterialRow],
        *,
        document_numbers: Mapping[str, Sequence[str]],
    ) -> dict[str, Any]:
        """把已仲裁的材料要求整体换成这一份（幂等）：本次没有的行会被删掉。

        ``document_numbers`` 是 ``source_register.csv`` 的「业务代号 → 登记过的文号」；材料表
        里的 ``applies_to_policy_ids`` 只能借它落到库里的政策上。任何一处解析不出来都整体
        失败——宁可导入不成功，也不要把一份「少了几个政策」的清单写进库。
        """

        if not rows:
            raise MaterialError("没有可导入的材料要求：CSV 里没有复核状态为「已仲裁」的行")

        wanted = sorted({code for row in rows for code in row.applies_to_policy_codes})
        unknown = [code for code in wanted if code not in document_numbers]
        if unknown:
            raise MaterialError(
                f"source_register.csv 里没有这些政策代号，无法解析成库里的政策：{', '.join(unknown)}"
            )

        imported_at = datetime.now(timezone.utc)
        with self._db.connection() as conn, conn.transaction():
            resolved = self._resolve_material_policies(
                conn, {code: document_numbers[code] for code in wanted}
            )
            for row in rows:
                for code in row.applies_to_policy_codes:
                    policy_type = resolved[code][1]
                    if policy_type != row.policy_type:
                        raise MaterialError(
                            f"{row.material_id} 的 policy_type 是 {row.policy_type}，"
                            f"但政策 {code} 是 {policy_type}：材料与政策的类型必须一致"
                        )
                self._execute(conn, _INSERT_MATERIAL, self._material_params(row, imported_at))
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(
                        "DELETE FROM material_requirement_policy WHERE material_id = %s",
                        (row.material_id,),
                    )
                    for code in row.applies_to_policy_codes:
                        cur.execute(
                            "INSERT INTO material_requirement_policy (material_id, policy_id) "
                            "VALUES (%s, %s) ON CONFLICT DO NOTHING",
                            (row.material_id, resolved[code][0]),
                        )

            kept = [row.material_id for row in rows]
            stale = [
                item["material_id"]
                for item in self._fetchall(
                    conn,
                    "SELECT material_id FROM material_requirement WHERE NOT (material_id = ANY(%s))"
                    " ORDER BY material_id",
                    (kept,),
                )
            ]
            if stale:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM material_requirement WHERE NOT (material_id = ANY(%s))",
                        (kept,),
                    )

        return {
            "imported": sorted(row.material_id for row in rows),
            "removed": stale,
            "policies": {code: str(resolved[code][0]) for code in wanted},
        }

    @staticmethod
    def _material_params(row: MaterialRow, imported_at: datetime) -> dict[str, Any]:
        return {
            "material_id": row.material_id,
            "policy_type": row.policy_type,
            "title": row.title,
            "is_required": row.is_required,
            "condition_desc": row.condition_desc,
            "description": row.description,
            "proves_fact_keys": Jsonb(list(row.proves_fact_keys)),
            "applies_to_policy_codes": Jsonb(list(row.applies_to_policy_codes)),
            "source_basis": row.source_basis,
            "review_status": row.review_status,
            "imported_at": imported_at,
        }

    @staticmethod
    def _resolve_material_policies(
        conn: Connection, codes: Mapping[str, Sequence[str]]
    ) -> dict[str, tuple[UUID, str]]:
        """业务代号 → (policy_id, policy_type)；按文号找已发布版本，最新的一版为准。

        一个代号可以登记多份文号（同一目录的新旧两版）。只有当这些文号在库里指向
        **不同**政策时才算歧义——同一项政策的多份文号照常解析。
        """

        if not codes:
            return {}
        documents = sorted({document for docs in codes.values() for document in docs})
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(_POLICY_BY_DOCUMENT_NUMBER, (documents,))
            rows = cur.fetchall()

        by_document: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for row in rows:
            by_document[row["document_number"]].append(row)

        resolved: dict[str, tuple[UUID, str]] = {}
        for code, code_documents in codes.items():
            found = [row for document in code_documents for row in by_document.get(document, [])]
            if not found:
                listed = "、".join(code_documents)
                raise MaterialError(
                    f"政策 {code} 的文号「{listed}」在库里没有已发布的政策版本："
                    "请先导入并发布该政策，再导入材料要求"
                )
            policy_ids = {row["policy_id"] for row in found}
            if len(policy_ids) > 1:
                listed = "、".join(code_documents)
                raise MaterialError(
                    f"政策 {code} 登记了多个文号（{listed}），在库里对应了多个政策，"
                    "无法判定材料挂在哪一项"
                )
            resolved[code] = (found[0]["policy_id"], found[0]["policy_type"])
        return resolved

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

    @contextmanager
    def begin_run(self, header: "RunHeader") -> Iterator["RunSession"]:
        """T1：插 ``running`` 行并占住该 run 的 advisory lock（E3 B2）。

        连接用 ``dedicated_connection`` 而不是池里的：会话级 advisory lock 跟着连接走，
        而池会把连接还回去给别的请求；评估还可能持续数十秒到数分钟，占着池里的名额会
        把并发上限耗光。这也是 E2 发现运行已经采用的同一条规则。

        锁键用 ``hashtextextended(id, 0)``：与中断扫描里的表达式必须逐字一致，否则
        「锁还被持有」这一判据会永远为假。
        """

        with self._db.dedicated_connection() as conn:
            conn.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (str(header.id),))
            try:
                with conn.transaction():
                    self._execute(
                        conn,
                        """
                        INSERT INTO assessment_run (
                            id, enterprise_id, as_of, status, fact_version, model_or_rule_version,
                            created_at, period_start, period_end, supersedes_run_id, user_id,
                            lock_owner_pid, lock_owner_started_at
                        ) VALUES (
                            %(id)s, %(enterprise_id)s, %(as_of)s, 'running', %(fact_version)s,
                            %(rule_version)s, %(created_at)s, %(period_start)s, %(period_end)s,
                            %(supersedes_run_id)s, %(user_id)s, pg_backend_pid(),
                            (SELECT backend_start FROM pg_stat_activity
                              WHERE pid = pg_backend_pid())
                        )
                        """,
                        {
                            "id": header.id,
                            "enterprise_id": header.enterprise_id,
                            "as_of": header.as_of,
                            "fact_version": header.fact_version,
                            "rule_version": header.model_or_rule_version,
                            "created_at": header.created_at,
                            "period_start": header.period_start,
                            "period_end": header.period_end,
                            "supersedes_run_id": header.supersedes_run_id,
                            "user_id": header.user_id,
                        },
                    )
                yield RunSession(self, conn, header)
            finally:
                try:
                    conn.execute(
                        "SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (str(header.id),)
                    )
                except Exception:  # noqa: BLE001 - 连接已断时锁随连接消失，不能掩盖真实异常
                    logger.debug("释放 run 锁失败，已忽略", exc_info=True)

    def mark_interrupted_runs(self, *, max_duration_seconds: int) -> list[UUID]:
        """把「超时 + 锁未被持有 + 原连接已断开」三者同时成立的 running 行标为中断。

        三条缺一不可（METRICS_SPEC §8.1 第 5 项）：少任何一条都会把仍在跑的运行标死。
        原连接用 ``(pid, backend_start)`` 成对判断——PID 会被复用，只比 pid 会把活着
        的新连接当成死掉的旧连接。
        """

        marked: list[UUID] = []
        with self._db.dedicated_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, lock_owner_pid, lock_owner_started_at
                FROM assessment_run
                WHERE status = 'running'
                  AND lock_owner_pid IS NOT NULL
                  AND created_at < now() - make_interval(secs => %s)
                """,
                (max_duration_seconds,),
            ).fetchall()
            for run_id, pid, started_at in rows:
                key = str(run_id)
                got = conn.execute(
                    "SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (key,)
                ).fetchone()
                if not got or not got[0]:
                    continue  # 锁仍被持有：这一轮还在跑
                # 立刻释放：扫描只是试探，不能顺手占着锁让下一轮扫不到
                conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (key,))
                alive = conn.execute(
                    "SELECT 1 FROM pg_stat_activity WHERE pid = %s AND backend_start = %s",
                    (pid, started_at),
                ).fetchone()
                if alive:
                    continue  # 原连接还在
                conn.execute(
                    "UPDATE assessment_run SET status = 'interrupted', finished_at = now() "
                    "WHERE id = %s AND status = 'running'",
                    (run_id,),
                )
                marked.append(run_id)
        return marked

    def save_run(
        self,
        *,
        run: AssessmentRun,
        dependencies: Mapping[UUID, Sequence[tuple[str, Any, Any]]],
        trace_steps: Sequence[Any],
        trace_id: str,
        policy_version_ids: Mapping[UUID, UUID],
        user_id: UUID | None = None,
        connection: Connection | None = None,
        insert: bool = True,
    ) -> None:
        """一次事务写入 run、每项政策的结果快照、依赖与在线轨迹。

        ``user_id`` 是发起账号（E1 归属）；demo 建的 run 与历史数据为 ``None``，
        列表与读取按「无主」对待。

        ``connection`` 给定时并入调用方已打开的那条连接：B2 的运行生命周期把 T1
        （插 ``running`` 行）与 T2（写结果 + 改 ``succeeded``）拆成两个事务，但都
        在同一条连接、同一把 advisory lock 之下。``insert=False`` 表示 run 行已经
        由 T1 插入，这里只写结果。
        """

        if connection is None:
            with self._db.connection() as conn, conn.transaction():
                self.save_run(
                    run=run,
                    dependencies=dependencies,
                    trace_steps=trace_steps,
                    trace_id=trace_id,
                    policy_version_ids=policy_version_ids,
                    user_id=user_id,
                    connection=conn,
                )
            return

        conn = connection
        if insert:
            self._execute(
                conn,
                """
                INSERT INTO assessment_run (
                    id, enterprise_id, as_of, status, fact_version, model_or_rule_version,
                    created_at, finished_at, period_start, period_end, supersedes_run_id,
                    user_id
                ) VALUES (
                    %(id)s, %(enterprise_id)s, %(as_of)s, 'succeeded', %(fact_version)s,
                    %(rule_version)s, %(created_at)s, %(created_at)s, %(period_start)s,
                    %(period_end)s, %(supersedes_run_id)s, %(user_id)s
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
                    "user_id": user_id,
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
                    # 列名沿用 evidence_refs（迁移不改列），内容是 TraceStep.objectRefs。
                    "evidence_refs": Jsonb(list(step.object_refs)),
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
            # E3 B2：运行有生命周期了，未成功的 run 能列、能查状态，但拿不到结果。
            # 409 而不是 404——「还没完成」与「不存在」从此可区分，也与 API-13 同码。
            if header["status"] != "succeeded":
                raise ApiProblem(
                    409,
                    "RUN_NOT_COMPLETED",
                    "该判断运行尚未成功完成，暂时没有结果。",
                    {"status": header["status"]},
                )
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

    def run_status(self, run_id: UUID) -> str:
        """只取生命周期状态；报告下载要按它决定 409 还是出文件。

        与 :meth:`get_run` 同一个 404 码与文案（历史数据: ``succeeded``）。
        """

        with self._db.connection() as conn:
            row = self._fetchone(
                conn, "SELECT status FROM assessment_run WHERE id = %s", (run_id,)
            )
        if row is None:
            raise ApiProblem(404, "RUN_NOT_FOUND", "没有找到对应的判断运行。")
        return row["status"]

    def run_owner(self, run_id: UUID) -> UUID | None:
        """run 的归属账号；``None`` 表示无主（demo 建的历史运行）。"""

        with self._db.connection() as conn:
            row = self._fetchone(
                conn, "SELECT user_id FROM assessment_run WHERE id = %s", (run_id,)
            )
        if row is None:
            raise ApiProblem(404, "RUN_NOT_FOUND", "没有找到对应的判断运行。")
        return row["user_id"]

    def list_runs(
        self,
        *,
        scope: str,
        viewer_user_id: UUID | None,
        enterprise_id: UUID | None,
        cursor: str | None,
        limit: int,
    ) -> RunSummaryList:
        """AUTH-07：先按身份定可见集，再在可见集里按 ``enterprise_id`` 筛。

        ``enterprise_id`` 的可筛范围由路由先校验（越界一律 404 ``ENTERPRISE_NOT_FOUND``），
        这里只负责「在已经允许的集合里再筛一次」，不会因为传了别的企业而放宽可见范围。
        """

        predicate = _RUN_SCOPE_SQL.get(scope)
        if predicate is None:  # pragma: no cover - 路由只传三个字面量
            raise ValueError(f"未知的运行列表范围：{scope}")
        fingerprint = f"runs:{scope}:{viewer_user_id}:{enterprise_id}"
        after = self._decode_cursor(cursor, fingerprint) if cursor else None
        after_ts, after_id = after if after else (None, None)

        sql = f"""
        SELECT r.id, r.enterprise_id, r.as_of, r.created_at, r.status,
               EXISTS (SELECT 1 FROM assessment a WHERE a.run_id = r.id) AS has_results
        FROM assessment_run r
        WHERE {predicate}
          AND (%(enterprise_id)s::uuid IS NULL OR r.enterprise_id = %(enterprise_id)s::uuid)
          AND (
                %(after_ts)s::timestamptz IS NULL
                OR (r.created_at, r.id) < (%(after_ts)s::timestamptz, %(after_id)s::uuid)
              )
        ORDER BY r.created_at DESC, r.id DESC
        LIMIT %(limit)s
        """
        params = {
            "viewer": viewer_user_id,
            "enterprise_id": enterprise_id,
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
            RunSummary(
                id=row["id"],
                enterprise_id=row["enterprise_id"],
                as_of=row["as_of"],
                created_at=row["created_at"],
                status=row["status"],
                report_available=row["status"] == "succeeded" and bool(row["has_results"]),
            )
            for row in rows
        ]
        next_cursor = (
            self._encode_cursor(rows[-1]["created_at"], rows[-1]["id"], fingerprint)
            if has_more and rows
            else None
        )
        return RunSummaryList(items=items, next_cursor=next_cursor)

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
                    object_refs=_list_of(row["evidence_refs"]),
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

    @staticmethod
    def _preserved_source_findings(
        conn: Connection, version_id: UUID, findings: Sequence[FindingRecord]
    ) -> list[FindingRecord]:
        """取出库里已有的来源级提示，供重算条件 findings 时原样保留。

        API-05 重新抽取、API-06 保存条款都只产出条件级结论；保存是整体替换，若不先
        把来源级码捞出来，DISC-07 写进草稿的附件提示会在第一次保存时消失。只保留
        :data:`PRESERVED_SOURCE_FINDING_CODES`，找得到的旧条件结论与其它政策级结论
        一律不保留。调用方恰好也传了同一条时按 ``(code, message)`` 去重。
        """

        if not PRESERVED_SOURCE_FINDING_CODES:
            return []
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT code, message, source_spans
                FROM policy_validation_finding
                WHERE policy_version_id = %(id)s
                  AND criterion_id IS NULL
                  AND code = ANY(%(codes)s)
                ORDER BY ordinal
                """,
                {"id": version_id, "codes": sorted(PRESERVED_SOURCE_FINDING_CODES)},
            )
            rows = cur.fetchall()
        seen = {(item.criterion_id, item.code, item.message) for item in findings}
        preserved: list[FindingRecord] = []
        for item in rows:
            key = (None, item["code"], item["message"])
            if key in seen:
                continue
            seen.add(key)
            preserved.append(
                FindingRecord(
                    criterion_id=None,
                    code=item["code"],
                    message=item["message"],
                    spans=list(item["source_spans"] or []),
                )
            )
        return preserved

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
