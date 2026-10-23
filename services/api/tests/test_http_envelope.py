"""统一错误信封、追踪头、健康检查与 CORS 头。"""

from __future__ import annotations

from app.config import APP_VERSION

ALLOWED_ORIGIN = "http://127.0.0.1:5173"


class FakeDatabase:
    def __init__(self, available: bool) -> None:
        self.available = available

    def ping(self, timeout: float = 2.0) -> bool:
        return self.available

    def close(self) -> None:
        pass


def test_health_reports_ok_when_database_reachable(app, client):
    app.state.db = FakeDatabase(True)
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": APP_VERSION}
    assert response.headers["X-Trace-Id"]


def test_health_reports_503_without_leaking_details(app, client):
    app.state.db = FakeDatabase(False)
    response = client.get("/api/v1/health")

    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    assert body["traceId"] == response.headers["X-Trace-Id"]
    assert "127.0.0.1" not in response.text
    assert "postgres" not in response.text.lower()


def test_health_requires_no_credentials(app, client):
    app.state.db = FakeDatabase(True)
    assert client.get("/api/v1/health").status_code == 200


def test_unknown_route_uses_error_envelope(client):
    response = client.get("/api/v1/does-not-exist")

    assert response.status_code == 404
    body = response.json()
    assert set(body) == {"error", "traceId"}
    assert body["error"]["code"] == "NOT_FOUND"
    assert body["traceId"] == response.headers["X-Trace-Id"]


def test_unhandled_error_is_sanitised(app, client):
    @app.get("/api/v1/boom")
    async def boom():
        raise RuntimeError("postgresql://huice:secret@db.internal:5432/huice")

    response = client.get("/api/v1/boom")

    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert body["traceId"] == response.headers["X-Trace-Id"]
    assert "secret" not in response.text
    assert "Traceback" not in response.text
    assert "db.internal" not in response.text


def test_cors_preflight_allows_configured_origin_and_headers(client):
    response = client.options(
        "/api/v1/admin/policies/import",
        headers={
            "Origin": ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type,if-match",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    allowed = response.headers["access-control-allow-headers"].lower()
    for name in ("authorization", "content-type", "if-match"):
        assert name in allowed
    assert response.headers["access-control-allow-credentials"] == "true"


def test_cors_rejects_unknown_origin(client):
    response = client.options(
        "/api/v1/admin/policies/import",
        headers={
            "Origin": "http://evil.example.com",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_exposed_headers_include_etag_and_trace(app, client, auth, repository):
    app.state.db = FakeDatabase(True)
    response = client.get("/api/v1/admin/policies", headers={**auth, "Origin": ALLOWED_ORIGIN})

    assert response.status_code == 200
    exposed = response.headers["access-control-expose-headers"].lower()
    assert "etag" in exposed
    assert "x-trace-id" in exposed
