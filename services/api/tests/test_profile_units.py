"""画像归一化与 ``factVersion`` 的单元口径（实现约定 2.3）。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.agents.profile import (
    NormalizedFact,
    fact_value_text,
    fact_version_of,
    normalize_profile,
    to_wire_fact,
)
from app.errors import ApiProblem
from app.schemas import EnterpriseInput

YEAR = {"startDate": "2026-01-01", "endDate": "2026-12-31"}


def payload(**overrides) -> EnterpriseInput:
    body = {
        "nameSynthetic": "合成企业A",
        "period": YEAR,
        "facts": [
            {"key": "region", "value": "440300", "unit": "code", "period": YEAR, "source": "user_input"},
            {"key": "industry", "value": "C39", "unit": "code", "period": YEAR, "source": "user_input"},
        ],
    }
    body.update(overrides)
    return EnterpriseInput.model_validate(body)


def fact(key: str, unit: str, value, period=("2026-01-01", "2026-12-31")) -> NormalizedFact:
    return NormalizedFact(
        key=key,
        unit=unit,
        period_start=date.fromisoformat(period[0]),
        period_end=date.fromisoformat(period[1]),
        source="user_input",
        value=value,
    )


# ------------------------------------------------------------- 线上数值形态


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (None, None),
        (True, True),
        (False, False),
        (date(2026, 5, 1), "2026-05-01"),
        ("440300", "440300"),
        (Decimal("2800000"), "2800000.00"),
        (Decimal("2800000.5"), "2800000.50"),
        (Decimal("0"), "0.00"),
        (Decimal("-1234.5"), "-1234.50"),
    ],
)
def test_money_is_always_written_with_two_decimals_on_the_wire(value, text):
    assert fact_value_text(value, unit="CNY") == text


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (Decimal("120"), "120"),
        (Decimal("120.0"), "120"),
        (Decimal("120.50"), "120.5"),
        (0, "0"),
    ],
)
def test_non_money_numbers_drop_trailing_zeros(value, text):
    assert fact_value_text(value, unit="person") == text


def test_unknown_stays_null_and_never_becomes_zero():
    wire = to_wire_fact(fact("taxableIncome", "CNY", None))

    assert wire.value is None
    assert wire.verified is False


# ----------------------------------------------------------- 版本哈希口径


def test_version_is_independent_of_fact_order():
    facts = [
        fact("region", "code", "440300"),
        fact("taxableIncome", "CNY", Decimal("2800000")),
    ]

    assert fact_version_of(facts) == fact_version_of(list(reversed(facts)))


def test_version_ignores_trailing_zeros():
    # 内容相同即同一版本：2800000 与 2800000.00 是同一个数。
    assert fact_version_of([fact("taxableIncome", "CNY", Decimal("2800000"))]) == fact_version_of(
        [fact("taxableIncome", "CNY", Decimal("2800000.00"))]
    )


def test_version_distinguishes_unknown_from_zero():
    unknown = fact_version_of([fact("taxableIncome", "CNY", None)])
    zero = fact_version_of([fact("taxableIncome", "CNY", Decimal("0"))])

    assert unknown != zero


def test_version_covers_unit_source_and_period():
    base = fact("taxableIncome", "CNY", Decimal("1"))
    assert fact_version_of([base]) != fact_version_of([fact("taxableIncome", "person", Decimal("1"))])
    assert fact_version_of([base]) != fact_version_of(
        [fact("taxableIncome", "CNY", Decimal("1"), period=("2026-01-01", "2026-06-30"))]
    )


# --------------------------------------------------------------- 归一化


def test_profile_is_stored_in_a_stable_order_with_a_stable_version():
    body = payload(
        facts=[
            {"key": "industry", "value": "C39", "unit": "code", "period": YEAR, "source": "user_input"},
            {"key": "region", "value": "440300", "unit": "code", "period": YEAR, "source": "user_input"},
            {
                "key": "taxableIncome",
                "value": "2800000.00",
                "unit": "CNY",
                "period": YEAR,
                "source": "user_input",
            },
        ]
    )

    first = normalize_profile(body)
    second = normalize_profile(body.model_copy(deep=True))

    assert [item.key for item in first.facts] == ["industry", "region", "taxableIncome"]
    assert first.fact_version == second.fact_version


def test_normalized_money_keeps_only_the_significant_digits():
    profile = normalize_profile(
        payload(
            facts=[
                {"key": "region", "value": "440300", "unit": "code", "period": YEAR, "source": "user_input"},
                {"key": "industry", "value": "C39", "unit": "code", "period": YEAR, "source": "user_input"},
                {
                    "key": "taxableIncome",
                    "value": "2800000.00",
                    "unit": "CNY",
                    "period": YEAR,
                    "source": "user_input",
                },
            ]
        )
    )

    income = next(item for item in profile.facts if item.key == "taxableIncome")
    assert isinstance(income.value, Decimal)
    assert income.value == Decimal("2800000")


def test_negative_taxable_income_is_allowed_but_other_amounts_are_not():
    assert normalize_profile(
        payload(
            facts=[
                {"key": "region", "value": "440300", "unit": "code", "period": YEAR, "source": "user_input"},
                {"key": "industry", "value": "C39", "unit": "code", "period": YEAR, "source": "user_input"},
                {
                    "key": "taxableIncome",
                    "value": "-1200",
                    "unit": "CNY",
                    "period": YEAR,
                    "source": "user_input",
                },
            ]
        )
    )

    with pytest.raises(ApiProblem) as rejected:
        normalize_profile(
            payload(
                facts=[
                    {"key": "region", "value": "440300", "unit": "code", "period": YEAR, "source": "user_input"},
                    {"key": "industry", "value": "C39", "unit": "code", "period": YEAR, "source": "user_input"},
                    {"key": "rdExpense", "value": "-1", "unit": "CNY", "period": YEAR, "source": "user_input"},
                ]
            )
        )
    assert rejected.value.status_code == 422
    assert any(item["field"] == "facts[2].value" for item in rejected.value.details["fieldErrors"])


def test_money_rejects_more_than_two_decimals_but_accepts_them_after_the_point():
    with pytest.raises(ApiProblem) as rejected:
        normalize_profile(
            payload(
                facts=[
                    {"key": "region", "value": "440300", "unit": "code", "period": YEAR, "source": "user_input"},
                    {"key": "industry", "value": "C39", "unit": "code", "period": YEAR, "source": "user_input"},
                    {
                        "key": "taxableIncome",
                        "value": "1234.567",
                        "unit": "CNY",
                        "period": YEAR,
                        "source": "user_input",
                    },
                ]
            )
        )

    assert any("最多 2 位小数" in item["message"] for item in rejected.value.details["fieldErrors"])
