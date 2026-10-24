"""Object storage for uploaded policy sources.

``OBJECT_STORE_MODE=local`` writes bytes to ``LOCAL_OBJECT_DIR`` (default
``.local-objects`` at the repository root). ``obs`` uploads to a private
Huawei Cloud OBS bucket through the official ``esdk-obs-python`` SDK.

Two boundaries matter here:

* The SDK client is created lazily. Importing this module — or running in
  ``local`` mode — never loads credentials or opens a socket.
* Every SDK failure (non-2xx status or a raised exception) becomes the same
  ``503 DEPENDENCY_UNAVAILABLE`` answer, so callers never leak bucket names or
  credentials into a response.
"""

from __future__ import annotations

import os
import threading
import uuid
from pathlib import Path
from typing import Any, Callable, Protocol

from ..config import Settings
from ..errors import ApiProblem

LOCAL_KEY_PREFIX = "local"
OBS_KEY_PREFIX = "obs"

UNAVAILABLE_MESSAGE = "文件存储暂不可用，请稍后重试。"


class ObjectStore(Protocol):
    def put(self, data: bytes, *, suffix: str = "") -> str: ...

    def delete(self, key: str) -> None: ...


class LocalObjectStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    def put(self, data: bytes, *, suffix: str = "") -> str:
        name = f"{uuid.uuid4().hex}{suffix}"
        path = self._root / name
        try:
            # 目录可能不存在（全新部署、systemd 只读父目录），mkdir 失败同样是
            # 存储不可用，必须落在同一个 503 分支里，不能冒成 500。
            self._root.mkdir(parents=True, exist_ok=True)
            handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
        except OSError as exc:
            raise ApiProblem(503, "DEPENDENCY_UNAVAILABLE", UNAVAILABLE_MESSAGE) from exc
        return f"{LOCAL_KEY_PREFIX}/{name}"

    def delete(self, key: str) -> None:
        prefix, _, name = key.partition("/")
        if prefix != LOCAL_KEY_PREFIX or not name:
            return
        try:
            (self._root / name).unlink(missing_ok=True)
        except OSError:
            pass

    def close(self) -> None:
        return None


ClientFactory = Callable[[str, str, str], Any]


def _normalize_endpoint(raw: str) -> str:
    """OBS accepts ``obs.<region>.myhuaweicloud.com``; tolerate a scheme or slash."""
    endpoint = raw.strip()
    for scheme in ("https://", "http://"):
        if endpoint.lower().startswith(scheme):
            endpoint = endpoint[len(scheme) :]
    return endpoint.rstrip("/")


def _default_client_factory(access_key: str, secret_key: str, endpoint: str) -> Any:
    # Imported here so that ``local`` mode never requires the SDK at runtime.
    from obs import ObsClient

    return ObsClient(
        access_key_id=access_key,
        secret_access_key=secret_key,
        server=endpoint,
        timeout=10,
        max_retry_count=2,
        is_cname=False,
    )


class ObsObjectStore:
    """Private-bucket uploads through the official Huawei Cloud OBS SDK.

    Object keys use the ``obs/<uuid>`` namespace so they never collide with keys
    written by ``local`` mode; keys created under ``local/`` are not migrated.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: ClientFactory = _default_client_factory,
    ) -> None:
        self._bucket = settings.obs_bucket
        self._endpoint = _normalize_endpoint(settings.obs_endpoint)
        self._access_key = settings.obs_ak
        self._secret_key = settings.obs_sk
        self._factory = client_factory
        self._client: Any = None
        self._lock = threading.Lock()

    def _ensure_client(self) -> Any:
        """Build the SDK client once; ``put`` runs in a threadpool, hence the lock."""
        client = self._client
        if client is not None:
            return client
        with self._lock:
            if self._client is None:
                self._client = self._factory(
                    self._access_key, self._secret_key, self._endpoint
                )
            return self._client

    def put(self, data: bytes, *, suffix: str = "") -> str:
        key = f"{OBS_KEY_PREFIX}/{uuid.uuid4().hex}{suffix}"
        try:
            response = self._ensure_client().putObject(self._bucket, key, content=data)
        except Exception as exc:  # SDK error types are not stable across versions
            raise ApiProblem(503, "DEPENDENCY_UNAVAILABLE", UNAVAILABLE_MESSAGE) from exc
        status = getattr(response, "status", 0)
        if not 200 <= status < 300:
            raise ApiProblem(503, "DEPENDENCY_UNAVAILABLE", UNAVAILABLE_MESSAGE)
        return key

    def delete(self, key: str) -> None:
        prefix, _, name = key.partition("/")
        if prefix != OBS_KEY_PREFIX or not name:
            return
        try:
            self._ensure_client().deleteObject(self._bucket, f"{OBS_KEY_PREFIX}/{name}")
        except Exception:
            # Cleanup runs while another exception is propagating; do not mask it.
            return None

    def close(self) -> None:
        with self._lock:
            client, self._client = self._client, None
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def build_object_store(settings: Settings) -> ObjectStore:
    if settings.object_store_mode == "obs":
        return ObsObjectStore(settings)
    return LocalObjectStore(settings.local_object_path)
