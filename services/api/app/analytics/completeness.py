"""D4 资料完整度：按 ``(FactKey, period_start, period_end)`` 的单位统计。

口径见 ``docs/product/COMPLETENESS_SPEC.md``（M3 的 D4 唯一来源）：

- 单位 = 事实键 + 期间；同一键的不同年度/季度各成单位，不能合并；
- 七状态 ``filled / filled_unverified / answered_unknown / not_answered /
  not_applicable / applicability_unknown / not_due``；
- ``not_due`` 优先于「已填写」：未到期即使有值也不进分子分母（方案 C）；
- 分母为 0 时 ``ratio`` 为 ``null``，不是 0；
- 首版适用性是 **静态引用**（``d4.static.v1``）：scope 内条件直接引用的键即适用，
  不做 OR/AND 分支可达性判断；``manual_review`` 单列，不进完整度。

单位怎么来：直接复用求值器的事实引用收集（``domain.rules.referenced_facts``），
保证「完整度要求填什么」与「规则实际读什么」来自同一处，不各写一份。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Iterable, Sequence
from uuid import UUID

from ..db import Database
from ..domain.rules import referenced_facts
from ..domain.versioning import SHANGHAI
from ..errors import ApiProblem

DEFINITION_VERSION = "d4.static.v1"
APPLICABILITY_MODE = "static"

FILLED = "filled"
FILLED_UNVERIFIED = "filled_unverified"
ANSWERED_UNKNOWN = "answered_unknown"
NOT_ANSWERED = "not_answered"
NOT_APPLICABLE = "not_applicable"
APPLICABILITY_UNKNOWN = "applicability_unknown"
NOT_DUE = "not_due"

#: 季度键：同一季度的四个单位统一以「季度结束次日 00:00」为到期点（§3.1.1）。
QUARTERLY_KEYS = frozenset(
    {"assetsStart", "assetsEnd", "employeeCountStart", "employeeCountEnd"}
)

#: 分组按 §4 的 5/8/4/1；未知键归 ``other``，不悄悄并入某一组。
GROUP_LABELS: dict[str, str] = {
    "basic": "基础与资质",
    "operation": "经营与财务",
    "rd": "研发情况",
    "credit": "合规与信用",
    "other": "其他",
}

GROUP_OF: dict[str, str] = {
    "region": "basic",
    "industry": "basic",
    "establishedDate": "basic",
    "independentLegalEntity": "basic",
    "specificMarketSince": "basic",
    "revenue": "operation",
    "taxableIncome": "operation",
    "rdExpense": "operation",
    "assetsStart": "operation",
    "assetsEnd": "operation",
    "employeeCountStart": "operation",
    "employeeCountEnd": "operation",
    "equityFinancing": "operation",
    "rdActivity": "rd",
    "rdExpenseIncrease": "rd",
    "rdExpenseRevenueRatio": "rd",
    "rdAddDeductionClaimed": "rd",
    "creditRecord": "credit",
}


def unit_status(*, due: bool, present: bool, value_known: bool, verified: bool) -> str:
    """单个单位的状态；``not_due`` 最优先（§3.1.1 边界）。"""

    if not due:
        return NOT_DUE
    if not present:
        return NOT_ANSWERED
    if not value_known:
        return ANSWERED_UNKNOWN
    return FILLED if verified else FILLED_UNVERIFIED


def is_due(*, fact_key: str, period_start: date, period_end: date, as_of: datetime) -> bool:
    """方案 C 的到期判定（Asia/Shanghai）。

    - 季度键：``asOf < period_end 次日 00:00`` → 未到期；季度结束当天仍未到期。
    - 年度/跨年区间：``period_start > asOf 的当地日期`` → 未到期。
    """

    local = as_of.astimezone(SHANGHAI)
    if fact_key in QUARTERLY_KEYS:
        next_day = datetime.combine(period_end, time.min, tzinfo=SHANGHAI) + timedelta(days=1)
        return not (local < next_day)
    return period_start <= local.date()


@dataclass(frozen=True)
class _Unit:
    fact_key: str
    start: date
    end: date
    status: str

    @property
    def group(self) -> str:
        return GROUP_OF.get(self.fact_key, "other")

    @property
    def filled(self) -> bool:
        return self.status in (FILLED, FILLED_UNVERIFIED)


@dataclass(frozen=True)
class _ScopeVersion:
    version_id: UUID
    policy_id: UUID
    version: str


class CompletenessReader:
    """读 scope、事实与条件，算单位状态。只读；鉴权由路由负责。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    # ------------------------------------------------------------------ 入口

    def build(
        self,
        *,
        enterprise_id: UUID,
        as_of: datetime | None = None,
        run_id: UUID | None = None,
        policy_ids: Sequence[UUID] | None = None,
    ) -> dict[str, Any]:
        moment = (as_of or datetime.now(SHANGHAI)).astimezone(SHANGHAI)

        with self._db.connection() as conn:
            enterprise = conn.execute(
                "SELECT period_start, period_end FROM enterprise WHERE id = %s",
                (enterprise_id,),
            ).fetchone()
            if enterprise is None:
                raise ApiProblem(404, "ENTERPRISE_NOT_FOUND", "没有找到对应的企业。")
            period = (enterprise[0], enterprise[1])

            if run_id is not None:
                scope, mode, period = self._run_scope(conn, run_id, enterprise_id)
                if as_of is None:
                    run_as_of = conn.execute(
                        "SELECT as_of FROM assessment_run WHERE id = %s", (run_id,)
                    ).fetchone()
                    if run_as_of is not None:
                        moment = run_as_of[0].astimezone(SHANGHAI)
            elif policy_ids:
                scope, mode = self._explicit_scope(conn, policy_ids)
            else:
                scope, mode = self._profile_scope(conn)

            criteria = self._criteria(conn, [item.version_id for item in scope])
            facts = conn.execute(
                "SELECT key, period_start, period_end, verified, "
                "value_code, value_date, value_amount, value_boolean "
                "FROM enterprise_fact WHERE enterprise_id = %s",
                (enterprise_id,),
            ).fetchall()

        units = self._units(scope, criteria, facts, period=period, as_of=moment)
        manual_review = self._manual_review(scope, criteria)

        return {
            "enterpriseId": enterprise_id,
            "asOf": moment,
            "scope": {
                "mode": mode,
                "policyVersions": {
                    str(item.policy_id): item.version for item in sorted(scope, key=lambda i: str(i.policy_id))
                },
            },
            "definitionVersion": DEFINITION_VERSION,
            "applicabilityMode": APPLICABILITY_MODE,
            "units": [
                {
                    "factKey": unit.fact_key,
                    "periodStart": unit.start,
                    "periodEnd": unit.end,
                    "group": unit.group,
                    "status": unit.status,
                }
                for unit in units
            ],
            "groups": self._group_rows(units),
            "totals": self._totals(units),
            "manualReview": manual_review,
            "generatedAt": datetime.now(SHANGHAI),
        }

    # ------------------------------------------------------------------ scope

    def _profile_scope(self, conn) -> tuple[list[_ScopeVersion], str]:
        """画像页：当前已发布政策的最新版本，排除 closed 历史样本。"""

        rows = conn.execute(
            """
            WITH latest AS (
                SELECT DISTINCT ON (pv.policy_id)
                       pv.policy_id, pv.id, pv.version, pv.effective_to, pv.application_window
                FROM policy_version pv
                WHERE pv.review_status = 'published'
                ORDER BY pv.policy_id, pv.reviewed_at DESC NULLS LAST, pv.id DESC
            )
            SELECT policy_id, id, version FROM latest
            WHERE NOT (
                (effective_to IS NOT NULL
                 AND effective_to < (now() AT TIME ZONE 'Asia/Shanghai')::date)
                OR (application_window IS NOT NULL
                    AND NULLIF(application_window ->> 'closesAt', '')::timestamptz < now())
            )
            ORDER BY policy_id
            """
        ).fetchall()
        return (
            [_ScopeVersion(row[1], row[0], row[2]) for row in rows],
            "profile",
        )

    def _explicit_scope(self, conn, policy_ids: Sequence[UUID]) -> tuple[list[_ScopeVersion], str]:
        rows = conn.execute(
            """
            SELECT DISTINCT ON (pv.policy_id) pv.policy_id, pv.id, pv.version
            FROM policy_version pv
            WHERE pv.policy_id = ANY(%s) AND pv.review_status = 'published'
            ORDER BY pv.policy_id, pv.reviewed_at DESC NULLS LAST, pv.id DESC
            """,
            (list(policy_ids),),
        ).fetchall()
        found = {row[0] for row in rows}
        missing = [str(item) for item in policy_ids if item not in found]
        if missing:
            raise ApiProblem(
                422,
                "POLICY_NOT_PUBLISHED",
                "存在尚未审核发布的政策，不能统计完整度。",
                {"policyIds": missing},
            )
        return (
            [_ScopeVersion(row[1], row[0], row[2]) for row in rows],
            "explicit",
        )

    def _run_scope(
        self, conn, run_id: UUID, enterprise_id: UUID
    ) -> tuple[list[_ScopeVersion], str, tuple[date, date]]:
        """run 页：固定该 run 的政策版本集合；分子仍读当前事实。"""

        header = conn.execute(
            "SELECT enterprise_id, period_start, period_end FROM assessment_run WHERE id = %s",
            (run_id,),
        ).fetchone()
        if header is None:
            raise ApiProblem(404, "RUN_NOT_FOUND", "没有找到对应的判断运行。")
        if header[0] != enterprise_id:
            raise ApiProblem(
                422,
                "RUN_ENTERPRISE_MISMATCH",
                "该判断运行不属于指定企业。",
            )
        rows = conn.execute(
            """
            SELECT a.policy_id, pv.id, pv.version
            FROM assessment a JOIN policy_version pv ON pv.id = a.policy_version_id
            WHERE a.run_id = %s ORDER BY a.policy_id
            """,
            (run_id,),
        ).fetchall()
        return (
            [_ScopeVersion(row[1], row[0], row[2]) for row in rows],
            "run",
            (header[1], header[2]),
        )

    # -------------------------------------------------------------- 条件与事实

    def _criteria(self, conn, version_ids: Sequence[UUID]) -> list[tuple[Any, ...]]:
        if not version_ids:
            return []
        return conn.execute(
            """
            SELECT pv.policy_id, pv.version, c.criterion_id, c.title, c.expression
            FROM policy_criterion c JOIN policy_version pv ON pv.id = c.policy_version_id
            WHERE c.policy_version_id = ANY(%s)
            ORDER BY pv.policy_id, c.created_at, c.criterion_id
            """,
            (list(version_ids),),
        ).fetchall()

    def _units(
        self,
        scope: Sequence[_ScopeVersion],
        criteria: Sequence[Any],
        facts: Sequence[Any],
        *,
        period: tuple[date, date],
        as_of: datetime,
    ) -> list[_Unit]:
        rows = {
            (row[0], row[1], row[2]): (row[3], row[4], row[5], row[6], row[7])
            for row in facts
        }
        seen: set[tuple[str, date, date]] = set()
        units: list[_Unit] = []
        for _policy_id, _version, _criterion_id, _title, expression in criteria:
            for key, start, end in referenced_facts(expression, period=period):
                ref = (str(key), start, end)
                if ref in seen:
                    continue
                seen.add(ref)
                row = rows.get(ref)
                present = row is not None
                value_known = present and any(
                    value is not None for value in row[1:]
                )
                verified = bool(row[0]) if present else True
                units.append(
                    _Unit(
                        fact_key=ref[0],
                        start=start,
                        end=end,
                        status=unit_status(
                            due=is_due(
                                fact_key=ref[0], period_start=start, period_end=end, as_of=as_of
                            ),
                            present=present,
                            value_known=value_known,
                            verified=verified,
                        ),
                    )
                )
        units.sort(key=lambda item: (item.group, item.fact_key, item.start, item.end))
        return units

    def _manual_review(
        self, scope: Sequence[_ScopeVersion], criteria: Sequence[Any]
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for policy_id, version, criterion_id, title, expression in criteria:
            for reason in _manual_review_reasons(expression):
                items.append(
                    {
                        "policyId": policy_id,
                        "policyVersion": version,
                        "criterionId": criterion_id,
                        "title": title,
                        "reason": reason,
                    }
                )
        return items

    # ------------------------------------------------------------------ 汇总

    def _group_rows(self, units: Iterable[_Unit]) -> list[dict[str, Any]]:
        order = ("basic", "operation", "rd", "credit", "other")
        counts = {
            key: {"applicable": 0, "filled": 0} for key in order
        }
        for unit in units:
            bucket = counts[unit.group]
            if unit.status in (NOT_DUE, NOT_APPLICABLE, APPLICABILITY_UNKNOWN):
                continue
            bucket["applicable"] += 1
            if unit.filled:
                bucket["filled"] += 1
        rows = []
        for key in order:
            bucket = counts[key]
            if key == "other" and bucket["applicable"] == 0 and not any(
                unit.group == "other" for unit in units
            ):
                continue
            rows.append(
                {
                    "key": key,
                    "label": GROUP_LABELS[key],
                    "applicableCount": bucket["applicable"],
                    "filledCount": bucket["filled"],
                    "ratio": _ratio(bucket["filled"], bucket["applicable"]),
                }
            )
        return rows

    def _totals(self, units: Iterable[_Unit]) -> dict[str, Any]:
        counts = {
            FILLED: 0,
            FILLED_UNVERIFIED: 0,
            ANSWERED_UNKNOWN: 0,
            NOT_ANSWERED: 0,
            NOT_DUE: 0,
            NOT_APPLICABLE: 0,
            APPLICABILITY_UNKNOWN: 0,
        }
        for unit in units:
            counts[unit.status] += 1
        applicable = (
            counts[FILLED]
            + counts[FILLED_UNVERIFIED]
            + counts[ANSWERED_UNKNOWN]
            + counts[NOT_ANSWERED]
        )
        filled = counts[FILLED] + counts[FILLED_UNVERIFIED]
        return {
            "applicableCount": applicable,
            "filledCount": filled,
            "ratio": _ratio(filled, applicable),
            "answeredUnknownCount": counts[ANSWERED_UNKNOWN],
            "notDueCount": counts[NOT_DUE],
            "notApplicableCount": counts[NOT_APPLICABLE],
            "applicabilityUnknownCount": counts[APPLICABILITY_UNKNOWN],
        }


def _ratio(filled: int, applicable: int) -> float | None:
    if applicable == 0:
        return None
    return round(filled / applicable, 4)


def _manual_review_reasons(expression: Any) -> list[str]:
    reasons: list[str] = []
    _walk_manual_review(expression, reasons)
    return reasons


def _walk_manual_review(node: Any, reasons: list[str]) -> None:
    if isinstance(node, list):
        for item in node:
            _walk_manual_review(item, reasons)
        return
    if not isinstance(node, dict):
        return
    if node.get("op") == "manual_review":
        reason = node.get("reason")
        if isinstance(reason, str) and reason.strip():
            reasons.append(reason.strip())
        return
    for value in node.values():
        if isinstance(value, (dict, list)):
            _walk_manual_review(value, reasons)
