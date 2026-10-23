"""管理鉴权：Bearer 常量时间比较，restricted 模式额外要求演示会话 cookie。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from support import ADMIN_TOKEN, FakeObjectStore, FakeRepository

DEMO_COOKIE = "huice_demo_session"


def build_client(**overrides) -> TestClient:
    values = {
        "app_env": "local",
        "api_host": "127.0.0.1",
        "database_url": "postgresql://huice:huice@127.0.0.1:5432/huice_test",
        "admin_token": ADMIN_TOKEN,
        "demo_access_mode": "local",
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

    client.cookies.set(DEMO_COOKIE, "gateway-session")
    accepted = client.get("/api/v1/admin/policies", headers=headers)
    assert accepted.status_code == 200


def test_restricted_mode_still_checks_bearer():
    client = build_client(app_env="demo", demo_access_mode="restricted")
    client.cookies.set(DEMO_COOKIE, "gateway-session")

    response = client.get("/api/v1/admin/policies")
    assert response.status_code == 401


def test_blank_demo_cookie_is_rejected():
    client = build_client(app_env="demo", demo_access_mode="restricted")
    client.cookies.set(DEMO_COOKIE, "   ")

    response = client.get(
        "/api/v1/admin/policies",
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
    )
    assert response.status_code == 403
