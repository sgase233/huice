"""API-15 列表与 API-16 详情的 HTTP 行为，以及分页游标的签名校验。"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.db import Database
from app.errors import ApiProblem
from app.integrations.storage import PolicyRepository
from app.schemas import PolicyImportMetadata
from support import build_draft, metadata_payload


def seed(repository, **overrides):
    draft = build_draft(PolicyImportMetadata.model_validate(metadata_payload(**overrides)), [])
    repository.drafts.append(draft)
    return draft


def cursor_repository() -> PolicyRepository:
    return PolicyRepository(
        Database("postgresql://huice:huice@127.0.0.1:65432/huice_test"),
        cursor_secret="test-cursor-secret",
    )


def test_list_returns_items_without_etag(client, auth, repository):
    seed(repository)

    response = client.get("/api/v1/admin/policies", headers=auth)

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 1
    assert body["nextCursor"] is None
    assert "ETag" not in response.headers
    assert repository.list_calls[0] == {
        "status": None,
        "policy_type": None,
        "cursor": None,
        "limit": 20,
    }


def test_list_returns_empty_page(client, auth):
    response = client.get("/api/v1/admin/policies", headers=auth)

    assert response.status_code == 200
    assert response.json() == {"items": [], "nextCursor": None}


def test_list_passes_filters(client, auth, repository):
    seed(repository)

    response = client.get(
        "/api/v1/admin/policies?status=pending_parse&type=grant&limit=5",
        headers=auth,
    )

    assert response.status_code == 200
    assert repository.list_calls[0]["status"] == "pending_parse"
    assert repository.list_calls[0]["policy_type"] == "grant"
    assert repository.list_calls[0]["limit"] == 5


@pytest.mark.parametrize(
    "query",
    ["limit=0", "limit=51", "limit=abc", "status=deleted", "type=subsidy", "cursor="],
)
def test_invalid_query_parameters_return_400(client, auth, query):
    response = client.get(f"/api/v1/admin/policies?{query}", headers=auth)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_QUERY"


def test_detail_returns_etag(client, auth, repository):
    draft = seed(repository)

    response = client.get(f"/api/v1/admin/policies/{draft.id}", headers=auth)

    assert response.status_code == 200
    assert response.headers["ETag"] == f'"{draft.revision}"'
    assert response.json()["revision"] == str(draft.revision)
    assert repository.get_calls[-1] == (draft.id, None)


def test_detail_passes_explicit_version(client, auth, repository):
    draft = seed(repository, version="v2.0")

    response = client.get(f"/api/v1/admin/policies/{draft.id}?version=v2.0", headers=auth)

    assert response.status_code == 200
    assert repository.get_calls[-1] == (draft.id, "v2.0")


def test_detail_unknown_policy_returns_404(client, auth):
    response = client.get(f"/api/v1/admin/policies/{uuid4()}", headers=auth)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "POLICY_NOT_FOUND"


@pytest.mark.parametrize("path", ["not-a-uuid", "1234"])
def test_detail_invalid_uuid_returns_400(client, auth, path):
    response = client.get(f"/api/v1/admin/policies/{path}", headers=auth)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_QUERY"


def test_detail_empty_version_returns_400(client, auth, repository):
    draft = seed(repository)

    response = client.get(f"/api/v1/admin/policies/{draft.id}?version=", headers=auth)

    assert response.status_code == 400


def test_cursor_roundtrip_keeps_position_and_fingerprint():
    repository = cursor_repository()
    imported_at = datetime(2026, 10, 23, 8, 30, tzinfo=timezone.utc)
    version_id = uuid4()

    token = repository._encode_cursor(imported_at, version_id, "fingerprint-a")
    decoded_at, decoded_id = repository._decode_cursor(token, "fingerprint-a")

    assert decoded_at == imported_at
    assert decoded_id == version_id


def test_cursor_with_other_filters_is_rejected():
    repository = cursor_repository()
    token = repository._encode_cursor(datetime.now(timezone.utc), uuid4(), "fingerprint-a")

    with pytest.raises(ApiProblem) as info:
        repository._decode_cursor(token, "fingerprint-b")

    assert info.value.status_code == 400
    assert info.value.code == "INVALID_QUERY"


def test_tampered_cursor_is_rejected():
    repository = cursor_repository()
    token = repository._encode_cursor(datetime.now(timezone.utc), uuid4(), "fingerprint-a")
    payload, signature = token.split(".")
    forged = base64.urlsafe_b64encode(
        json.dumps({"t": "2020-01-01T00:00:00+00:00", "i": str(uuid4()), "f": "fingerprint-a"}).encode()
    ).rstrip(b"=").decode()

    with pytest.raises(ApiProblem):
        repository._decode_cursor(f"{forged}.{signature}", "fingerprint-a")


@pytest.mark.parametrize("value", ["", "abc", "a.b.c", "!!!.???"])
def test_garbage_cursor_is_rejected(value):
    repository = cursor_repository()

    with pytest.raises(ApiProblem) as info:
        repository._decode_cursor(value, "fingerprint-a")

    assert info.value.code == "INVALID_QUERY"
