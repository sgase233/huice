"""AUTH-01—05 与凭证真值表（#46 / #55 draft.4）。

这里断言的都是**判定顺序**与**凭证取舍**，不是业务字段：
门禁 → 身份 → 角色 → 对象归属 → CSRF/Origin。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app import auth as auth_tools
from app.config import Settings
from app.main import create_app
from support import (
    ADMIN_TOKEN,
    DEMO_SESSION_SECRET,
    FakeAccounts,
    FakeObjectStore,
    FakeRepository,
)

PASSWORD = "correct-horse-battery-9"
ORIGIN = "http://127.0.0.1:5173"
TEST_DATABASE_URL = "postgresql://huice:huice@127.0.0.1:5432/huice_test"
YEAR = {"startDate": "2026-01-01", "endDate": "2026-12-31"}

ENTERPRISE_BODY = {
    "username": "acme.one",
    "password": PASSWORD,
    "enterprise": {
        "nameSynthetic": "合成企业A",
        "period": YEAR,
        "facts": [
            {"key": "region", "value": "440300", "unit": "code", "period": YEAR, "source": "user_input"},
            {"key": "industry", "value": "F51", "unit": "code", "period": YEAR, "source": "user_input"},
        ],
    },
}


def build_client(**overrides) -> tuple[TestClient, object]:
    """独立的 app 与客户端：用于改部署配置的用例（审核员注册密钥）。"""

    values = {
        "app_env": "local",
        "api_host": "127.0.0.1",
        "database_url": TEST_DATABASE_URL,
        "admin_token": ADMIN_TOKEN,
        "demo_access_mode": "local",
        "demo_session_secret": DEMO_SESSION_SECRET,
        "local_object_dir": ".local-objects-test",
        "cors_origins": (ORIGIN,),
    }
    values.update(overrides)
    application = create_app(Settings(**values))
    application.state.repository = FakeRepository()
    application.state.object_store = FakeObjectStore()
    application.state.accounts = FakeAccounts(application.state.repository)
    return TestClient(application, headers={"Origin": ORIGIN}), application


def register(client: TestClient, **overrides):
    body = dict(ENTERPRISE_BODY)
    body.update(overrides)
    return client.post("/api/v1/auth/register", json=body)


def login(client: TestClient, username: str, password: str = PASSWORD):
    return client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )


# ------------------------------------------------------------------ AUTH-01


def test_register_creates_the_account_and_its_enterprise(client, accounts, repository):
    response = register(client)

    assert response.status_code == 201, response.text
    user = response.json()["user"]
    assert user["username"] == "acme.one"
    assert user["role"] == "enterprise"
    assert user["enterpriseId"] is not None
    # 企业归属绑到新账号，不是无主（假仓储用 UUID 做键，响应里是字符串）。
    owners = {str(key): value for key, value in repository.enterprise_owners.items()}
    assert str(owners[user["enterpriseId"]]) == user["id"]
    assert accounts.users["acme.one"]["role"] == "enterprise"


def test_register_normalizes_the_username(client, accounts):
    response = register(client, username="  ACME.Two  ")

    assert response.status_code == 201, response.text
    assert response.json()["user"]["username"] == "acme.two"
    assert "acme.two" in accounts.users


def test_register_rejects_an_invalid_username(client):
    response = register(client, username="A")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert response.json()["error"]["details"]["fieldErrors"][0]["field"] == "username"


def test_register_requires_an_enterprise_for_enterprise_accounts(client):
    body = dict(ENTERPRISE_BODY)
    body.pop("enterprise")
    response = client.post("/api/v1/auth/register", json=body)

    assert response.status_code == 422
    assert response.json()["error"]["details"]["fieldErrors"][0]["field"] == "enterprise"


def test_register_rejects_a_duplicate_username(client, accounts):
    accounts.seed_user("taken.one")

    response = register(client, username="taken.one")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "USERNAME_TAKEN"


def test_register_with_a_reviewer_key_creates_a_reviewer_without_enterprise():
    client, application = build_client(reviewer_signup_key="reviewer-signup-key-0001")
    body = {
        "username": "reviewer.two",
        "password": PASSWORD,
        "reviewerSignupKey": "reviewer-signup-key-0001",
    }
    response = client.post("/api/v1/auth/register", json=body)

    assert response.status_code == 201, response.text
    assert response.json()["user"]["role"] == "reviewer"
    assert response.json()["user"]["enterpriseId"] is None
    assert "reviewer.two" in application.state.accounts.users


def test_register_with_a_wrong_reviewer_key_is_forbidden(client):
    body = {
        "username": "reviewer.three",
        "password": PASSWORD,
        "reviewerSignupKey": "not-the-configured-key",
    }
    response = client.post("/api/v1/auth/register", json=body)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ROLE_FORBIDDEN"


def test_register_needs_a_same_origin_request(client):
    response = client.post(
        "/api/v1/auth/register", json=ENTERPRISE_BODY, headers={"Origin": "https://evil.example"}
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ORIGIN_MISMATCH"


# ------------------------------------------------------------------ AUTH-02


def test_login_sets_a_session_cookie_and_returns_the_same_csrf_token(client, accounts):
    accounts.seed_user("login.one")
    response = login(client, "login.one")

    assert response.status_code == 200, response.text
    body = response.json()
    assert response.headers["cache-control"] == "no-store"
    cookie = response.cookies["huice_session"]
    assert body["csrfToken"] == auth_tools.csrf_token_for(cookie)

    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["csrfToken"] == body["csrfToken"]


def test_login_rejects_a_wrong_password_without_saying_which_part_failed(client, accounts):
    accounts.seed_user("login.two")

    response = login(client, "login.two", "not-the-right-password")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"

    unknown = login(client, "nobody.here", PASSWORD)
    assert unknown.status_code == 401
    assert unknown.json()["error"]["code"] == "INVALID_CREDENTIALS"
    assert unknown.json()["error"]["message"] == response.json()["error"]["message"]


def test_login_replaces_a_live_session_instead_of_keeping_two(client, accounts):
    learner = accounts.seed_user("login.three")
    old_token = accounts.seed_session("login.three", token="old-session-token-0001")
    client.cookies.set("huice_session", old_token, path="/api")

    response = login(client, "login.three")

    assert response.status_code == 200
    old_row = accounts.sessions[auth_tools.digest(old_token)]
    assert old_row["revoked_at"] is not None
    assert old_row["user_id"] == learner["id"]


def test_login_ignores_an_expired_session_cookie(client, accounts):
    """HttpOnly Cookie 前端删不掉：带着失效会话也要能重新登录，不能 401。"""

    accounts.seed_user("login.four")
    stale = accounts.seed_session("login.four", token="stale-session-token-0001")
    accounts.sessions[auth_tools.digest(stale)]["revoked_at"] = "2026-01-01T00:00:00Z"
    client.cookies.set("huice_session", stale, path="/api")

    response = login(client, "login.four")

    assert response.status_code == 200, response.text


# ------------------------------------------------------------------ AUTH-03


def test_logout_revokes_only_the_presented_session(client, accounts):
    accounts.seed_user("logout.one")
    token = accounts.seed_session("logout.one", token="logout-token-0001")
    other = accounts.seed_session("logout.one", token="logout-token-0002")
    client.cookies.set("huice_session", token, path="/api")

    response = client.post(
        "/api/v1/auth/logout",
        headers={
            "Origin": "http://127.0.0.1:5173",
            "X-CSRF-Token": auth_tools.csrf_token_for(token),
        },
    )

    assert response.status_code == 204
    assert accounts.sessions[auth_tools.digest(token)]["revoked_at"] is not None
    assert accounts.sessions[auth_tools.digest(other)]["revoked_at"] is None


def test_logout_without_csrf_changes_nothing(client, accounts):
    accounts.seed_user("logout.two")
    token = accounts.seed_session("logout.two", token="logout-token-0003")
    client.cookies.set("huice_session", token, path="/api")

    response = client.post("/api/v1/auth/logout", headers={"Origin": "http://127.0.0.1:5173"})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_REQUIRED"
    assert accounts.sessions[auth_tools.digest(token)]["revoked_at"] is None


def test_logout_without_a_session_still_clears_the_cookie(anon_client):
    response = anon_client.post("/api/v1/auth/logout")

    assert response.status_code == 204
    assert "huice_session" in response.headers["set-cookie"]


def test_revoked_session_is_rejected_afterwards(client, accounts):
    accounts.seed_user("logout.three")
    token = accounts.seed_session("logout.three", token="logout-token-0004")
    accounts.sessions[auth_tools.digest(token)]["revoked_at"] = "2026-01-01T00:00:00Z"
    client.cookies.set("huice_session", token, path="/api")

    response = client.get("/api/v1/auth/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "SESSION_INVALID"


# ------------------------------------------------------------------ AUTH-04/05


def test_me_requires_a_session_not_a_bearer(anon_client, bearer):
    anonymous = anon_client.get("/api/v1/auth/me")
    assert anonymous.status_code == 401
    assert anonymous.json()["error"]["code"] == "UNAUTHENTICATED"

    from_bearer = anon_client.get("/api/v1/auth/me", headers=bearer)
    assert from_bearer.status_code == 403
    assert from_bearer.json()["error"]["code"] == "ROLE_FORBIDDEN"


def test_change_password_needs_the_current_one_and_kills_all_sessions(client, accounts):
    accounts.seed_user("pw.one")
    token = accounts.seed_session("pw.one", token="pw-token-0001")
    client.cookies.set("huice_session", token, path="/api")
    headers = {
        "Origin": "http://127.0.0.1:5173",
        "X-CSRF-Token": auth_tools.csrf_token_for(token),
    }

    wrong = client.post(
        "/api/v1/auth/password",
        json={"currentPassword": "nope-nope-nope", "newPassword": "a-new-password-9"},
        headers=headers,
    )
    assert wrong.status_code == 401
    assert wrong.json()["error"]["code"] == "INVALID_CREDENTIALS"

    changed = client.post(
        "/api/v1/auth/password",
        json={"currentPassword": PASSWORD, "newPassword": "a-new-password-9"},
        headers=headers,
    )
    assert changed.status_code == 204
    assert accounts.sessions[auth_tools.digest(token)]["revoked_at"] is not None
    assert auth_tools.verify_password(
        "a-new-password-9", accounts.users["pw.one"]["password_hash"]
    )


# ------------------------------------------------------------ 真值表（#54 D1.5）


def test_session_beats_a_wrong_bearer_instead_of_falling_through(client):
    """「提供了但无效」不降级：会话有效就用会话，错误 Bearer 不参与判定。"""

    response = client.get(
        "/api/v1/policies", headers={"Authorization": "Bearer not-the-admin-token"}
    )

    assert response.status_code == 200


def test_an_invalid_session_does_not_fall_back_to_the_demo_cookie(client, accounts):
    from conftest import ENTERPRISE_TOKEN

    accounts.sessions[auth_tools.digest(ENTERPRISE_TOKEN)]["revoked_at"] = "2026-01-01T00:00:00Z"

    response = client.get("/api/v1/policies")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "SESSION_INVALID"


def test_session_cookie_is_httponly_lax_and_scoped_to_api(client, accounts):
    accounts.seed_user("cookie.one")

    response = login(client, "cookie.one")
    header = response.headers["set-cookie"]

    assert "huice_session=" in header
    assert "HttpOnly" in header
    assert "SameSite=lax" in header.replace("samesite=lax", "SameSite=lax")
    assert "Path=/api" in header


# ------------------------------------------------------------------ 限流（D1.7）


def test_the_sixth_failed_login_is_throttled_even_with_the_right_password(client, accounts):
    accounts.seed_user("throttle.one")

    for _ in range(5):
        assert login(client, "throttle.one", "wrong-password-1").status_code == 401

    blocked = login(client, "throttle.one")

    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "ACCOUNT_LOCKED"
    assert blocked.headers["Retry-After"]
    # 被限流时连正确密码都到不了比对，更不会签发会话。
    assert "set-cookie" not in blocked.headers


def test_a_successful_login_does_not_reset_the_failure_window(client, accounts):
    accounts.seed_user("reset.one")

    for _ in range(4):
        assert login(client, "reset.one", "wrong-password-1").status_code == 401
    assert login(client, "reset.one").status_code == 200

    assert login(client, "reset.one", "wrong-password-1").status_code == 401  # 第 5 次
    assert login(client, "reset.one").status_code == 429


def test_field_validation_does_not_burn_attempts(client, accounts):
    """格式错误的注册请求是 422，不该算作一次失败把后来的登录锁住。"""

    accounts.seed_user("careful.one")
    for _ in range(6):
        assert register(client, username="A").status_code == 422

    assert login(client, "careful.one").status_code == 200
