"""API-08/09/10：企业画像的创建、读取与单条事实更新（FR-03 / FR-07）。

重点不在「能存能取」，而在两个容易被做坏的约定：

* ``ETag`` 就是 ``factVersion``，写请求缺 If-Match 是 428、不是 400；版本已变是 409
  并带 ``currentVersion``，绝不静默覆盖别人的修改。
* ``value=null`` 是「未知」而不是 0；``"0"`` 是有效已知值；空串与 ``"null"`` 必须拒。
"""

from __future__ import annotations

from uuid import uuid4

import pytest

YEAR = {"startDate": "2026-01-01", "endDate": "2026-12-31"}


def fact(key: str, value, unit: str, period: dict | None = None) -> dict:
    return {
        "key": key,
        "value": value,
        "unit": unit,
        "period": period or YEAR,
        "source": "user_input",
    }


def payload(**overrides) -> dict:
    body = {
        "nameSynthetic": "合成企业A",
        "period": YEAR,
        "facts": [
            fact("region", "440300", "code"),
            fact("industry", "C39", "code"),
            fact("taxableIncome", None, "CNY"),
        ],
    }
    body.update(overrides)
    return body


def create(client, body: dict | None = None):
    return client.post("/api/v1/enterprises", json=body or payload())


def test_create_returns_201_with_etag_and_server_side_verified(client):
    response = create(client)

    assert response.status_code == 201
    body = response.json()
    assert body["factVersion"] == response.headers["ETag"].strip('"')
    assert len(body["factVersion"]) == 64
    assert all(item["verified"] is False for item in body["facts"])
    # null 表示未知，原样返回，不能变成 0 或被补齐。
    assert body["facts"][2]["value"] is None


@pytest.mark.parametrize(
    "facts, expected_field",
    [
        # 缺少非空 industry：画像不成立。
        ([fact("region", "440300", "code")], "facts"),
        # 同一 key+期间重复。
        (
            [
                fact("region", "440300", "code"),
                fact("region", "440300", "code"),
                fact("industry", "C39", "code"),
            ],
            "facts[1].period",
        ),
        # 单位必须与字典配套。
        ([fact("region", "440300", "code"), fact("industry", "C39", "CNY")], "facts[1].unit"),
        # 年度范围键的期间必须等于画像年度。
        (
            [
                fact("region", "440300", "code", {"startDate": "2026-01-01", "endDate": "2026-06-30"}),
                fact("industry", "C39", "code"),
            ],
            "facts[0].period",
        ),
        # 空串、字符串 "null"、前导零都不是合法数值。
        (
            [fact("region", "440300", "code"), fact("industry", "C39", "code"), fact("taxableIncome", "", "CNY")],
            "facts[2].value",
        ),
        (
            [
                fact("region", "440300", "code"),
                fact("industry", "C39", "code"),
                fact("taxableIncome", "null", "CNY"),
            ],
            "facts[2].value",
        ),
        (
            [
                fact("region", "440300", "code"),
                fact("industry", "C39", "code"),
                fact("taxableIncome", "007", "CNY"),
            ],
            "facts[2].value",
        ),
    ],
)
def test_invalid_profiles_are_rejected_with_field_errors(client, facts, expected_field):
    response = create(client, payload(facts=facts))

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    # 坏掉的那条事实被整条拒绝，因此 region/industry 的完整性会再报一次；这里只断言
    # 具体字段一定出现在 fieldErrors 里，由前端按字段回填提示。
    fields = [item["field"] for item in error["details"]["fieldErrors"]]
    assert expected_field in fields


def test_zero_is_a_known_value(client):
    response = create(
        client,
        payload(
            facts=[
                fact("region", "440300", "code"),
                fact("industry", "C39", "code"),
                fact("taxableIncome", "0", "CNY"),
            ]
        ),
    )

    assert response.status_code == 201
    # 金额统一补两位小数，但 0 仍然是「已知的 0」，与未知的 null 有本质差别。
    assert response.json()["facts"][2]["value"] == "0.00"


def test_client_cannot_claim_verified(client):
    body = payload()
    body["facts"][0]["verified"] = True

    response = create(client, body)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_read_returns_same_version_as_etag(client):
    created = create(client).json()

    response = client.get(f"/api/v1/enterprises/{created['id']}")

    assert response.status_code == 200
    assert response.json() == created
    assert response.headers["ETag"] == f'"{created["factVersion"]}"'


def test_read_unknown_enterprise_is_404(client):
    response = client.get(f"/api/v1/enterprises/{uuid4()}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ENTERPRISE_NOT_FOUND"


def patch(client, enterprise_id: str, key: str, body: dict, *, if_match=None):
    headers = {} if if_match is None else {"If-Match": if_match}
    return client.patch(f"/api/v1/enterprises/{enterprise_id}/facts/{key}", json=body, headers=headers)


def income_update(value="2800000.00") -> dict:
    return {"value": value, "unit": "CNY", "period": YEAR, "source": "user_input"}


def test_update_fact_returns_full_enterprise_and_new_etag(client):
    created = create(client).json()

    response = patch(
        client, created["id"], "taxableIncome", income_update(), if_match=f'"{created["factVersion"]}"'
    )

    assert response.status_code == 200
    updated = response.json()
    assert updated["factVersion"] != created["factVersion"]
    assert response.headers["ETag"] == f'"{updated["factVersion"]}"'
    income = next(item for item in updated["facts"] if item["key"] == "taxableIncome")
    assert income["value"] == "2800000.00"


def test_update_with_same_content_keeps_the_version(client):
    created = create(client).json()
    first = patch(
        client, created["id"], "taxableIncome", income_update(), if_match=f'"{created["factVersion"]}"'
    ).json()

    second = patch(
        client, created["id"], "taxableIncome", income_update(), if_match=f'"{first["factVersion"]}"'
    )

    assert second.status_code == 200
    assert second.json()["factVersion"] == first["factVersion"]


def test_null_clears_the_value_without_deleting_the_row(client):
    created = create(client).json()
    with_income = patch(
        client, created["id"], "taxableIncome", income_update(), if_match=f'"{created["factVersion"]}"'
    ).json()

    cleared = patch(
        client,
        created["id"],
        "taxableIncome",
        income_update(None),
        if_match=f'"{with_income["factVersion"]}"',
    )

    assert cleared.status_code == 200
    income = next(item for item in cleared.json()["facts"] if item["key"] == "taxableIncome")
    assert income["value"] is None


def test_missing_if_match_is_428(client):
    created = create(client).json()

    response = patch(client, created["id"], "taxableIncome", income_update())

    assert response.status_code == 428
    assert response.json()["error"]["code"] == "MISSING_IF_MATCH"


@pytest.mark.parametrize("if_match", ["*", 'W/"abc"', "not-a-version", '"abc"'])
def test_non_strong_or_malformed_if_match_is_400(client, if_match):
    created = create(client).json()

    response = patch(client, created["id"], "taxableIncome", income_update(), if_match=if_match)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_IF_MATCH"


def test_stale_if_match_is_409_with_current_version(client):
    created = create(client).json()
    moved = patch(
        client, created["id"], "taxableIncome", income_update(), if_match=f'"{created["factVersion"]}"'
    ).json()

    response = patch(
        client,
        created["id"],
        "taxableIncome",
        income_update("100"),
        if_match=f'"{created["factVersion"]}"',
    )

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "VERSION_CONFLICT"
    assert error["details"]["currentVersion"] == moved["factVersion"]


def test_update_unknown_enterprise_is_404(client):
    response = patch(
        client, str(uuid4()), "taxableIncome", income_update(), if_match=f'"{64 * "a"}"'
    )

    assert response.status_code == 404


def test_unknown_fact_key_in_path_is_400(client):
    created = create(client).json()

    response = patch(
        client, created["id"], "turnover", income_update(), if_match=f'"{created["factVersion"]}"'
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_QUERY"


def test_invalid_value_is_422_even_with_a_good_etag(client):
    created = create(client).json()

    response = patch(
        client,
        created["id"],
        "taxableIncome",
        income_update("not-a-number"),
        if_match=f'"{created["factVersion"]}"',
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# ------------------------------------------- rc.3 新增键的接口层口径（M2 复核 P1）


def test_negative_expense_increase_is_accepted_through_the_fact_endpoint(client):
    """研发费用增量允许为负：画像归一化曾只放行 taxableIncome，负值在接口层被拒。"""

    created = create(client).json()

    response = patch(
        client,
        created["id"],
        "rdExpenseIncrease",
        {"value": "-125000.50", "unit": "CNY", "period": YEAR, "source": "user_input"},
        if_match=f'"{created["factVersion"]}"',
    )

    assert response.status_code == 200, response.json()
    increase = next(item for item in response.json()["facts"] if item["key"] == "rdExpenseIncrease")
    assert increase["value"] == "-125000.50"

    # 负值必须真的进了版本：再读一次仍是同一个数、同一份 factVersion。
    fetched = client.get(f"/api/v1/enterprises/{created['id']}").json()
    assert fetched["factVersion"] == response.headers["ETag"].strip('"')
    assert next(item for item in fetched["facts"] if item["key"] == "rdExpenseIncrease")["value"] == "-125000.50"


def test_ratio_fact_round_trips_through_the_fact_endpoint(client):
    created = create(client).json()

    response = patch(
        client,
        created["id"],
        "rdExpenseRevenueRatio",
        {"value": "0.03", "unit": "ratio", "period": YEAR, "source": "user_input"},
        if_match=f'"{created["factVersion"]}"',
    )

    assert response.status_code == 200, response.json()
    ratio = next(item for item in response.json()["facts"] if item["key"] == "rdExpenseRevenueRatio")
    assert ratio["value"] == "0.03"
    assert ratio["unit"] == "ratio"


@pytest.mark.parametrize(
    ("key", "value", "unit"),
    [
        # 新增金额键必须和既有金额键一样只收两位小数。
        ("revenue", "1234.567", "CNY"),
        ("equityFinancing", "1234.567", "CNY"),
        # 比例不能为负——与 rules._validate_compare 的取值校验对齐。
        ("rdExpenseRevenueRatio", "-0.01", "ratio"),
    ],
)
def test_new_keys_are_validated_through_the_fact_endpoint(client, key, value, unit):
    created = create(client).json()

    response = patch(
        client,
        created["id"],
        key,
        {"value": value, "unit": unit, "period": YEAR, "source": "user_input"},
        if_match=f'"{created["factVersion"]}"',
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
