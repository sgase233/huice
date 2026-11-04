"""在线链路端到端（FR-04/FR-05/FR-08）：画像 → 判断 → 读取。

验收锚点来自分工计划 10/28 一行：「行业排除→不满足」「缺事实→证据不足」。这里用
无数据库的假仓储跑完整 HTTP 流程，确认四态、缺失事实、补问、证据与轨迹都落在
同一次运行上，并且未被发布的政策不会「悄悄跳过」。
"""

from __future__ import annotations

import logging

import pytest

from app.errors import ApiProblem
from app.integrations.llm import LlmResult
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


def test_decided_policy_reports_no_missing_facts_even_with_an_unknown_criterion(client, repository):
    """政策已判不满足时，另一条条件缺的事实不挡结论，也不该进 missingFacts 或补问。"""

    policy = published_draft(expressions=[INDUSTRY_GATE, INCOME_GATE])
    repository.drafts.append(policy)
    enterprise = create_enterprise(client)  # industry=F51 已排除；taxableIncome 未知

    run = create_run(client, enterprise["id"], [str(policy.id)])

    assessment = run["assessments"][0]
    assert assessment["result"] == "not_satisfied"
    assert assessment["missingFacts"] == []
    assert assessment["nextQuestion"] is None
    # 条件级仍如实标出自己的未知事实，只是不再冒充「挡住结论」的政策级缺失。
    assert assessment["criteriaResults"][1]["result"] == "insufficient_evidence"


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


class _CountingLlm:
    """真实模型分支的最小替身：只回一句改写，用量固定，便于断言轨迹与日志口径。"""

    mode = "deepseek"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, *, system: str, user: str, max_tokens: int = 1024) -> LlmResult:
        self.calls += 1
        return LlmResult(
            text="模型改写后的说明。",
            model="deepseek-chat",
            prompt_tokens=11,
            completion_tokens=22,
            elapsed_ms=8,
        )


def test_explanation_trace_records_model_tokens_once_per_call(app, client, repository):
    policy = published_draft(expressions=[INDUSTRY_GATE])
    repository.drafts.append(policy)
    llm = _CountingLlm()
    app.state.llm = llm
    enterprise = create_enterprise(client)

    create_run(client, enterprise["id"], [str(policy.id)])

    steps = repository.saved_runs[0]["trace_steps"]
    explanation = [step for step in steps if step.stage == "explanation"][0]
    assert llm.calls == 1
    # 用量只进轨迹摘要，不进契约响应：TraceStep 没有 token 字段。
    assert "模型 tokens 入 11 出 22" in explanation.output_summary
    assert explanation.output_summary.endswith(f"{policy.id}=deepseek")


class _FailingLlm:
    """解释用模型直接失败：判断照常返回，但这次调用要留一行、用量记未知。"""

    mode = "deepseek"
    model_name = "deepseek-chat"

    def complete(self, *, system: str, user: str, max_tokens: int = 1024) -> LlmResult:
        raise ApiProblem(
            503, "DEPENDENCY_UNAVAILABLE", "模型服务暂不可用。",
            retryable=True, usage_unknown=True,
        )


def test_a_failed_explanation_call_is_logged_with_unknown_usage(
    app, client, repository, caplog
):
    caplog.set_level(logging.INFO)
    policy = published_draft(expressions=[INDUSTRY_GATE])
    repository.drafts.append(policy)
    app.state.llm = _FailingLlm()
    enterprise = create_enterprise(client)

    body = create_run(client, enterprise["id"], [str(policy.id)])

    # 模型失败不影响判断：结论与降级警告照常返回。
    assert body["assessments"][0]["explanationMode"] == "saved"
    line = [r.getMessage() for r in caplog.records if r.getMessage().startswith("model_call")][-1]
    # 失败也要有一行：模型可能已被调用并计费，不能因为降级就从记录里消失。
    assert "outcome=error" in line and "usage=unknown" in line
    explanation = [
        step for step in repository.saved_runs[0]["trace_steps"] if step.stage == "explanation"
    ][0]
    # 未知用量单列，不能与已知 tokens 相加。
    assert "1 次调用失败、用量未知" in explanation.output_summary
    assert "tokens" not in explanation.output_summary


def test_explanation_trace_has_no_token_note_when_the_model_is_not_used(client, repository):
    policy = published_draft(expressions=[INDUSTRY_GATE])
    repository.drafts.append(policy)
    enterprise = create_enterprise(client)

    create_run(client, enterprise["id"], [str(policy.id)])

    explanation = [
        step for step in repository.saved_runs[0]["trace_steps"] if step.stage == "explanation"
    ][0]
    assert "tokens" not in explanation.output_summary
    assert explanation.output_summary.endswith(f"{policy.id}=fixture")


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
