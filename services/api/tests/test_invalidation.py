"""失效与有效性视图（FR-07）。

历史 run 只读：``validity``/``staleReasons``/``validUntil`` 都要按失效事件与读取时刻
重算。这里直接验证 :mod:`app.domain.versioning` 的口径：

* 事件只影响**真正读过该事实**的政策，没读过的政策必须保持 ``current``；
* 只保留**未来**边界作为 ``validUntil``，已过的边界改为 stale 原因；
* run 创建之前发生的事件不追溯。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

from app.domain import versioning

SHANGHAI = versioning.SHANGHAI
NOW = datetime(2026, 10, 26, 12, 0, tzinfo=SHANGHAI)
RUN_CREATED = NOW - timedelta(hours=1)


def event(reason: str, *, at: datetime | None = None, policy_id=None, fact_key=None):
    return versioning.ChangedEvent(
        reason=reason,
        occurred_at=at or NOW - timedelta(minutes=30),
        policy_id=policy_id,
        fact_key=fact_key,
    )


def test_shanghai_date_converts_utc_evening_to_next_day():
    assert versioning.shanghai_date(datetime(2026, 10, 22, 16, 30, tzinfo=timezone.utc)) == date(
        2026, 10, 23
    )


def test_valid_until_takes_the_earliest_future_boundary():
    boundary = versioning.PolicyBoundary(
        effective_to=date(2026, 12, 31),
        window_closes_at=datetime(2026, 11, 15, 18, 0, tzinfo=SHANGHAI),
    )

    assert versioning.valid_until(boundary=boundary, now=NOW) == datetime(
        2026, 11, 15, 18, 0, tzinfo=SHANGHAI
    )


def test_valid_until_ignores_boundaries_already_passed():
    boundary = versioning.PolicyBoundary(
        effective_to=date(2026, 10, 1),
        window_closes_at=datetime(2026, 10, 20, 18, 0, tzinfo=SHANGHAI),
    )

    assert versioning.valid_until(boundary=boundary, now=NOW) is None


def test_no_boundary_means_no_deadline():
    assert versioning.valid_until(boundary=versioning.PolicyBoundary(), now=NOW) is None


def test_fact_change_only_marks_criteria_that_read_that_fact():
    criterion_a = uuid4()
    criterion_b = uuid4()

    view = versioning.resolve_validity(
        boundary=versioning.PolicyBoundary(),
        events=[event("fact_changed", fact_key="taxableIncome", at=NOW - timedelta(minutes=5))],
        criterion_dependencies={
            criterion_a: [("taxableIncome", date(2026, 1, 1), date(2026, 12, 31))],
            criterion_b: [("employeeCountStart", date(2026, 1, 1), date(2026, 3, 31))],
        },
        run_created_at=RUN_CREATED,
        now=NOW,
    )
    assert view.validity == versioning.STALE
    codes = [item["code"] for item in view.stale_reasons]
    assert codes == ["fact_changed"]
    reason = view.stale_reasons[0]
    assert reason["changedCriterionIds"] == [str(criterion_a)]
    assert reason["changedFactRefs"] == [
        {
            "key": "taxableIncome",
            "period": {"startDate": "2026-01-01", "endDate": "2026-12-31"},
        }
    ]
    # 只读过员工人数的那条条件不在 changedCriterionIds 里：事实改动不按整企业失效。
    assert str(criterion_b) not in reason["changedCriterionIds"]


def test_policy_version_change_marks_stale():
    view = versioning.resolve_validity(
        boundary=versioning.PolicyBoundary(),
        events=[event("policy_version_changed")],
        criterion_dependencies={},
        run_created_at=RUN_CREATED,
        now=NOW,
    )

    assert view.validity == versioning.STALE
    assert [item["code"] for item in view.stale_reasons] == ["policy_changed"]


def test_events_before_the_run_are_not_retroactive():
    view = versioning.resolve_validity(
        boundary=versioning.PolicyBoundary(),
        events=[event("fact_changed", fact_key="taxableIncome", at=RUN_CREATED - timedelta(seconds=1))],
        criterion_dependencies={uuid4(): [("taxableIncome", date(2026, 1, 1), date(2026, 12, 31))]},
        run_created_at=RUN_CREATED,
        now=NOW,
    )

    assert view.validity == versioning.CURRENT
    assert view.stale_reasons == []


def test_closed_window_and_expired_policy_are_stale():
    view = versioning.resolve_validity(
        boundary=versioning.PolicyBoundary(
            effective_to=date(2026, 10, 1),
            window_closes_at=datetime(2026, 10, 20, 18, 0, tzinfo=SHANGHAI),
        ),
        events=[],
        criterion_dependencies={},
        run_created_at=RUN_CREATED,
        now=NOW,
    )

    assert view.validity == versioning.STALE
    assert [item["code"] for item in view.stale_reasons] == ["window_closed", "policy_expired"]


def test_valid_until_passed_is_reported():
    passed = NOW - timedelta(minutes=1)
    view = versioning.resolve_validity(
        boundary=versioning.PolicyBoundary(),
        events=[],
        criterion_dependencies={},
        run_created_at=RUN_CREATED,
        now=NOW,
        valid_until_at=passed,
    )

    assert [item["code"] for item in view.stale_reasons] == ["valid_until_passed"]


def test_history_payload_matches_the_contract_shape():
    """staleReasons 的字段名必须与契约一致，否则读取 run 时会 500。"""

    ref = ("taxableIncome", date(2026, 1, 1), date(2026, 12, 31))
    payload = versioning.fact_ref_json(ref)

    assert payload == {
        "key": "taxableIncome",
        "period": {"startDate": "2026-01-01", "endDate": "2026-12-31"},
    }
