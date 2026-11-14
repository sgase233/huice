"""D4 完整度的纯函数：七状态、到期判定（方案 C）与分母为 0。

这里不连库，锁的是 COMPLETENESS_SPEC §3.1/§3.1.1 的边界样例 N1—N9 与 §1 的
``ratio=null``；读库部分见 ``test_db_completeness.py``。
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.analytics.completeness import (
    ANSWERED_UNKNOWN,
    FILLED,
    FILLED_UNVERIFIED,
    NOT_ANSWERED,
    NOT_DUE,
    is_due,
    unit_status,
)
from app.domain.versioning import SHANGHAI

Q1 = (date(2026, 1, 1), date(2026, 3, 31))
Q4 = (date(2026, 10, 1), date(2026, 12, 31))
YEAR_2026 = (date(2026, 1, 1), date(2026, 12, 31))
TWO_YEARS = (date(2027, 1, 1), date(2028, 12, 31))


def _at(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=SHANGHAI)


@pytest.mark.parametrize(
    ("as_of", "fact_key", "period", "expected"),
    [
        # N1：Q1 已在 3-31 结束，4-1 00:00 起到期
        ("2026-04-01T00:00:00", "employeeCountStart", Q1, True),
        # N2：季度结束日当天仍未到期
        ("2026-03-31T23:59:59.999999", "employeeCountEnd", Q1, False),
        # N3：未来季度
        ("2026-11-10T00:00:00", "assetsEnd", Q4, False),
        # N4：当前年度算到期
        ("2026-11-10T00:00:00", "taxableIncome", YEAR_2026, True),
        # N5：年度尚未开始
        ("2025-12-31T23:59:59.999999", "taxableIncome", YEAR_2026, False),
        # N7：跨年区间未来
        ("2026-11-10T00:00:00", "rdAddDeductionClaimed", TWO_YEARS, False),
        # N8：季度内期初值也不提前入分母
        ("2026-11-10T00:00:00", "employeeCountStart", Q4, False),
        # N9：季度结束后补计
        ("2027-01-01T00:00:00", "employeeCountStart", Q4, True),
    ],
)
def test_scheme_c_not_due_boundaries(as_of, fact_key, period, expected) -> None:
    assert (
        is_due(fact_key=fact_key, period_start=period[0], period_end=period[1], as_of=_at(as_of))
        is expected
    )


def test_the_as_of_instant_is_read_in_shanghai_time() -> None:
    # 2026-03-31T16:00Z 就是北京时间 4-1 00:00：Q1 此时到期。
    instant = datetime(2026, 3, 31, 16, 0, tzinfo=timezone.utc)
    assert is_due(fact_key="assetsEnd", period_start=Q1[0], period_end=Q1[1], as_of=instant)


def test_not_due_wins_over_a_prefilled_value() -> None:
    assert unit_status(due=False, present=True, value_known=True, verified=True) == NOT_DUE


def test_statuses_distinguish_answered_unknown_from_missing() -> None:
    assert unit_status(due=True, present=False, value_known=False, verified=True) == NOT_ANSWERED
    assert unit_status(due=True, present=True, value_known=False, verified=True) == ANSWERED_UNKNOWN
    assert unit_status(due=True, present=True, value_known=True, verified=False) == FILLED_UNVERIFIED
    assert unit_status(due=True, present=True, value_known=True, verified=True) == FILLED
