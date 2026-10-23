"""OBS 对象存储：键命名空间、失败映射与凭据不外泄（全部用假客户端，不连云）。"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.errors import ApiProblem
from app.integrations.obs import ObsObjectStore, build_object_store


def obs_settings(**overrides) -> Settings:
    values = {
        "database_url": "postgresql://huice:pw@127.0.0.1:5432/huice",
        "admin_token": "0123456789abcdef0123456789abcdef",
        "object_store_mode": "obs",
        "obs_bucket": "huice-policies",
        "obs_endpoint": "obs.cn-south-1.myhuaweicloud.com",
        "obs_ak": "AK-secret-value",
        "obs_sk": "SK-secret-value",
    }
    values.update(overrides)
    return Settings(**values)


class FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status


class FakeObsClient:
    def __init__(self, *, status: int = 200, error: Exception | None = None) -> None:
        self.status = status
        self.error = error
        self.puts: list[tuple] = []
        self.deletes: list[tuple] = []
        self.closed = False
        self.put_calls = 0

    def putObject(self, bucket, key, content):
        self.put_calls += 1
        if self.error is not None:
            raise self.error
        self.puts.append((bucket, key, content))
        return FakeResponse(self.status)

    def deleteObject(self, bucket, key):
        if self.error is not None:
            raise self.error
        self.deletes.append((bucket, key))
        return FakeResponse(self.status)

    def close(self):
        self.closed = True


def store_with(client: FakeObsClient, settings: Settings | None = None) -> ObsObjectStore:
    created: list[dict] = []

    def factory(access_key, secret_key, endpoint):
        created.append({"ak": access_key, "sk": secret_key, "endpoint": endpoint})
        return client

    store = ObsObjectStore(settings or obs_settings(), client_factory=factory)
    store.created = created  # type: ignore[attr-defined]
    return store


def test_put_uploads_into_obs_namespace():
    client = FakeObsClient()
    store = store_with(client)

    key = store.put(b"%PDF-1.7 data", suffix=".pdf")

    assert key.startswith("obs/")
    assert key.endswith(".pdf")
    assert client.puts == [("huice-policies", key, b"%PDF-1.7 data")]


def test_client_is_created_once_and_uses_normalized_endpoint():
    client = FakeObsClient()
    store = store_with(client, obs_settings(obs_endpoint="https://obs.cn-south-1.myhuaweicloud.com/"))

    store.put(b"a")
    store.put(b"b")

    assert client.put_calls == 2
    assert store.created == [
        {
            "ak": "AK-secret-value",
            "sk": "SK-secret-value",
            "endpoint": "obs.cn-south-1.myhuaweicloud.com",
        }
    ]


@pytest.mark.parametrize("status", [403, 404, 500])
def test_non_2xx_status_becomes_dependency_unavailable(status):
    store = store_with(FakeObsClient(status=status))

    with pytest.raises(ApiProblem) as info:
        store.put(b"data")

    assert info.value.status_code == 503
    assert info.value.code == "DEPENDENCY_UNAVAILABLE"


def test_sdk_exception_is_mapped_without_leaking_details():
    client = FakeObsClient(error=RuntimeError("AK-secret-value SK-secret-value bucket"))
    store = store_with(client)

    with pytest.raises(ApiProblem) as info:
        store.put(b"data")

    message = str(info.value)
    assert info.value.status_code == 503
    for secret in ("AK-secret-value", "SK-secret-value", "huice-policies"):
        assert secret not in message


def test_delete_uses_obs_namespace_and_swallows_failures():
    client = FakeObsClient()
    store = store_with(client)

    store.delete("obs/abc123.pdf")
    assert client.deletes == [("huice-policies", "obs/abc123.pdf")]

    failing = store_with(FakeObsClient(error=RuntimeError("boom")))
    assert failing.delete("obs/abc123.pdf") is None


@pytest.mark.parametrize("key", ["local/abc.txt", "abc.txt", "obs/", ""])
def test_delete_ignores_foreign_keys(key):
    client = FakeObsClient()
    store = store_with(client)

    store.delete(key)

    assert client.deletes == []


def test_close_releases_client_and_next_put_recreates_it():
    client = FakeObsClient()
    store = store_with(client)

    store.put(b"x")
    store.close()
    assert client.closed is True

    store.put(b"y")
    assert len(store.created) == 2  # type: ignore[attr-defined]


def test_local_mode_still_builds_local_store():
    store = build_object_store(Settings(object_store_mode="local", local_object_dir=".tmp-obs-test"))
    assert type(store).__name__ == "LocalObjectStore"


def test_obs_mode_builds_obs_store():
    store = build_object_store(obs_settings())
    assert isinstance(store, ObsObjectStore)
