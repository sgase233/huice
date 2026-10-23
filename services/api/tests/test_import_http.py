"""API-04 导入接口的 HTTP 行为（使用假仓储，不依赖数据库）。"""

from __future__ import annotations

import hashlib

from app.agents.ingestion import MAX_FILE_BYTES
from app.errors import ApiProblem
from support import metadata_payload, multipart

IMPORT_URL = "/api/v1/admin/policies/import"
BODY_TEXT = "深圳市中小企业数字化转型扶持计划正文".encode()


def post(client, auth, payload, files=None, fields=None):
    return client.post(
        IMPORT_URL,
        headers=auth,
        files=multipart(payload, files=files, fields=fields),
    )


def test_import_returns_201_with_pending_parse_draft(client, auth, repository, object_store):
    response = post(
        client,
        auth,
        metadata_payload(),
        files=[("notice.txt", BODY_TEXT, "text/plain")],
        fields=None,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pending_parse"
    assert body["version"] == "v1.0"
    assert body["type"] == "grant"
    assert body["criteria"] == []
    assert body["validationFindings"] == []
    assert body["documentNumber"] is None
    assert response.headers["ETag"] == f'"{body["revision"]}"'
    assert len(response.headers["X-Trace-Id"]) == 36
    assert "Location" not in response.headers

    assert len(body["sources"]) == 1
    source = body["sources"][0]
    assert source["kind"] == "body"
    assert source["title"] == "notice.txt"
    assert source["contentHash"] == hashlib.sha256(BODY_TEXT).hexdigest()
    assert source["text"] == BODY_TEXT.decode()

    assert len(repository.created) == 1
    assert len(object_store.objects) == 1
    assert repository.created[0]["trace_id"] == response.headers["X-Trace-Id"]


def test_first_file_is_body_and_followers_are_attachments(client, auth, repository):
    response = post(
        client,
        auth,
        metadata_payload(),
        files=[
            ("notice.txt", BODY_TEXT, "text/plain"),
            ("annex.txt", "附件".encode(), "text/plain"),
        ],
    )

    assert response.status_code == 201
    kinds = [item["kind"] for item in response.json()["sources"]]
    assert kinds == ["body", "attachment"]
    assert len(repository.created[0]["storage_keys"]) == 2


def test_import_without_files_is_allowed(client, auth):
    response = post(client, auth, metadata_payload())

    assert response.status_code == 201
    assert response.json()["sources"] == []


def test_metadata_field_is_required(client, auth):
    response = post(client, auth, None, files=[("notice.txt", BODY_TEXT, "text/plain")])

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"]["fieldErrors"][0]["field"] == "metadata"


def test_malformed_json_is_rejected(client, auth):
    response = post(client, auth, "{not json")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_METADATA"


def test_non_object_json_is_rejected(client, auth):
    response = post(client, auth, "[]")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_METADATA"


def test_unknown_field_is_rejected(client, auth):
    response = post(client, auth, metadata_payload(extra="x"))

    assert response.status_code == 422
    fields = {item["field"] for item in response.json()["error"]["details"]["fieldErrors"]}
    assert "extra" in fields


def test_invalid_policy_type_is_rejected(client, auth):
    response = post(client, auth, metadata_payload(type="subsidy"))

    assert response.status_code == 422
    fields = {item["field"] for item in response.json()["error"]["details"]["fieldErrors"]}
    assert "type" in fields


def test_effective_date_before_published_date_is_rejected(client, auth):
    response = post(client, auth, metadata_payload(effectiveFrom="2026-08-01"))

    assert response.status_code == 422
    fields = {item["field"] for item in response.json()["error"]["details"]["fieldErrors"]}
    assert "effectiveFrom" in fields


def test_effective_to_before_effective_from_is_rejected(client, auth):
    response = post(client, auth, metadata_payload(effectiveTo="2026-09-15"))

    assert response.status_code == 422
    fields = {item["field"] for item in response.json()["error"]["details"]["fieldErrors"]}
    assert "effectiveTo" in fields


def test_application_window_inversion_is_rejected(client, auth):
    response = post(
        client,
        auth,
        metadata_payload(
            applicationWindow={
                "opensAt": "2026-11-01T09:00:00+08:00",
                "closesAt": "2026-10-01T09:00:00+08:00",
            }
        ),
    )

    assert response.status_code == 422
    fields = {item["field"] for item in response.json()["error"]["details"]["fieldErrors"]}
    assert "applicationWindow.closesAt" in fields


def test_non_official_source_url_is_rejected(client, auth):
    response = post(client, auth, metadata_payload(sourceUrl="https://example.com/policy"))

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "OFFICIAL_URL_REJECTED"
    assert error["details"]["field"] == "sourceUrl"


def test_ssrf_style_entry_url_is_rejected(client, auth):
    response = post(client, auth, metadata_payload(officialEntryUrl="http://169.254.169.254/latest"))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "OFFICIAL_URL_REJECTED"


def test_more_than_five_files_are_rejected(client, auth):
    files = [(f"f{index}.txt", b"body", "text/plain") for index in range(6)]
    response = post(client, auth, metadata_payload(), files=files)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "TOO_MANY_FILES"


def test_extra_form_field_is_rejected(client, auth):
    response = post(client, auth, metadata_payload(), fields=[("note", "hello")])

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "UNEXPECTED_FIELD"


def test_exactly_ten_mebibyte_file_is_accepted(client, auth):
    payload = b"a" * MAX_FILE_BYTES
    response = post(client, auth, metadata_payload(), files=[("big.txt", payload, "text/plain")])

    assert response.status_code == 201


def test_oversized_file_is_rejected(client, auth):
    payload = b"a" * (MAX_FILE_BYTES + 1)
    response = post(client, auth, metadata_payload(), files=[("big.txt", payload, "text/plain")])

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "FILE_TOO_LARGE"


def test_empty_file_is_rejected(client, auth):
    response = post(client, auth, metadata_payload(), files=[("a.txt", b"", "text/plain")])

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "EMPTY_FILE"


def test_binary_file_is_rejected(client, auth):
    response = post(
        client,
        auth,
        metadata_payload(),
        files=[("a.bin", b"\xff\xfe\x00\x01binary", "application/octet-stream")],
    )

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "UNSUPPORTED_FILE_TYPE"


def test_broken_pdf_is_rejected(client, auth):
    response = post(
        client,
        auth,
        metadata_payload(),
        files=[("a.pdf", b"%PDF-1.7\n%%EOF\n", "application/pdf")],
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] in {"INVALID_PDF", "PDF_HAS_NO_TEXT"}


def test_non_multipart_request_is_rejected(client, auth):
    response = client.post(IMPORT_URL, headers=auth, json=metadata_payload())

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"


def test_version_conflict_returns_409_and_cleans_objects(client, auth, repository, object_store):
    repository.error = ApiProblem(409, "VERSION_CONFLICT", "该版本已存在。")

    response = post(
        client,
        auth,
        metadata_payload(),
        files=[("notice.txt", BODY_TEXT, "text/plain")],
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VERSION_CONFLICT"
    assert object_store.objects == {}
    assert len(object_store.deleted) == 1


def test_storage_outage_returns_503_and_cleans_objects(client, auth, repository, object_store):
    from app.errors import StorageUnavailable

    repository.error = StorageUnavailable("数据库连接不可用")

    response = post(
        client,
        auth,
        metadata_payload(),
        files=[("notice.txt", BODY_TEXT, "text/plain")],
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    assert object_store.objects == {}
