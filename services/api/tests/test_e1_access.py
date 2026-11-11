"""对象归属、两个列表接口、CSRF/Origin 与 demo 配额（AUTH-06/07 与 #55 draft.4）。

所有越权断言都要求**与对象不存在完全相同的响应**：同一个码、同一段文案、同一个 404。
差别一旦可观测，就成了「他人是否存在这个对象」的探针。
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from uuid import UUID

from app import auth as auth_tools
from app.security import DEMO_SESSION_COOKIE, issue_demo_session
from support import DEMO_SESSION_SECRET, published_draft

ORIGIN = "http://127.0.0.1:5173"
YEAR = {"startDate": "2026-01-01", "endDate": "2026-12-31"}
AS_OF = "2026-10-23T10:00:00+08:00"

INDUSTRY_GATE = {
    "op": "industry_exclusion",
    "factKey": "industry",
    "period": "assessment",
    "excluded": ["F51", "F52"],
}

uuid_parse = UUID

ENTERPRISE_BODY = {
    "nameSynthetic": "合成企业A",
    "period": YEAR,
    "facts": [
        {"key": "region", "value": "440300", "unit": "code", "period": YEAR, "source": "user_input"},
        {"key": "industry", "value": "F51", "unit": "code", "period": YEAR, "source": "user_input"},
    ],
}


def as_user(app, accounts, username: str, token: str) -> TestClient:
    accounts.seed_user(username)
    accounts.seed_session(username, token=token)
    client = TestClient(
        app,
        headers={"Origin": ORIGIN, "X-CSRF-Token": auth_tools.csrf_token_for(token)},
    )
    client.cookies.set("huice_session", token, path="/api")
    return client


def as_reviewer(app, accounts, username: str = "reviewer.one", token: str = "rev-token-0001"):
    accounts.seed_user(username, role="reviewer")
    accounts.seed_session(username, token=token)
    client = TestClient(
        app,
        headers={"Origin": ORIGIN, "X-CSRF-Token": auth_tools.csrf_token_for(token)},
    )
    client.cookies.set("huice_session", token, path="/api")
    return client


def as_demo(app, *, sid: str | None = "demo-sid-0001", hours: float = 1.0) -> TestClient:
    client = TestClient(app, headers={"Origin": ORIGIN})
    value = issue_demo_session(
        DEMO_SESSION_SECRET, expires_at=int(time.time() + hours * 3600), sid=sid
    )
    client.cookies.set(DEMO_SESSION_COOKIE, value, path="/api")
    return client


def make_enterprise(client: TestClient) -> dict:
    response = client.post("/api/v1/enterprises", json=ENTERPRISE_BODY)
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------- 企业归属


def test_another_enterprise_account_gets_the_same_404_as_a_missing_one(app, accounts):
    owner = as_user(app, accounts, "enterprise.one", "token-one")
    stranger = as_user(app, accounts, "enterprise.two", "token-two")
    enterprise = make_enterprise(owner)

    hidden = stranger.get(f"/api/v1/enterprises/{enterprise['id']}")
    missing = stranger.get("/api/v1/enterprises/11111111-1111-4111-8111-111111111111")

    assert hidden.status_code == missing.status_code == 404
    assert hidden.json()["error"] == missing.json()["error"]


def test_another_enterprise_account_cannot_patch_facts(app, accounts):
    owner = as_user(app, accounts, "enterprise.one", "token-one")
    stranger = as_user(app, accounts, "enterprise.two", "token-two")
    enterprise = make_enterprise(owner)

    response = stranger.patch(
        f"/api/v1/enterprises/{enterprise['id']}/facts/industry",
        json={"value": "F52", "unit": "code", "period": YEAR, "source": "user_input"},
        headers={"If-Match": f'"{enterprise["factVersion"]}"'},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ENTERPRISE_NOT_FOUND"


def test_reviewer_reads_any_enterprise_but_only_writes_their_own(app, accounts):
    owner = as_user(app, accounts, "enterprise.one", "token-one")
    reviewer = as_reviewer(app, accounts)
    enterprise = make_enterprise(owner)

    assert reviewer.get(f"/api/v1/enterprises/{enterprise['id']}").status_code == 200

    write = reviewer.patch(
        f"/api/v1/enterprises/{enterprise['id']}/facts/industry",
        json={"value": "F52", "unit": "code", "period": YEAR, "source": "user_input"},
        headers={"If-Match": f'"{enterprise["factVersion"]}"'},
    )
    assert write.status_code == 404


def test_bearer_cannot_read_the_business_api(app, accounts, bearer):
    owner = as_user(app, accounts, "enterprise.one", "token-one")
    enterprise = make_enterprise(owner)

    # 新客户端只带 Bearer、不带会话 Cookie：Bearer 是有效凭证，但角色不够。
    response = TestClient(app).get(
        f"/api/v1/enterprises/{enterprise['id']}", headers=bearer
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ROLE_FORBIDDEN"


# ------------------------------------------------------------ CSRF / Origin


def test_session_write_without_csrf_is_rejected(app, accounts):
    owner = as_user(app, accounts, "enterprise.one", "token-one")
    enterprise = make_enterprise(owner)

    response = owner.patch(
        f"/api/v1/enterprises/{enterprise['id']}/facts/industry",
        json={"value": "F52", "unit": "code", "period": YEAR, "source": "user_input"},
        headers={"If-Match": f'"{enterprise["factVersion"]}"', "X-CSRF-Token": ""},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_REQUIRED"


def test_origin_is_checked_before_csrf(app, accounts):
    """两样都错时先报 ORIGIN_MISMATCH：不同源说明请求根本不该到达这里。"""

    owner = as_user(app, accounts, "enterprise.one", "token-one")
    enterprise = make_enterprise(owner)

    response = owner.patch(
        f"/api/v1/enterprises/{enterprise['id']}/facts/industry",
        json={"value": "F52", "unit": "code", "period": YEAR, "source": "user_input"},
        headers={
            "If-Match": f'"{enterprise["factVersion"]}"',
            "Origin": "https://evil.example",
            "X-CSRF-Token": "",
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ORIGIN_MISMATCH"


def test_a_second_enterprise_for_the_same_account_is_a_409(app, accounts):
    owner = as_user(app, accounts, "enterprise.one", "token-one")
    make_enterprise(owner)

    response = owner.post("/api/v1/enterprises", json=ENTERPRISE_BODY)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ENTERPRISE_ALREADY_BOUND"


# ----------------------------------------------------------------- demo 身份


def test_demo_creates_an_unowned_sample_and_consumes_one_quota(app, accounts):
    demo = as_demo(app)

    enterprise = make_enterprise(demo)

    owners = {str(key): value for key, value in app.state.repository.enterprise_owners.items()}
    assert owners[enterprise["id"]] is None
    assert accounts.quota[auth_tools.digest("demo-sid-0001")] == 1


def test_demo_cannot_see_owned_enterprises(app, accounts):
    owner = as_user(app, accounts, "enterprise.one", "token-one")
    enterprise = make_enterprise(owner)
    demo = as_demo(app)

    response = demo.get(f"/api/v1/enterprises/{enterprise['id']}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ENTERPRISE_NOT_FOUND"


def test_legacy_demo_cookie_cannot_create_samples(app):
    legacy = as_demo(app, sid=None)

    response = legacy.post("/api/v1/enterprises", json=ENTERPRISE_BODY)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "DEMO_SESSION_LEGACY"


def test_demo_quota_is_enforced_per_session(app, accounts, settings):
    demo = as_demo(app)
    sid_hash = auth_tools.digest("demo-sid-0001")
    accounts.quota[sid_hash] = settings.demo_session_quota

    response = demo.post("/api/v1/enterprises", json=ENTERPRISE_BODY)

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "DEMO_QUOTA_EXCEEDED"
    assert response.json()["error"]["details"]["limit"] == settings.demo_session_quota


# ------------------------------------------------------------------ 列表


def test_enterprise_list_only_shows_own_rows(app, accounts):
    first = as_user(app, accounts, "enterprise.one", "token-one")
    second = as_user(app, accounts, "enterprise.two", "token-two")
    mine = make_enterprise(first)
    make_enterprise(second)

    response = first.get("/api/v1/enterprises")

    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["id"] for item in items] == [mine["id"]]
    assert items[0]["ownership"] == "self"


def test_reviewer_list_shows_everything_with_relative_ownership(app, accounts):
    owner = as_user(app, accounts, "enterprise.one", "token-one")
    make_enterprise(owner)
    reviewer = as_reviewer(app, accounts)

    response = reviewer.get("/api/v1/enterprises")

    assert response.status_code == 200
    assert [item["ownership"] for item in response.json()["items"]] == ["other"]


def test_demo_list_shows_only_unowned_samples(app, accounts):
    owner = as_user(app, accounts, "enterprise.one", "token-one")
    make_enterprise(owner)
    demo = as_demo(app)
    sample = make_enterprise(demo)

    response = demo.get("/api/v1/enterprises")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [sample["id"]]
    assert response.json()["items"][0]["ownership"] == "unowned"


def test_bearer_cannot_list_business_objects(app, bearer):
    response = TestClient(app).get("/api/v1/enterprises", headers=bearer)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ROLE_FORBIDDEN"


def test_run_list_filters_by_enterprise_only_within_the_visible_set(app, accounts):
    first = as_user(app, accounts, "enterprise.one", "token-one")
    second = as_user(app, accounts, "enterprise.two", "token-two")
    mine = make_enterprise(first)
    theirs = make_enterprise(second)

    allowed = first.get(f"/api/v1/assessment-runs?enterpriseId={mine['id']}")
    hidden = first.get(f"/api/v1/assessment-runs?enterpriseId={theirs['id']}")

    assert allowed.status_code == 200
    assert allowed.json()["items"] == []
    assert hidden.status_code == 404
    assert hidden.json()["error"]["code"] == "ENTERPRISE_NOT_FOUND"


# ------------------------------------------------------------- run 与报告


def _make_run(client: TestClient, repository, enterprise_id: str) -> dict:
    policy = published_draft(expressions=[INDUSTRY_GATE])
    repository.drafts.append(policy)
    body = {
        "enterpriseId": enterprise_id,
        "policyIds": [str(policy.id)],
        "asOf": AS_OF,
        "period": YEAR,
    }
    response = client.post("/api/v1/assessment-runs", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_another_account_cannot_read_a_run(app, accounts, repository):
    owner = as_user(app, accounts, "enterprise.one", "token-one")
    stranger = as_user(app, accounts, "enterprise.two", "token-two")
    enterprise = make_enterprise(owner)
    created = _make_run(owner, repository, enterprise["id"])

    hidden = stranger.get(f"/api/v1/assessment-runs/{created['id']}")
    missing = stranger.get("/api/v1/assessment-runs/22222222-2222-4222-8222-222222222222")

    assert hidden.status_code == missing.status_code == 404
    assert hidden.json()["error"] == missing.json()["error"]


def test_run_is_owned_by_the_account_that_created_it(app, accounts, repository):
    owner = as_user(app, accounts, "enterprise.one", "token-one")
    enterprise = make_enterprise(owner)
    created = _make_run(owner, repository, enterprise["id"])

    user_id = accounts.users["enterprise.one"]["id"]
    assert str(repository.run_users[uuid_parse(created["id"])]) == str(user_id)


def test_reviewer_can_read_any_run(app, accounts, repository):
    owner = as_user(app, accounts, "enterprise.one", "token-one")
    reviewer = as_reviewer(app, accounts)
    enterprise = make_enterprise(owner)
    created = _make_run(owner, repository, enterprise["id"])

    assert reviewer.get(f"/api/v1/assessment-runs/{created['id']}").status_code == 200


def test_report_is_409_until_the_run_succeeded(app, accounts, repository, monkeypatch):
    owner = as_user(app, accounts, "enterprise.one", "token-one")
    enterprise = make_enterprise(owner)
    created = _make_run(owner, repository, enterprise["id"])
    monkeypatch.setattr(repository, "run_status", lambda run_id: "failed")

    response = owner.get(f"/api/v1/assessment-runs/{created['id']}/report.md")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "RUN_NOT_COMPLETED"
    assert response.json()["error"]["details"]["status"] == "failed"
