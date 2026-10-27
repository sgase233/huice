"""真实华为云 OBS 桶上的写入、读回与删除（可选用例）。

设置 ``HUICE_TEST_OBS_BUCKET`` / ``HUICE_TEST_OBS_ENDPOINT`` / ``HUICE_TEST_OBS_AK`` /
``HUICE_TEST_OBS_SK`` 才运行。用例只操作自己写入的 ``obs/<uuid>`` 键，结束即删除，
因此可以指向已有数据的私有桶。

写入用被测实现（``ObsObjectStore``），读回与存在性校验用独立构造的 SDK 客户端，
避免「自己写、自己读、自己认为对」的循环验证。

凭证处理：pytest 在用例失败时会把**夹具取值**打印进终端，所以凭证一律装在
``Credentials`` 里（``repr`` 已重写为 ``<redacted>``），``Settings`` 只在辅助函数
内部临时构造、不绑定到测试用例的局部变量——2026-10-27 首次实测就是因为在夹具里
直接返回了带 AK/SK 的 ``Settings``，失败时把密钥打进了终端。
"""

from __future__ import annotations

import dataclasses
import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient
from obs import ObsClient

from app.config import Settings
from app.errors import ApiProblem
from app.integrations.obs import (
    OBS_KEY_PREFIX,
    UNAVAILABLE_MESSAGE,
    ObsObjectStore,
    build_object_store,
)
from app.main import create_app
from support import ADMIN_TOKEN, FakeRepository, metadata_payload, multipart

pytestmark = [pytest.mark.obs]

IMPORT_URL = "/api/v1/admin/policies/import"
VARIABLES = (
    "HUICE_TEST_OBS_BUCKET",
    "HUICE_TEST_OBS_ENDPOINT",
    "HUICE_TEST_OBS_AK",
    "HUICE_TEST_OBS_SK",
)
BODY_TEXT = "深圳市中小企业数字化转型扶持计划正文（OBS 真桶用例）".encode()


@dataclass(frozen=True)
class Credentials:
    bucket: str
    endpoint: str
    ak: str = field(repr=False)
    sk: str = field(repr=False)

    def __repr__(self) -> str:
        return (
            f"Credentials(bucket={self.bucket!r}, endpoint={self.endpoint!r}, "
            "ak=<redacted>, sk=<redacted>)"
        )


@pytest.fixture(scope="module")
def creds() -> Credentials:
    missing = [name for name in VARIABLES if not os.environ.get(name, "").strip()]
    if missing:
        pytest.skip("未设置 " + "、".join(missing) + "，跳过真实 OBS 用例")
    return Credentials(*(os.environ[name].strip() for name in VARIABLES))


def _configured(settings: Settings, creds: Credentials) -> Settings:
    """只在本函数内构造带密钥的 Settings：调用方不绑定它，失败时不会被打印。"""
    return dataclasses.replace(
        settings,
        object_store_mode="obs",
        obs_bucket=creds.bucket,
        obs_endpoint=creds.endpoint,
        obs_ak=creds.ak,
        obs_sk=creds.sk,
    )


def _sdk(creds: Credentials) -> ObsClient:
    endpoint = creds.endpoint
    for scheme in ("https://", "http://"):
        if endpoint.lower().startswith(scheme):
            endpoint = endpoint[len(scheme) :]
    return ObsClient(
        access_key_id=creds.ak,
        secret_access_key=creds.sk,
        server=endpoint.rstrip("/"),
        timeout=10,
        max_retry_count=1,
        is_cname=False,
    )


@pytest.fixture(scope="module")
def sdk(creds) -> Iterator[ObsClient]:
    client = _sdk(creds)
    yield client
    client.close()


@pytest.fixture()
def created_keys(sdk, creds) -> Iterator[list[str]]:
    """记录本用例写入的键；无论断言是否失败，结束时都从桶里删掉。"""
    keys: list[str] = []
    yield keys
    for key in keys:
        sdk.deleteObject(creds.bucket, key)


@pytest.fixture()
def store(settings, creds) -> Iterator[ObsObjectStore]:
    real = build_object_store(_configured(settings, creds))
    assert isinstance(real, ObsObjectStore)
    yield real
    real.close()


class _RecordingStore:
    """包一层记录写入的键：请求中途失败时，键已在夹具的清理列表里。"""

    def __init__(self, inner, sink: list[str]) -> None:
        self._inner = inner
        self._sink = sink

    def put(self, data: bytes, *, suffix: str = "") -> str:
        key = self._inner.put(data, suffix=suffix)
        self._sink.append(key)
        return key

    def delete(self, key: str) -> None:
        self._inner.delete(key)

    def close(self) -> None:
        self._inner.close()


def _download(sdk: ObsClient, bucket: str, key: str):
    return sdk.getObject(bucket, key, loadStreamInMemory=True)


def test_put_writes_bytes_that_can_be_read_back(store, sdk, creds, created_keys) -> None:
    key = store.put(BODY_TEXT, suffix=".txt")
    created_keys.append(key)

    assert key.startswith(f"{OBS_KEY_PREFIX}/")
    assert key.endswith(".txt")
    response = _download(sdk, creds.bucket, key)
    assert response.status == 200
    assert response.body.buffer == BODY_TEXT


def test_delete_removes_the_object_from_the_bucket(store, sdk, creds, created_keys) -> None:
    key = store.put(b"to be deleted", suffix=".txt")
    created_keys.append(key)  # 夹具里的删除对已删除的键是幂等的

    store.delete(key)

    assert _download(sdk, creds.bucket, key).status == 404


def test_two_puts_use_distinct_keys_and_both_survive(store, sdk, creds, created_keys) -> None:
    first = store.put(BODY_TEXT, suffix=".txt")
    second = store.put(BODY_TEXT, suffix=".txt")
    created_keys.extend([first, second])

    assert first != second
    assert _download(sdk, creds.bucket, first).status == 200
    assert _download(sdk, creds.bucket, second).status == 200


def test_import_uploads_the_body_source_to_the_bucket(settings, creds, sdk, created_keys) -> None:
    application = create_app(_configured(settings, creds))
    repository = FakeRepository()
    application.state.repository = repository
    real_store = application.state.object_store
    application.state.object_store = _RecordingStore(real_store, created_keys)
    client = TestClient(application)
    try:
        response = client.post(
            IMPORT_URL,
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
            files=multipart(metadata_payload(), files=[("notice.txt", BODY_TEXT, "text/plain")]),
        )
        assert response.status_code == 201, response.text

        # 契约的 SourceDocument 不含 storageKey（内部键），只能从仓储入参断言。
        assert len(repository.created) == 1
        saved = list(repository.created[0]["storage_keys"])
        assert len(saved) == 1
        assert saved[0].startswith(f"{OBS_KEY_PREFIX}/")
        assert saved == created_keys  # 落库的键就是真的写进桶的那一条

        stored = _download(sdk, creds.bucket, saved[0])
        assert stored.status == 200
        assert stored.body.buffer == BODY_TEXT
    finally:
        real_store.close()


def test_unwritable_bucket_becomes_503_without_leaking_credentials(settings, creds) -> None:
    missing_bucket = f"huice-missing-{uuid.uuid4().hex}"
    configured = dataclasses.replace(_configured(settings, creds), obs_bucket=missing_bucket)
    store = build_object_store(configured)
    assert isinstance(store, ObsObjectStore)
    try:
        with pytest.raises(ApiProblem) as excinfo:
            store.put(b"x", suffix=".txt")
    finally:
        store.close()

    problem = excinfo.value
    assert problem.status_code == 503
    assert problem.code == "DEPENDENCY_UNAVAILABLE"
    assert problem.message == UNAVAILABLE_MESSAGE
    rendered = repr(problem) + str(problem.details)
    for secret in (missing_bucket, creds.ak, creds.sk):
        assert secret not in rendered
