"""门禁与身份：演示 cookie 的门禁层（E1 前一致）+ E1 后的三种凭证取舍。

门禁（``security.require_demo_session``）只看部署模式与 demo cookie 签名，
身份（``identity.resolve_identity``）只在门禁通过后按真值表选一个凭证。
两组断言分开写，是因为失败码不同：门禁失败 403 ``DEMO_SESSION_REQUIRED``，
身份失败是 401（未提供 / 提供了但无效），两者不能互换。
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.security import DEMO_SESSION_COOKIE, issue_demo_session
from support import (
    ADMIN_TOKEN,
    DEMO_SESSION_SECRET,
    FakeAccounts,
    FakeObjectStore,
    FakeRepository,
)

DEMO_COOKIE = DEMO_SESSION_COOKIE


def signed_session(hours: float = 1.0, secret: str = DEMO_SESSION_SECRET) -> str:
    return issue_demo_session(secret, expires_at=int(time.time() + hours * 3600))


def build_client(**overrides) -> TestClient:
    values = {
        "app_env": "local",
        "api_host": "127.0.0.1",
        "database_url": "postgresql://huice:huice@127.0.0.1:5432/huice_test",
        "admin_token": ADMIN_TOKEN,
        "demo_access_mode": "local",
        "demo_session_secret": DEMO_SESSION_SECRET,
        "local_object_dir": ".local-objects-test",
    }
    values.update(overrides)
    application = create_app(Settings(**values))
    application.state.repository = FakeRepository()
    application.state.object_store = FakeObjectStore()
    application.state.accounts = FakeAccounts(application.state.repository)
    return TestClient(application)


# ------------------------------------------------------------------ 身份层


def test_missing_identity_is_401(anon_client):
    response = anon_client.get("/api/v1/admin/policies")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


def test_wrong_bearer_is_token_invalid(anon_client):
    response = anon_client.get(
        "/api/v1/admin/policies",
        headers={"Authorization": "Bearer wrong-token-wrong-token"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_INVALID"
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("header", ["Basic dXNlcjpwYXNz", ADMIN_TOKEN, "Bearer ", "bearer"])
def test_non_bearer_or_empty_credentials_are_rejected(anon_client, header):
    response = anon_client.get("/api/v1/admin/policies", headers={"Authorization": header})
    assert response.status_code == 401


def test_reviewer_session_reads_the_admin_list(client, auth):
    assert client.get("/api/v1/admin/policies", headers=auth).status_code == 200


def test_bearer_still_reads_the_admin_list(anon_client, bearer):
    # 不带会话 Cookie：带了会话时真值表按「会话优先」选身份，Bearer 不参与判定。
    assert anon_client.get("/api/v1/admin/policies", headers=bearer).status_code == 200


def test_enterprise_session_cannot_read_the_admin_list(client):
    response = client.get("/api/v1/admin/policies")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ROLE_FORBIDDEN"


def test_bearer_can_no_longer_write(client, bearer):
    """E1 起运维 Bearer 只读：写操作要审核员会话。"""

    response = client.post("/api/v1/admin/policies/import", headers=bearer)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ROLE_FORBIDDEN"


def test_import_requires_an_identity(anon_client):
    assert anon_client.post("/api/v1/admin/policies/import").status_code == 401


# ------------------------------------------------------------------ 门禁层


def test_restricted_mode_requires_demo_cookie():
    client = build_client(app_env="demo", demo_access_mode="restricted")
    headers = {"Authorization": f"Bearer {ADMIN_TOKEN}"}

    missing = client.get("/api/v1/admin/policies", headers=headers)
    assert missing.status_code == 403
    assert missing.json()["error"]["code"] == "DEMO_SESSION_REQUIRED"

    client.cookies.set(DEMO_COOKIE, signed_session())
    accepted = client.get("/api/v1/admin/policies", headers=headers)
    assert accepted.status_code == 200


@pytest.mark.parametrize(
    "value",
    [
        "gateway-session",  # 任意非空串，曾经能通过
        "v1.notanumber.deadbeef",
        "v2.9999999999.deadbeef",
        "v1.9999999999",  # 缺签名
        "",
        "   ",
    ],
)
def test_restricted_mode_rejects_unsigned_or_malformed_cookie(value):
    client = build_client(app_env="demo", demo_access_mode="restricted")
    client.cookies.set(DEMO_COOKIE, value)

    response = client.get(
        "/api/v1/admin/policies",
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "DEMO_SESSION_REQUIRED"


def test_expired_session_cookie_is_rejected():
    client = build_client(app_env="demo", demo_access_mode="restricted")
    client.cookies.set(DEMO_COOKIE, signed_session(hours=-1))

    response = client.get(
        "/api/v1/admin/policies",
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
    )
    assert response.status_code == 403


def test_tampered_expiry_is_rejected():
    version, _, rest = signed_session().partition(".")
    _, _, signature = rest.partition(".")
    forged = f"{version}.9999999999.{signature}"

    client = build_client(app_env="demo", demo_access_mode="restricted")
    client.cookies.set(DEMO_COOKIE, forged)

    response = client.get(
        "/api/v1/admin/policies",
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
    )
    assert response.status_code == 403


def test_session_signed_with_admin_token_is_rejected():
    """持 Bearer 的人不能自行签发会话：两层门禁必须独立（M2 二审 P1）。"""
    client = build_client(app_env="demo", demo_access_mode="restricted")
    client.cookies.set(DEMO_COOKIE, signed_session(secret=ADMIN_TOKEN))

    response = client.get(
        "/api/v1/admin/policies",
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "DEMO_SESSION_REQUIRED"


def test_runtime_guard_rejects_secret_equal_to_admin_token():
    """即使绕过配置校验直接构造 Settings，密钥等于 ADMIN_TOKEN 时也必须拒绝。"""
    client = build_client(
        app_env="demo",
        demo_access_mode="restricted",
        demo_session_secret=ADMIN_TOKEN,
    )
    client.cookies.set(DEMO_COOKIE, signed_session(secret=ADMIN_TOKEN))

    response = client.get(
        "/api/v1/admin/policies",
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "DEMO_SESSION_REQUIRED"


def test_session_signed_with_another_secret_is_rejected():
    client = build_client(app_env="demo", demo_access_mode="restricted")
    client.cookies.set(DEMO_COOKIE, signed_session(secret="another-demo-secret-0000000000000002"))

    response = client.get(
        "/api/v1/admin/policies",
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
    )
    assert response.status_code == 403


def test_restricted_demo_cookie_alone_is_not_a_reviewer():
    """门禁通过只说明「能进来」；demo 是身份之一，但角色不够读后台列表。"""

    client = build_client(app_env="demo", demo_access_mode="restricted")
    client.cookies.set(DEMO_COOKIE, signed_session())

    response = client.get("/api/v1/admin/policies")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ROLE_FORBIDDEN"


def test_local_mode_ignores_the_cookie_entirely():
    client = build_client(app_env="local", demo_access_mode="local")

    response = client.get(
        "/api/v1/admin/policies",
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
    )
    assert response.status_code == 200
