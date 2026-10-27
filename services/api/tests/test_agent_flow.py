"""在线链路端到端（FR-04/FR-05/FR-08）：画像 → 判断 → 读取。

验收锚点来自分工计划 10/28 一行：「行业排除→不满足」「缺事实→证据不足」。这里用
无数据库的假仓储跑完整 HTTP 流程，确认四态、缺失事实、补问、证据与轨迹都落在
同一次运行上，并且未被发布的政策不会「悄悄跳过」。
"""

from __future__ import annotations

import pytest

from support import published_draft

YEAR = {"startDate": "2026-01-01", "endDate": "2026-12-31"}

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

AS_OF = "2026-10-23T10:00:00+08:00"


def enterprise_payload(industry: str = "F51", income=None) -> dict:
    return {
        "nameSynthetic": "合成企业A",
        "period": YEAR,
        "facts": [
            {"key": "region", "value": "440300", "unit": "code", "period": YEAR, "source": "user_input"},
            {"key": "industry", "value": industry, "unit": "code", "period": YEAR, "source": "user_input"},
            {"key": "taxableIncome", "value": income, "unit": "CNY", "period": YEAR, "source": "user_input"},
        ],
    }


def create_enterprise(client, **kwargs) -> dict:
    response = client.post("/api/v1/enterprises", json=enterprise_payload(**kwargs))
    assert response.status_code == 201, response.text
    return response.json()


def create_run(client, enterprise_id: str, policy_ids: list[str], **extra) -> dict:
    body = {"enterpriseId": enterprise_id, "policyIds": policy_ids, "asOf": AS_OF, "period": YEAR}
    body.update(extra)
    response = client.post("/api/v1/assessment-runs", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_industry_exclusion_ends_in_not_satisfied(client, repository):
    policy = published_draft(expressions=[INDUSTRY_GATE])
    repository.drafts.append(policy)
    enterprise = create_enterprise(client)

    run = create_run(client, enterprise["id"], [str(policy.id)])

    assessment = run["assessments"][0]
    assert assessment["result"] == "not_satisfied"
    assert assessment["criteriaResults"][0]["result"] == "not_satisfied"
    assert assessment["missingFacts"] == []
    assert assessment["nextQuestion"] is None
    # 证据必须带可回溯的来源与原文片段，不能只有一句结论。
    evidence = assessment["criteriaResults"][0]["evidenceRefs"][0]
    assert evidence["sourceUrl"].startswith("https://www.sz.gov.cn/")
    assert evidence["quote"]
    assert len(evidence["textHash"]) == 64


def test_missing_fact_ends_in_insufficient_evidence_with_one_question(client, repository):
    policy = published_draft(expressions=[INCOME_GATE])
    repository.drafts.append(policy)
    enterprise = create_enterprise(client)  # taxableIncome 为 null＝未知

    run = create_run(client, enterprise["id"], [str(policy.id)])

    assessment = run["assessments"][0]
    assert assessment["result"] == "insufficient_evidence"
    assert assessment["missingFacts"] == [{"key": "taxableIncome", "period": YEAR}]
    question = assessment["nextQuestion"]
    assert question["factRef"] == {"key": "taxableIncome", "period": YEAR}
    assert question["rank"] == 1
    assert question["reason"]


def test_only_policies_missing_the_question_get_it(client, repository):
    """全局只有一问；受影响的政策才带 nextQuestion，其余为 null。"""

    income_policy = published_draft(expressions=[INCOME_GATE])
    industry_policy = published_draft(expressions=[INDUSTRY_GATE])
    repository.drafts.extend([income_policy, industry_policy])
    enterprise = create_enterprise(client)

    run = create_run(client, enterprise["id"], [str(income_policy.id), str(industry_policy.id)])

    by_policy = {item["policyId"]: item for item in run["assessments"]}
    assert by_policy[str(income_policy.id)]["nextQuestion"] is not None
    assert by_policy[str(industry_policy.id)]["nextQuestion"] is None


def test_skipped_fact_refs_remove_the_question_but_not_the_result(client, repository):
    policy = published_draft(expressions=[INCOME_GATE])
    repository.drafts.append(policy)
    enterprise = create_enterprise(client)

    run = create_run(
        client,
        enterprise["id"],
        [str(policy.id)],
        skippedFactRefs=[{"key": "taxableIncome", "period": YEAR}],
    )

    assessment = run["assessments"][0]
    assert assessment["nextQuestion"] is None
    # 跳过只是不再追问，事实仍未知，结论不能因此变成不满足。
    assert assessment["result"] == "insufficient_evidence"


def test_unpublished_policy_fails_the_whole_run(client, repository):
    draft = published_draft(expressions=[INDUSTRY_GATE])
    repository.drafts.append(draft.model_copy(update={"status": "pending_review"}))
    enterprise = create_enterprise(client)

    response = client.post(
        "/api/v1/assessment-runs",
        json={
            "enterpriseId": enterprise["id"],
            "policyIds": [str(draft.id)],
            "asOf": AS_OF,
            "period": YEAR,
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "POLICY_NOT_PUBLISHED"
    assert repository.saved_runs == []


def test_run_snapshots_trace_steps_and_two_runs_never_share_an_id(client, repository):
    policy = published_draft(expressions=[INDUSTRY_GATE])
    repository.drafts.append(policy)
    enterprise = create_enterprise(client)

    first = create_run(client, enterprise["id"], [str(policy.id)])
    second = create_run(client, enterprise["id"], [str(policy.id)])

    assert first["id"] != second["id"]
    saved = repository.saved_runs[0]
    assert [step.stage for step in saved["trace_steps"]] == [
        "profile",
        "retrieval",
        "evaluation",
        "questioning",
        "explanation",
    ]
    # 运行里保存的是所用政策版本的内部 ID，供读取时重算有效性。
    assert saved["policy_version_ids"][policy.id] is not None


def test_supersedes_must_match_enterprise_policies_and_period(client, repository):
    policy = published_draft(expressions=[INDUSTRY_GATE])
    repository.drafts.append(policy)
    enterprise = create_enterprise(client)
    old = create_run(client, enterprise["id"], [str(policy.id)])

    other_year = {"startDate": "2025-01-01", "endDate": "2025-12-31"}
    response = client.post(
        "/api/v1/assessment-runs",
        json={
            "enterpriseId": enterprise["id"],
            "policyIds": [str(policy.id)],
            "asOf": AS_OF,
            "period": other_year,
            "supersedesRunId": old["id"],
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_recompute_links_to_the_previous_run(client, repository):
    policy = published_draft(expressions=[INCOME_GATE])
    repository.drafts.append(policy)
    enterprise = create_enterprise(client)
    first = create_run(client, enterprise["id"], [str(policy.id)])

    second = create_run(client, enterprise["id"], [str(policy.id)], supersedesRunId=first["id"])

    assert second["id"] != first["id"]
    assert second["supersedesRunId"] == first["id"]


@pytest.mark.parametrize(
    "payload",
    [
        {"policyIds": []},
        {"period": {"startDate": "2026-12-31", "endDate": "2026-01-01"}},
        {"policyIds": ["dup"]},
    ],
)
def test_invalid_run_requests_are_rejected(client, repository, payload):
    policy = published_draft(expressions=[INDUSTRY_GATE])
    repository.drafts.append(policy)
    enterprise = create_enterprise(client)

    body = {"enterpriseId": enterprise["id"], "policyIds": [str(policy.id)], "asOf": AS_OF, "period": YEAR}
    body.update(payload)
    if payload.get("policyIds") == ["dup"]:
        body["policyIds"] = [str(policy.id), str(policy.id)]

    response = client.post("/api/v1/assessment-runs", json=body)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_read_run_round_trips_the_saved_snapshot(client, repository):
    policy = published_draft(expressions=[INDUSTRY_GATE])
    repository.drafts.append(policy)
    enterprise = create_enterprise(client)
    run = create_run(client, enterprise["id"], [str(policy.id)])

    response = client.get(f"/api/v1/assessment-runs/{run['id']}")

    assert response.status_code == 200
    assert response.json() == run


@pytest.mark.parametrize(
    "run_id, status",
    [("00000000-0000-4000-8000-000000000000", 404), ("not-a-uuid", 400)],
)
def test_read_run_errors(client, run_id, status):
    response = client.get(f"/api/v1/assessment-runs/{run_id}")

    assert response.status_code == status
