"""restricted 模式下「门禁先于身份」的行为必须与发布的 ``x-e1-gate`` 一致。

PR #61 最新复审 P1 的另一半：契约里写出门禁还不够，得有测试证明**实际的** restricted
行为就是文档写的那样。这里不逐个手写端点，而是读服务自己发布的 OpenAPI 文档，按每个
操作的 ``x-e1-gate`` 发一次**不带任何凭证**的请求：

* 文档说 ``restricted: DemoSession`` 的，必须是 403 ``DEMO_SESSION_REQUIRED``——
  不能是 401（门禁不是身份），也不能是 422（字段校验跑到了门禁前面）；
* 文档说 ``restricted: exempt`` 的，只有健康检查与登出，它们不能被这个码挡住。

新增端点会自动进入这组断言：谁把门禁漏掉（或把某个端点悄悄加进豁免），这里直接转红。
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.security import DEMO_SESSION_COOKIE, issue_demo_session
from support import DEMO_SESSION_SECRET

from test_auth import build_client

#: 路径参数的替身：只要能在路由层匹配上，具体的对象是否存在由门禁之后的层负责。
SAMPLE_PATH_PARAMS = {
    "enterpriseId": str(uuid.uuid4()),
    "runId": str(uuid.uuid4()),
    "policyId": str(uuid.uuid4()),
    "factKey": "industry",
}

#: 请求体由 handler 里显式调用门禁的操作：必须给出**能过 schema** 的体，否则 422
#: 会先于门禁发生（那正是这组断言要排除的情况）。其余操作的请求体是路由级依赖先跑，
#: 给空对象就够。
SAMPLE_BODIES = {
    "registerUser": {"username": "gate.user", "password": "gate-password-0001"},
    "loginUser": {"username": "gate.user", "password": "gate-password-0001"},
    "changePassword": {
        "currentPassword": "gate-password-0001",
        "newPassword": "gate-password-0002",
    },
}

HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def _concrete(path: str) -> str:
    for name, value in SAMPLE_PATH_PARAMS.items():
        path = path.replace("{" + name + "}", value)
    return path


def _iter_operations(client: TestClient):
    schema = client.app.openapi()  # type: ignore[attr-defined]
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            if method not in HTTP_METHODS:
                continue
            yield path, method, operation


def _request(client: TestClient, path: str, method: str, operation: dict):
    body = None
    if "requestBody" in operation:
        body = SAMPLE_BODIES.get(operation["operationId"], {})
    return client.request(method.upper(), _concrete(path), json=body)


def test_restricted_mode_never_bypasses_a_documented_gate():
    client = build_client(app_env="demo", demo_access_mode="restricted")
    checked = 0

    for path, method, operation in _iter_operations(client):
        gate = operation["x-e1-gate"]["restricted"]
        response = _request(client, path, method, operation)

        if gate == "DemoSession":
            assert response.status_code == 403, (method, path, response.text)
            assert response.json()["error"]["code"] == "DEMO_SESSION_REQUIRED", (
                method,
                path,
                response.text,
            )
            checked += 1
        else:
            assert gate == "exempt", (method, path, gate)
            error = response.json().get("error", {}) if response.content else {}
            assert error.get("code") != "DEMO_SESSION_REQUIRED", (method, path)

    # 16 个现行操作 + 7 个 E1 操作里，只有 health 与 logout 两侧豁免。
    assert checked == 21


def test_the_exempt_endpoints_reach_their_handlers():
    """豁免不是「放行一切」：健康检查走到依赖检查、登出真的能清 Cookie。

    这里不替换数据库依赖，健康检查可能是 503 `DEPENDENCY_UNAVAILABLE`（本机没有测试库）；
    关键是它**没有被门禁挡在 403**，说明豁免生效、请求到了 handler。
    """

    client = build_client(app_env="demo", demo_access_mode="restricted")

    health = client.get("/api/v1/health")
    assert health.status_code in (200, 503)
    if health.status_code == 503:
        assert health.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"

    logout = client.post("/api/v1/auth/logout")
    assert logout.status_code == 204


def test_a_valid_demo_cookie_passes_the_gate_but_identity_still_applies():
    """门禁通过不等于授权：restricted 下拿着有效 demo cookie 读后台仍是 403 角色不足。"""

    client = build_client(app_env="demo", demo_access_mode="restricted")
    client.cookies.set(
        DEMO_SESSION_COOKIE,
        issue_demo_session(DEMO_SESSION_SECRET, expires_at=2**31 - 1, sid="gate-sid-0001"),
    )

    response = client.get("/api/v1/admin/policies")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ROLE_FORBIDDEN"


def test_local_mode_never_publishes_or_returns_the_gate_error():
    """local 部署没有门禁：同样不带凭证的请求不能出现 DEMO_SESSION_REQUIRED。"""

    client = build_client(app_env="local", demo_access_mode="local")

    for path, method, operation in _iter_operations(client):
        assert operation["x-e1-gate"]["local"] == "none", (method, path)
        response = _request(client, path, method, operation)
        if response.content:
            error = response.json().get("error") or {}
            assert error.get("code") != "DEMO_SESSION_REQUIRED", (method, path)


@pytest.mark.parametrize("path", ["/api/v1/health", "/api/v1/auth/logout"])
def test_health_and_logout_are_the_only_exempt_operations(path):
    client = build_client(app_env="demo", demo_access_mode="restricted")
    operations = client.app.openapi()["paths"][path]  # type: ignore[attr-defined]

    assert {op["x-e1-gate"]["restricted"] for op in operations.values()} == {"exempt"}
