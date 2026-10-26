"""管理鉴权：Bearer 常量时间比较，restricted 模式额外要求演示会话 cookie。"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.security import DEMO_SESSION_COOKIE, issue_demo_session
from support import ADMIN_TOKEN, DEMO_SESSION_SECRET, FakeObjectStore, FakeRepository

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
    return TestClient(application)


def test_missing_bearer_is_rejected(client):
    response = client.get("/api/v1/admin/policies")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"
    assert response.headers["www-authenticate"] == "Bearer"


def test_wrong_token_is_rejected(client):
    response = client.get(
        "/api/v1/admin/policies",
        headers={"Authorization": "Bearer wrong-token-wrong-token"},
    )
    assert response.status_code == 401


@pytest.mark.parametrize("header", ["Basic dXNlcjpwYXNz", ADMIN_TOKEN, "Bearer ", "bearer"])
def test_non_bearer_or_empty_credentials_are_rejected(client, header):
    response = client.get("/api/v1/admin/policies", headers={"Authorization": header})
    assert response.status_code == 401


def test_correct_token_passes_in_local_mode(client, auth):
    response = client.get("/api/v1/admin/policies", headers=auth)
    assert response.status_code == 200


def test_import_requires_bearer_too(client):
    response = client.post("/api/v1/admin/policies/import")
    assert response.status_code == 401


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


def test_restricted_mode_still_checks_bearer():
    client = build_client(app_env="demo", demo_access_mode="restricted")
    client.cookies.set(DEMO_COOKIE, signed_session())

    response = client.get("/api/v1/admin/policies")
    assert response.status_code == 401


def test_local_mode_ignores_the_cookie_entirely(client, auth):
    response = client.get("/api/v1/admin/policies", headers=auth)
    assert response.status_code == 200
