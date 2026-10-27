"""API-13 报告与 API-14 轨迹（假仓储）：只读同一份历史，不重新求值。"""

from __future__ import annotations

import pytest

from support import ADMIN_TOKEN, published_draft

YEAR = {"startDate": "2026-01-01", "endDate": "2026-12-31"}
AS_OF = "2026-10-23T10:00:00+08:00"

INDUSTRY_GATE = {
    "op": "industry_exclusion",
    "factKey": "industry",
    "period": "assessment",
    "excluded": ["F51", "F52"],
}

INCOME_GATE = {
    "op": "compare",
    "factKey": "taxableIncome",
    "period": "assessment",
    "comparator": "gte",
    "value": "3000000.00",
    "unit": "CNY",
}


def enterprise(client, industry: str = "F51", income=None) -> dict:
    response = client.post(
        "/api/v1/enterprises",
        json={
            "nameSynthetic": "合成企业A",
            "period": YEAR,
            "facts": [
                {"key": "region", "value": "440300", "unit": "code", "period": YEAR, "source": "user_input"},
                {"key": "industry", "value": industry, "unit": "code", "period": YEAR, "source": "user_input"},
                {"key": "taxableIncome", "value": income, "unit": "CNY", "period": YEAR, "source": "user_input"},
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def run(client, enterprise_id: str, policy_ids: list[str], **extra) -> dict:
    body = {"enterpriseId": enterprise_id, "policyIds": policy_ids, "asOf": AS_OF, "period": YEAR}
    body.update(extra)
    response = client.post("/api/v1/assessment-runs", json=body)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture()
def missing_income_run(client, repository):
    policy = published_draft(expressions=[INCOME_GATE])
    repository.drafts.append(policy)
    return run(client, enterprise(client)["id"], [str(policy.id)])


# ------------------------------------------------------------------ API-13


def test_report_is_markdown_with_a_safe_attachment_name(client, missing_income_run):
    response = client.get(f"/api/v1/assessment-runs/{missing_income_run['id']}/report.md")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.headers["content-disposition"] == (
        f'attachment; filename="assessment-{missing_income_run["id"]}.md"'
    )
    assert response.text.startswith("# 惠策协同：政策机会分析报告")
    assert "非税务专业意见" in response.text


def test_report_carries_the_run_identity_and_conclusion(client, missing_income_run):
    body = client.get(f"/api/v1/assessment-runs/{missing_income_run['id']}/report.md").text

    assert missing_income_run["id"] in body
    assert missing_income_run["factVersion"] in body
    assert missing_income_run["ruleVersion"] in body
    assert "证据不足" in body
    assert "taxableIncome" in body
    # 补问建议来自 questioning 的 rank=1 结果，报告只转写、不重排。
    assert "应纳税所得额" in body


def test_report_states_validity_as_of_download_time(client, missing_income_run):
    body = client.get(f"/api/v1/assessment-runs/{missing_income_run['id']}/report.md").text

    assert "截至下载时刻的有效性" in body
    assert "仍按当前规则有效" in body
    assert "下载时间" in body


def test_report_separates_tax_and_funding_lists(client, repository):
    tax = published_draft(expressions=[INDUSTRY_GATE], type="tax")
    grant = published_draft(expressions=[INDUSTRY_GATE], type="grant")
    repository.drafts.extend([tax, grant])
    created = run(client, enterprise(client)["id"], [str(tax.id), str(grant.id)])

    body = client.get(f"/api/v1/assessment-runs/{created['id']}/report.md").text

    assert "## 税收政策" in body
    assert "## 资金政策" in body
    assert body.index("## 税收政策") < body.index("## 资金政策")


def test_report_unknown_run_is_404_and_bad_id_is_400(client):
    assert client.get("/api/v1/assessment-runs/00000000-0000-4000-8000-000000000000/report.md").status_code == 404
    assert client.get("/api/v1/assessment-runs/not-a-uuid/report.md").status_code == 400


def test_report_errors_stay_json(client, missing_income_run):
    response = client.get("/api/v1/assessment-runs/not-a-uuid/report.md")

    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"]["code"] == "INVALID_QUERY"


# ------------------------------------------------------------------ API-14


def test_trace_requires_admin_bearer(client, missing_income_run):
    url = f"/api/v1/assessment-runs/{missing_income_run['id']}/trace"

    anonymous = client.get(url)
    assert anonymous.status_code == 401
    assert anonymous.headers["www-authenticate"] == "Bearer"

    wrong = client.get(url, headers={"Authorization": "Bearer wrong-token-000000"})
    assert wrong.status_code == 401


def test_trace_lists_the_online_stages_in_order(client, auth, missing_income_run):
    response = client.get(
        f"/api/v1/assessment-runs/{missing_income_run['id']}/trace", headers=auth
    )

    assert response.status_code == 200
    steps = response.json()["steps"]
    assert [step["stage"] for step in steps] == [
        "profile",
        "retrieval",
        "evaluation",
        "questioning",
        "explanation",
    ]
    assert [step["sequence"] for step in steps] == [1, 2, 3, 4, 5]
    assert all(step["status"] == "ok" for step in steps)
    assert steps[0]["inputSummary"] and steps[0]["outputSummary"]


def test_trace_steps_carry_no_secrets_or_enterprise_raw_text(client, auth, missing_income_run):
    body = client.get(
        f"/api/v1/assessment-runs/{missing_income_run['id']}/trace", headers=auth
    ).text

    assert ADMIN_TOKEN not in body
    assert "postgresql://" not in body
    # 脱敏：只允许结构化引用，不放原文片段。
    steps = __import__("json").loads(body)["steps"]
    assert all(isinstance(ref, dict) for step in steps for ref in step["evidenceRefs"])


def test_trace_unknown_run_is_404(client, auth):
    missing = client.get(
        "/api/v1/assessment-runs/00000000-0000-4000-8000-000000000000/trace", headers=auth
    )

    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "RUN_NOT_FOUND"
