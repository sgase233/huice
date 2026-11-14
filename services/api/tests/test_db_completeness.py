"""D4 完整度的数据库用例（``app/analytics/completeness.py``）。

锁住：单位按 ``(FactKey, period)`` 展开、`answered_unknown` 与 `not_answered` 可区分、
`not_due` 优先于已填、`verified=false` 记 `filled_unverified`、手动核查单列且不进分母、
分母为 0 时 `ratio=null`，以及 run 范围固定该 run 的版本集合。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from app.analytics.completeness import (
    ANSWERED_UNKNOWN,
    CompletenessReader,
    FILLED,
    FILLED_UNVERIFIED,
    NOT_ANSWERED,
    NOT_DUE,
)
from app.db import Database
from app.domain.versioning import SHANGHAI

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("clean_tables")]

AS_OF = datetime(2026, 11, 10, 12, 0, tzinfo=SHANGHAI)
PROFILE = (date(2026, 1, 1), date(2026, 12, 31))


def _reader(dsn: str) -> CompletenessReader:
    return CompletenessReader(Database(dsn))


def _enterprise(conn) -> UUID:
    enterprise_id = uuid4()
    conn.execute(
        "INSERT INTO enterprise (id, name_synthetic, period_start, period_end) "
        "VALUES (%s, '完整度合成企业', '2026-01-01', '2026-12-31')",
        (enterprise_id,),
    )
    return enterprise_id


def _published_version(
    conn, expressions: list[dict], *, policy_id=None, version: str | None = None
) -> tuple[UUID, UUID, str]:
    policy_id = policy_id or uuid4()
    version_id = uuid4()
    name = version or f"v-{version_id.hex[:8]}"
    if policy_id is not None:
        conn.execute(
            "INSERT INTO policy (id) VALUES (%s) ON CONFLICT (id) DO NOTHING", (policy_id,)
        )
    conn.execute(
        "INSERT INTO policy_version (id, policy_id, version, title, policy_type, issuer, region, "
        "applicable_entities, published_at, effective_from, source_url, official_entry_url, "
        "review_status, revision) VALUES (%s, %s, %s, '完整度合成政策', 'grant', '合成部门', "
        "'440300', %s, '2026-01-01', '2026-01-01', 'https://example.gov.cn/c.html', "
        "'https://example.gov.cn/c.html', 'pending_review', %s)",
        (version_id, policy_id, name, Jsonb(["企业"]), uuid4()),
    )
    for index, expression in enumerate(expressions):
        conn.execute(
            "INSERT INTO policy_criterion (policy_version_id, criterion_id, title, expression, "
            "review_finding, validation_status) VALUES (%s, %s, %s, %s, 'none', 'passed')",
            (version_id, uuid4(), f"条件 {index + 1}", Jsonb(expression)),
        )
    conn.execute(
        "UPDATE policy_version SET review_status = 'published', reviewed_at = now() "
        "WHERE id = %s",
        (version_id,),
    )
    return policy_id, version_id, name


def _fact(conn, enterprise_id, *, key, start, end, unit, verified=True, **values) -> None:
    conn.execute(
        "INSERT INTO enterprise_fact (enterprise_id, key, period_start, period_end, unit, "
        "source, verified, value_code, value_date, value_amount, value_boolean) "
        "VALUES (%s, %s, %s, %s, %s, 'user_input', %s, %s, %s, %s, %s)",
        (
            enterprise_id,
            key,
            start,
            end,
            unit,
            verified,
            values.get("code"),
            values.get("day"),
            values.get("amount"),
            values.get("boolean"),
        ),
    )


def _build(dsn: str, enterprise_id, **kwargs):
    return _reader(dsn).build(enterprise_id=enterprise_id, as_of=AS_OF, **kwargs)


def test_units_come_from_referenced_facts_and_carry_seven_states(database_url_schema: str) -> None:
    expressions = [
        {
            "op": "compare",
            "factKey": "taxableIncome",
            "period": "assessment",
            "comparator": "lte",
            "value": "3000000",
            "unit": "CNY",
        },
        {
            "op": "compare",
            "factKey": "assetsStart",
            "period": {"startDate": "2026-01-01", "endDate": "2026-03-31"},
            "comparator": "gte",
            "value": "1",
            "unit": "CNY",
        },
        {
            "op": "compare",
            "factKey": "assetsStart",
            "period": {"startDate": "2026-10-01", "endDate": "2026-12-31"},
            "comparator": "gte",
            "value": "1",
            "unit": "CNY",
        },
        {"op": "manual_review", "reason": "需要人工核对行业分类。"},
    ]
    with psycopg.connect(database_url_schema) as conn:
        enterprise_id = _enterprise(conn)
        _published_version(conn, expressions)
        # 已填：年度应纳税所得额
        _fact(
            conn,
            enterprise_id,
            key="taxableIncome",
            start=PROFILE[0],
            end=PROFILE[1],
            unit="CNY",
            amount=Decimal("100"),
        )
        # Q1 资产未填 → not_answered
        conn.commit()

    payload = _build(database_url_schema, enterprise_id)
    units = {
        (unit["factKey"], unit["periodStart"]): unit["status"] for unit in payload["units"]
    }

    assert units[("taxableIncome", date(2026, 1, 1))] == FILLED
    assert units[("assetsStart", date(2026, 1, 1))] == NOT_ANSWERED
    # 2026Q4 在 asOf 时尚未结束：not_due，即使没有值也不进分母
    assert units[("assetsStart", date(2026, 10, 1))] == NOT_DUE

    totals = payload["totals"]
    assert totals["applicableCount"] == 2
    assert totals["filledCount"] == 1
    assert totals["ratio"] == 0.5
    assert totals["notDueCount"] == 1
    # manual_review 不进分母，单独列出
    assert [item["reason"] for item in payload["manualReview"]] == ["需要人工核对行业分类。"]
    assert [item["key"] for item in payload["groups"]][:2] == ["basic", "operation"]


def test_answered_unknown_and_unverified_are_distinct(database_url_schema: str) -> None:
    expressions = [
        {
            "op": "compare",
            "factKey": "rdAddDeductionClaimed",
            "period": "assessment",
            "comparator": "eq",
            "value": "true",
            "unit": "boolean",
        },
        {
            "op": "compare",
            "factKey": "independentLegalEntity",
            "period": "assessment",
            "comparator": "eq",
            "value": "true",
            "unit": "boolean",
        },
    ]
    with psycopg.connect(database_url_schema) as conn:
        enterprise_id = _enterprise(conn)
        _published_version(conn, expressions)
        # 行存在但值为 null：已答未知，不是「未填写」，也不是 false
        _fact(
            conn,
            enterprise_id,
            key="rdAddDeductionClaimed",
            start=PROFILE[0],
            end=PROFILE[1],
            unit="boolean",
        )
        # 有值但 verified=false：已填写（未核实）
        _fact(
            conn,
            enterprise_id,
            key="independentLegalEntity",
            start=PROFILE[0],
            end=PROFILE[1],
            unit="boolean",
            verified=False,
            boolean=True,
        )
        conn.commit()

    payload = _build(database_url_schema, enterprise_id)
    statuses = {unit["factKey"]: unit["status"] for unit in payload["units"]}

    assert statuses["rdAddDeductionClaimed"] == ANSWERED_UNKNOWN
    assert statuses["independentLegalEntity"] == FILLED_UNVERIFIED
    # 已答未知不算已填写；分母仍含它
    assert payload["totals"]["applicableCount"] == 2
    assert payload["totals"]["filledCount"] == 1
    assert payload["totals"]["answeredUnknownCount"] == 1


def test_repeated_manual_review_reason_is_listed_once(database_url_schema: str) -> None:
    expression = {
        "op": "or",
        "children": [
            {"op": "manual_review", "reason": "同一句人工核查说明。"},
            {"op": "manual_review", "reason": "同一句人工核查说明。"},
        ],
    }
    with psycopg.connect(database_url_schema) as conn:
        enterprise_id = _enterprise(conn)
        _published_version(conn, [expression])
        conn.commit()

    payload = _build(database_url_schema, enterprise_id)

    assert [item["reason"] for item in payload["manualReview"]] == ["同一句人工核查说明。"]


def test_an_empty_scope_has_ratio_null(database_url_schema: str) -> None:
    with psycopg.connect(database_url_schema) as conn:
        enterprise_id = _enterprise(conn)
        _published_version(conn, [{"op": "manual_review", "reason": "只看人工。"}])
        conn.commit()

    payload = _build(database_url_schema, enterprise_id)

    assert payload["units"] == []
    assert payload["totals"]["applicableCount"] == 0
    assert payload["totals"]["ratio"] is None, "分母为 0 时必须是 null，不是 0"
    assert payload["manualReview"]


def test_a_run_scope_pins_its_policy_versions(database_url_schema: str) -> None:
    expressions = [
        {
            "op": "compare",
            "factKey": "taxableIncome",
            "period": "assessment",
            "comparator": "lte",
            "value": "3000000",
            "unit": "CNY",
        }
    ]
    with psycopg.connect(database_url_schema) as conn:
        enterprise_id = _enterprise(conn)
        policy_id, v1, _ = _published_version(conn, expressions, version="v1")
        # 同一政策再发 v2：旧 run 仍应钉住 v1
        _, _, v2_name = _published_version(conn, expressions, policy_id=policy_id, version="v2")

        run_id = uuid4()
        conn.execute(
            "INSERT INTO assessment_run (id, enterprise_id, as_of, status, fact_version, "
            "model_or_rule_version, created_at, finished_at, period_start, period_end) "
            "VALUES (%s, %s, %s, 'succeeded', %s, 'rule:1', now(), now(), "
            "'2026-01-01', '2026-12-31')",
            (run_id, enterprise_id, AS_OF, "f" * 64),
        )
        conn.execute(
            "INSERT INTO assessment (run_id, policy_id, policy_version_id, result, snapshot) "
            "VALUES (%s, %s, %s, 'satisfied', %s)",
            (run_id, policy_id, v1, Jsonb({})),
        )
        conn.commit()

    payload = _build(database_url_schema, enterprise_id, run_id=run_id)

    assert payload["scope"]["mode"] == "run"
    # run 钉住的是 v1，不是后来发布的 v2
    assert payload["scope"]["policyVersions"] == {str(policy_id): "v1"}
    assert v2_name == "v2"
    assert payload["definitionVersion"] == "d4.static.v1"
    assert payload["applicabilityMode"] == "static"
