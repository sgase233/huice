"""API-02/03：公开政策列表与详情（FR-04）。

公开读取只含已审核发布的版本；查询参数按契约走 400，未发布/不存在的版本一律
404 且不回退。``asOf`` 只是元数据过滤，并且必须按北京时间换算成日期。
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest

from app.schemas import PolicyImportMetadata
from support import build_draft, metadata_payload, published_draft


def add_draft(repository, **overrides):
    metadata = PolicyImportMetadata.model_validate(metadata_payload(**overrides))
    draft = build_draft(metadata, [])
    repository.drafts.append(draft)
    return draft


def test_list_returns_only_reviewed_versions(client, repository):
    published = published_draft()
    add_draft(repository, version="v2.0")  # pending_parse
    repository.drafts.append(published)

    response = client.get("/api/v1/policies")

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["items"]] == [str(published.id)]
    assert body["items"][0]["status"] == "published"
    assert body["nextCursor"] is None


def test_list_forwards_every_filter(client, repository):
    repository.drafts.append(published_draft(policy_type="tax"))

    client.get(
        "/api/v1/policies",
        params={"status": "closed", "type": "tax", "region": "440300", "limit": 5},
    )

    call = repository.public_list_calls[-1]
    assert call["status"] == "closed"
    assert call["policy_type"] == "tax"
    assert call["region"] == "440300"
    assert call["limit"] == 5


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("2026-10-23T10:00:00+08:00", date(2026, 10, 23)),
        # UTC 的 22 日 16:30 已是北京时间 23 日 00:30，按日期过滤必须换算。
        ("2026-10-22T16:30:00Z", date(2026, 10, 23)),
    ],
)
def test_as_of_is_converted_to_beijing_date(client, repository, raw, expected):
    client.get("/api/v1/policies", params={"asOf": raw})

    assert repository.public_list_calls[-1]["as_of"] == expected


@pytest.mark.parametrize(
    "params",
    [
        {"status": "pending_review"},
        {"type": "subsidy"},
        {"region": ""},
        {"limit": 0},
        {"limit": 51},
        {"asOf": "2026-10-23T10:00:00"},  # 无时区
    ],
)
def test_invalid_query_is_400(client, params):
    response = client.get("/api/v1/policies", params=params)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_QUERY"


def test_detail_returns_published_version(client, repository):
    policy = published_draft(expressions=[{"op": "compare", "factKey": "taxableIncome", "period": "assessment", "comparator": "gte", "value": "1", "unit": "CNY"}])
    repository.drafts.append(policy)

    response = client.get(f"/api/v1/policies/{policy.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(policy.id)
    assert body["criteria"][0]["validationStatus"] == "passed"
    assert body["preparationTemplate"]["handlingMode"] == "grant_application"


def test_detail_never_falls_back_to_an_unpublished_version(client, repository):
    policy = published_draft()
    repository.drafts.append(policy)

    missing_version = client.get(f"/api/v1/policies/{policy.id}", params={"version": "v9.9"})
    unpublished = client.get(f"/api/v1/policies/{uuid4()}")

    assert missing_version.status_code == 404
    assert missing_version.json()["error"]["code"] == "POLICY_NOT_FOUND"
    assert unpublished.status_code == 404


def test_detail_of_a_pending_policy_is_404(client, repository):
    draft = add_draft(repository)

    response = client.get(f"/api/v1/policies/{draft.id}")

    assert response.status_code == 404


def test_non_uuid_policy_id_is_400(client):
    response = client.get("/api/v1/policies/not-a-uuid")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_QUERY"
