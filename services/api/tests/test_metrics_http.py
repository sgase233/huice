"""E3 统计端点的 HTTP 行为：鉴权、区间边界、缺测不出 0。

计算口径在 ``test_db_metrics.py`` / ``test_db_completeness.py``；这里把查询层
替换成固定返回值，只测路由层。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from app.analytics import completeness as completeness_module
from app.analytics import metrics as metrics_module
from app.analytics.metrics import NOT_COLLECTED, PARTIAL, PRECISE
from app.domain.versioning import SHANGHAI


def _day(**overrides):
    payload = {
        "date": "2026-11-20",
        "newDrafts": 2,
        "reviewsCompleted": 1,
        "pendingAtEndOfDay": None,
        "availability": {
            "newDrafts": {"state": PRECISE},
            "reviewsCompleted": {"state": PARTIAL, "coverageFrom": "2026-11-20T10:00:00+08:00"},
            "pendingAtEndOfDay": {"state": PRECISE},
        },
        "dayOpen": True,
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def reviewer_headers(auth) -> dict[str, str]:
    return auth


def test_review_workload_returns_per_metric_availability(
    client, reviewer_headers, monkeypatch
) -> None:
    monkeypatch.setattr(
        metrics_module.MetricsReader,
        "review_workload",
        lambda self, **kwargs: [_day()],
    )

    response = client.get(
        "/api/v1/admin/metrics/review-workload?from=2026-11-10&to=2026-11-10",
        headers=reviewer_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["from"] == "2026-11-10"
    assert body["timeZone"] == "Asia/Shanghai"
    assert body["days"][0]["reviewsCompleted"] == 1
    assert body["days"][0]["availability"]["reviewsCompleted"]["state"] == PARTIAL
    assert body["days"][0]["dayOpen"] is True


def test_not_collected_is_null_not_zero(client, reviewer_headers, monkeypatch) -> None:
    day = _day(
        reviewsCompleted=0,
        availability={
            "newDrafts": {"state": PRECISE},
            "reviewsCompleted": {"state": NOT_COLLECTED},
            "pendingAtEndOfDay": {"state": NOT_COLLECTED},
        },
    )
    monkeypatch.setattr(
        metrics_module.MetricsReader, "review_workload", lambda self, **kwargs: [day]
    )

    response = client.get(
        "/api/v1/admin/metrics/review-workload?from=2026-11-10&to=2026-11-10",
        headers=reviewer_headers,
    )

    body = response.json()
    assert body["days"][0]["reviewsCompleted"] is None
    assert body["days"][0]["pendingAtEndOfDay"] is None


def test_assessment_runs_exposes_unfinished_separately(
    client, reviewer_headers, monkeypatch
) -> None:
    monkeypatch.setattr(
        metrics_module.MetricsReader,
        "assessment_runs",
        lambda self, **kwargs: [
            {
                "date": "2026-11-20",
                "total": 3,
                "succeeded": 1,
                "failed": 1,
                "unfinished": 1,
                "durationSampleCount": 0,
                "avgDurationMs": None,
                "availability": {
                    name: {"state": PRECISE}
                    for name in (
                        "total",
                        "succeeded",
                        "failed",
                        "unfinished",
                        "durationSampleCount",
                        "avgDurationMs",
                    )
                },
                "dayOpen": False,
            }
        ],
    )

    response = client.get(
        "/api/v1/admin/metrics/assessment-runs?from=2026-11-10&to=2026-11-10",
        headers=reviewer_headers,
    )

    assert response.status_code == 200
    day = response.json()["days"][0]
    assert day["unfinished"] == 1
    assert day["failed"] == 1
    assert day["avgDurationMs"] is None


@pytest.mark.parametrize(
    "query",
    ["from=2026-11-11&to=2026-11-10", "from=2026-01-01&to=2026-03-01", "from=&to=2026-11-20"],
)
def test_invalid_ranges_are_rejected(client, reviewer_headers, query) -> None:
    response = client.get(f"/api/v1/admin/metrics/review-workload?{query}", headers=reviewer_headers)

    assert response.status_code in (400, 422)


def test_enterprise_session_cannot_read_metrics(client, user_headers) -> None:
    response = client.get(
        "/api/v1/admin/metrics/review-workload?from=2026-11-10&to=2026-11-10",
        headers=user_headers,
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ROLE_FORBIDDEN"


def test_bearer_can_read_metrics(anon_client, bearer, monkeypatch) -> None:
    monkeypatch.setattr(
        metrics_module.MetricsReader, "review_workload", lambda self, **kwargs: [_day()]
    )

    response = anon_client.get(
        "/api/v1/admin/metrics/review-workload?from=2026-11-10&to=2026-11-10",
        headers=bearer,
    )

    assert response.status_code == 200


def test_completeness_rejects_run_and_policy_ids_together(
    client, reviewer_headers
) -> None:
    response = client.get(
        f"/api/v1/admin/metrics/completeness?enterpriseId={uuid4()}"
        f"&runId={uuid4()}&policyIds={uuid4()}",
        headers=reviewer_headers,
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_SCOPE"


def test_completeness_returns_units_and_null_ratio(
    client, reviewer_headers, monkeypatch
) -> None:
    enterprise_id, policy_id = uuid4(), uuid4()

    def fake_build(self, **kwargs):
        assert kwargs["enterprise_id"] == enterprise_id
        return {
            "enterpriseId": enterprise_id,
            "asOf": datetime(2026, 11, 10, 12, 0, tzinfo=SHANGHAI),
            "scope": {"mode": "profile", "policyVersions": {str(policy_id): "v1"}},
            "definitionVersion": "d4.static.v1",
            "applicabilityMode": "static",
            "units": [],
            "groups": [],
            "totals": {
                "applicableCount": 0,
                "filledCount": 0,
                "ratio": None,
                "answeredUnknownCount": 0,
                "notDueCount": 0,
                "notApplicableCount": 0,
                "applicabilityUnknownCount": 0,
            },
            "manualReview": [],
            "generatedAt": datetime(2026, 11, 10, 12, 0, tzinfo=SHANGHAI),
        }

    monkeypatch.setattr(completeness_module.CompletenessReader, "build", fake_build)

    response = client.get(
        f"/api/v1/admin/metrics/completeness?enterpriseId={enterprise_id}",
        headers=reviewer_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["definitionVersion"] == "d4.static.v1"
    assert body["applicabilityMode"] == "static"
    assert body["totals"]["ratio"] is None


def test_future_dates_are_rejected(client, reviewer_headers) -> None:
    tomorrow = (datetime.now(SHANGHAI).date() + timedelta(days=1)).isoformat()

    response = client.get(
        f"/api/v1/admin/metrics/review-workload?from={tomorrow}&to={tomorrow}",
        headers=reviewer_headers,
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "FUTURE_RANGE"
