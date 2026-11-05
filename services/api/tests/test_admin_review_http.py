"""后台审核流程 API-05/06/07：抽取幂等、引用校验、人工发布门禁。"""

from __future__ import annotations

import hashlib
import json
import logging
from uuid import uuid4

import pytest

from app.agents.ingestion import PreparedSource
from app.errors import ApiProblem
from app.integrations.llm import LlmResult
from app.schemas import PolicyImportMetadata
from support import build_draft, metadata_payload

BODY_TEXT = (
    "第一条 在本市登记的中小企业，年应纳税所得额不超过300万元的，减按25%计入应纳税所得额。\n"
    "第二条 本通知不适用于批发和零售业等限制类行业。"
)
QUOTE = "第一条 在本市登记的中小企业，年应纳税所得额不超过300万元的，减按25%计入应纳税所得额。"

THRESHOLD_EXPRESSION = {
    "op": "compare",
    "factKey": "taxableIncome",
    "period": "assessment",
    "comparator": "lte",
    "value": "3000000",
    "unit": "CNY",
}


def quote_hash(quote: str) -> str:
    return hashlib.sha256(quote.encode("utf-8")).hexdigest()


def seed_draft(repository, *, text: str = BODY_TEXT):
    source = PreparedSource(
        kind="body",
        file_name="policy.txt",
        mime_type="text/plain",
        text=text,
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        byte_size=len(text.encode("utf-8")),
    )
    metadata = PolicyImportMetadata.model_validate(metadata_payload())
    draft = build_draft(metadata, [source])
    repository.drafts.append(draft)
    return draft


def criterion_payload(draft, *, quote: str = QUOTE, expression=None, review_finding: str = "none"):
    source = draft.sources[0]
    return {
        "id": str(uuid4()),
        "title": "合成演示阈值",
        "expression": expression or THRESHOLD_EXPRESSION,
        "sourceSpans": [
            {
                "sourceId": str(source.id),
                "sourceUrl": source.source_url,
                "sourceVersion": draft.version,
                "page": None,
                "section": "第一条",
                "quote": quote,
                "textHash": quote_hash(quote),
            }
        ],
        "reviewFinding": review_finding,
    }


def headers(auth, revision, *, key=None) -> dict[str, str]:
    values = {**auth, "If-Match": f'"{revision}"'}
    if key is not None:
        values["Idempotency-Key"] = str(key)
    return values


def save_criteria(client, auth, draft, criteria, *, revision=None):
    return client.put(
        f"/api/v1/admin/policies/{draft.id}/criteria",
        headers=headers(auth, revision or draft.revision),
        json={"version": draft.version, "criteria": criteria},
    )


# ------------------------------------------------------------------ API-05


def test_extract_requires_if_match(client, auth, repository):
    draft = seed_draft(repository)
    response = client.post(
        f"/api/v1/admin/policies/{draft.id}/extract",
        headers={**auth, "Idempotency-Key": str(uuid4())},
        json={"version": draft.version},
    )
    assert response.status_code == 428
    assert response.json()["error"]["code"] == "MISSING_IF_MATCH"


def test_extract_requires_idempotency_key(client, auth, repository):
    draft = seed_draft(repository)
    response = client.post(
        f"/api/v1/admin/policies/{draft.id}/extract",
        headers=headers(auth, draft.revision),
        json={"version": draft.version},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "MISSING_IDEMPOTENCY_KEY"


def test_extract_rejects_weak_or_wildcard_if_match(client, auth, repository):
    draft = seed_draft(repository)
    for value in ("*", f'W/"{draft.revision}"'):
        response = client.post(
            f"/api/v1/admin/policies/{draft.id}/extract",
            headers={**auth, "If-Match": value, "Idempotency-Key": str(uuid4())},
            json={"version": draft.version},
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "INVALID_IF_MATCH"


def test_extract_moves_draft_to_pending_review_with_cited_criteria(client, auth, repository):
    draft = seed_draft(repository)
    response = client.post(
        f"/api/v1/admin/policies/{draft.id}/extract",
        headers=headers(auth, draft.revision, key=uuid4()),
        json={"version": draft.version},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["draft"]["status"] == "pending_review"
    assert body["usage"]["mode"] == "fixture"
    assert len(body["criteriaDrafts"]) == 1
    criterion = body["criteriaDrafts"][0]
    assert criterion["validationStatus"] == "passed"
    assert criterion["expression"]["comparator"] == "lte"
    assert criterion["expression"]["value"] == "3000000"
    assert criterion["sourceSpans"][0]["quote"] == QUOTE
    assert response.headers["etag"] == f'"{body["draft"]["revision"]}"'
    assert body["draft"]["revision"] != str(draft.revision)


def test_extract_is_idempotent_for_the_same_key_and_body(client, auth, repository):
    draft = seed_draft(repository)
    key = uuid4()
    first = client.post(
        f"/api/v1/admin/policies/{draft.id}/extract",
        headers=headers(auth, draft.revision, key=key),
        json={"version": draft.version},
    )
    second = client.post(
        f"/api/v1/admin/policies/{draft.id}/extract",
        headers=headers(auth, first.json()["draft"]["revision"], key=key),
        json={"version": draft.version},
    )

    assert second.status_code == 200
    assert second.json() == first.json()
    assert len(repository.saved_criteria) == 1  # 没有重复落库、没有重复计费
    # 重放没有发生新的模型调用，因此也不该再写一条抽取轨迹。
    assert len(repository.saved_extractions) == 1


def test_extract_records_the_extraction_step_for_the_trace(client, auth, repository):
    draft = seed_draft(repository)
    body = client.post(
        f"/api/v1/admin/policies/{draft.id}/extract",
        headers=headers(auth, draft.revision, key=uuid4()),
        json={"version": draft.version},
    ).json()

    recorded = repository.saved_extractions[-1]
    assert recorded["model"] == body["usage"]["modelVersion"]
    assert recorded["input_chars"] == len(BODY_TEXT)
    assert recorded["elapsed_ms"] >= 0


class _ModelExtractionLlm:
    """真实模型抽取分支的替身：回一条带原文引文的条款，用量固定。"""

    mode = "deepseek"

    def complete(self, *, system: str, user: str, max_tokens: int = 1024) -> LlmResult:
        item = {
            "title": "合成演示阈值",
            "expression": THRESHOLD_EXPRESSION,
            "quote": QUOTE,
            "section": "第一条",
        }
        return LlmResult(
            text=json.dumps([item], ensure_ascii=False),
            model="deepseek-chat",
            prompt_tokens=120,
            completion_tokens=45,
            elapsed_ms=300,
        )


def test_a_real_model_extraction_logs_its_usage_once(app, client, auth, repository, caplog):
    caplog.set_level(logging.INFO)
    draft = seed_draft(repository)
    app.state.llm = _ModelExtractionLlm()

    client.post(
        f"/api/v1/admin/policies/{draft.id}/extract",
        headers=headers(auth, draft.revision, key=uuid4()),
        json={"version": draft.version},
    )

    lines = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("model_call purpose=extraction")
    ]
    assert len(lines) == 1
    assert "model=deepseek-chat" in lines[0]
    assert "prompt=120" in lines[0] and "completion=45" in lines[0]
    assert str(draft.id) in lines[0]
    # 只记用量与对象标识：不写提示词、正文与密钥。
    assert QUOTE not in lines[0]
    # 轨迹里记的是模型名，与日志里的 model= 对得上。
    assert repository.saved_extractions[-1]["model"] == "deepseek-chat"


def test_a_rule_based_extraction_is_not_logged_as_a_model_call(app, client, auth, repository, caplog):
    caplog.set_level(logging.INFO)
    draft = seed_draft(repository)

    client.post(
        f"/api/v1/admin/policies/{draft.id}/extract",
        headers=headers(auth, draft.revision, key=uuid4()),
        json={"version": draft.version},
    )

    assert "model_call purpose=extraction" not in caplog.text
    # 规则抽取仍然是一次真实的抽取：轨迹与用量照记，只是不算模型开销。
    assert repository.saved_extractions[-1]["model"]


class _FailingExtractionLlm:
    """抽取时直接失败：``problem`` 决定是限流、超时还是上游 4xx。"""

    mode = "deepseek"
    model_name = "deepseek-chat"

    def __init__(self, problem) -> None:
        self._problem = problem

    def complete(self, *, system: str, user: str, max_tokens: int = 1024) -> LlmResult:
        raise self._problem


def _failing_llm(*, retryable: bool, usage_unknown: bool, status_code: int, code: str):
    return _FailingExtractionLlm(
        ApiProblem(
            status_code,
            code,
            "模型服务暂不可用，已改用规则结果与已存说明。",
            retryable=retryable,
            usage_unknown=usage_unknown,
        )
    )


def _extract(client, auth, draft):
    return client.post(
        f"/api/v1/admin/policies/{draft.id}/extract",
        headers=headers(auth, draft.revision, key=uuid4()),
        json={"version": draft.version},
    )


def test_a_rate_limited_extraction_is_recorded_as_retryable(app, client, auth, repository, caplog):
    caplog.set_level(logging.INFO)
    draft = seed_draft(repository)
    app.state.llm = _failing_llm(
        retryable=True, usage_unknown=True, status_code=429, code="UPSTREAM_RATE_LIMITED"
    )

    response = _extract(client, auth, draft)

    # 响应形状不变：限流仍是 429，不因为要留痕就改成 503。
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "UPSTREAM_RATE_LIMITED"
    failed = repository.failed_extractions[-1]
    assert failed["status"] == "retryable_error"
    assert failed["error_code"] == "UPSTREAM_RATE_LIMITED"
    # 429 也没有「未计费」的证据：用量记未知，不能混进确定的 0。
    assert failed["usage_unknown"] is True
    line = [r.getMessage() for r in caplog.records if r.getMessage().startswith("model_call")][-1]
    assert "outcome=error" in line and "usage=unknown" in line
    assert "prompt=unknown" in line and "completion=unknown" in line


def test_a_timeout_during_extraction_is_retryable_with_unknown_usage(app, client, auth, repository, caplog):
    caplog.set_level(logging.INFO)
    draft = seed_draft(repository)
    app.state.llm = _failing_llm(
        retryable=True, usage_unknown=True, status_code=503, code="DEPENDENCY_UNAVAILABLE"
    )

    response = _extract(client, auth, draft)

    assert response.status_code == 503
    failed = repository.failed_extractions[-1]
    assert failed["status"] == "retryable_error"
    # 超时时上游可能已处理并计费：用量登记为未知，不能当 0。
    assert failed["usage_unknown"] is True
    line = [r.getMessage() for r in caplog.records if r.getMessage().startswith("model_call")][-1]
    assert "usage=unknown" in line and "prompt=unknown" in line


def test_an_upstream_client_error_is_fatal_and_not_retried(app, client, auth, repository):
    draft = seed_draft(repository)
    app.state.llm = _failing_llm(
        retryable=False, usage_unknown=True, status_code=503, code="DEPENDENCY_UNAVAILABLE"
    )

    response = _extract(client, auth, draft)

    assert response.status_code == 503
    failed = repository.failed_extractions[-1]
    # 密钥/模型名写错这类失败重试也不会好，记 fatal_error 让人直接去查配置；
    # 但「不该重试」不等于「上游没处理」，用量仍记未知。
    assert failed["status"] == "fatal_error"
    assert failed["usage_unknown"] is True


def test_a_failed_extraction_leaves_the_draft_untouched(app, client, auth, repository):
    draft = seed_draft(repository)
    app.state.llm = _failing_llm(
        retryable=True, usage_unknown=True, status_code=503, code="DEPENDENCY_UNAVAILABLE"
    )

    _extract(client, auth, draft)

    # 失败留痕是独立事务：草稿内容、状态与 revision 都不变，也没有半条条款落库。
    assert repository.saved_criteria == []
    assert repository.get_version_for_edit(draft.id, draft.version).revision == draft.revision


def test_a_call_that_then_loses_the_save_stays_on_the_call_record(
    app, client, auth, repository, caplog
):
    caplog.set_level(logging.INFO)
    draft = seed_draft(repository)
    app.state.llm = _ModelExtractionLlm()

    response = client.post(
        f"/api/v1/admin/policies/{draft.id}/extract",
        headers=headers(auth, uuid4(), key=uuid4()),
        json={"version": draft.version},
    )

    assert response.status_code == 409
    assert repository.saved_criteria == []
    # 草稿保存被 409 挡下、抽取轨迹随事务回滚，但这次调用已经发生并计费：
    # 调用记录独立于草稿保存事务，必须留在日志里可核对。
    lines = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("model_call")
    ]
    assert len(lines) == 1
    assert "outcome=ok" in lines[0] and "prompt=120" in lines[0]
    # 调用本身没失败，只是没保存成功，不该记一条失败步骤。
    assert repository.failed_extractions == []


def test_manual_save_does_not_claim_an_extraction(client, auth, repository):
    draft = seed_draft(repository)
    response = save_criteria(client, auth, draft, [criterion_payload(draft)])

    assert response.status_code == 200
    # 手工保存条款没有调用模型，不能凭空写一条抽取轨迹。
    assert repository.saved_extractions[-1]["model"] is None


def test_extract_rejects_same_key_with_different_body(client, auth, repository):
    draft = seed_draft(repository)
    key = uuid4()
    client.post(
        f"/api/v1/admin/policies/{draft.id}/extract",
        headers=headers(auth, draft.revision, key=key),
        json={"version": draft.version},
    )
    response = client.post(
        f"/api/v1/admin/policies/{draft.id}/extract",
        headers=headers(auth, draft.revision, key=key),
        json={"version": draft.version, "modelVersion": "deepseek-chat"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_extract_with_stale_revision_conflicts(client, auth, repository):
    draft = seed_draft(repository)
    response = client.post(
        f"/api/v1/admin/policies/{draft.id}/extract",
        headers=headers(auth, uuid4(), key=uuid4()),
        json={"version": draft.version},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VERSION_CONFLICT"
    assert response.json()["error"]["details"]["currentVersion"] == str(draft.revision)


def test_extract_on_unknown_version_is_404(client, auth, repository):
    draft = seed_draft(repository)
    response = client.post(
        f"/api/v1/admin/policies/{draft.id}/extract",
        headers=headers(auth, draft.revision, key=uuid4()),
        json={"version": "v9.9"},
    )
    assert response.status_code == 404


# ------------------------------------------------------------------ API-06


def test_save_criteria_requires_if_match(client, auth, repository):
    draft = seed_draft(repository)
    response = client.put(
        f"/api/v1/admin/policies/{draft.id}/criteria",
        headers=auth,
        json={"version": draft.version, "criteria": [criterion_payload(draft)]},
    )
    assert response.status_code == 428


def test_save_criteria_keeps_unsupported_draft_and_returns_findings(client, auth, repository):
    draft = seed_draft(repository)
    bad_quote = "第一条 在本市登记的中小企业，年应纳税所得额不超过500万元的。"
    response = save_criteria(client, auth, draft, [criterion_payload(draft, quote=bad_quote)])

    assert response.status_code == 200
    body = response.json()
    assert body["criteria"][0]["validationStatus"] == "failed"
    codes = {item["code"] for item in body["validationFindings"]}
    assert "SPAN_QUOTE_NOT_FOUND" in codes


def test_save_criteria_rejects_numeric_direction_mismatch(client, auth, repository):
    """FR-02 验收：把「不超过」抽成「不低于」，保存能过但校验失败，发布被拦。"""
    draft = seed_draft(repository)
    wrong = {**THRESHOLD_EXPRESSION, "comparator": "gte"}
    response = save_criteria(client, auth, draft, [criterion_payload(draft, expression=wrong)])

    assert response.status_code == 200
    body = response.json()
    assert body["criteria"][0]["validationStatus"] == "failed"
    assert "CITATION_THRESHOLD_MISMATCH" in {item["code"] for item in body["validationFindings"]}

    blocked = client.post(
        f"/api/v1/admin/policies/{draft.id}/publish",
        headers=headers(auth, body["draft"]["revision"]),
        json={"version": draft.version, "reviewConfirmed": True},
    )
    assert blocked.status_code == 422
    assert blocked.json()["error"]["code"] == "CRITERIA_NOT_VALIDATED"


def test_save_criteria_rejects_duplicate_ids(client, auth, repository):
    draft = seed_draft(repository)
    item = criterion_payload(draft)
    response = save_criteria(client, auth, draft, [item, {**item}])
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "DUPLICATE_CRITERION_ID"


def test_save_criteria_requires_at_least_one(client, auth, repository):
    draft = seed_draft(repository)
    response = save_criteria(client, auth, draft, [])
    assert response.status_code == 422


def test_save_criteria_marks_invalid_ast_as_failed(client, auth, repository):
    draft = seed_draft(repository)
    broken = {"op": "sum", "children": []}
    response = save_criteria(client, auth, draft, [criterion_payload(draft, expression=broken)])
    assert response.status_code == 200
    assert response.json()["criteria"][0]["validationStatus"] == "failed"
    assert "EXPRESSION_OP_UNKNOWN" in {item["code"] for item in response.json()["validationFindings"]}


# ------------------------------------- 政策级「合取为空」（criterionId 为空）

CONTRADICTION_BODY = (
    "第一条 上一年度营业收入不低于1000万元的企业，按研发投入的20%给予补助。" "\n"
    "第二条 上一年度营业收入低于1000万元的企业，按股权融资额的5%给予补助。"
)
CONTRADICTION_QUOTES = (
    "第一条 上一年度营业收入不低于1000万元的企业，按研发投入的20%给予补助。",
    "第二条 上一年度营业收入低于1000万元的企业，按股权融资额的5%给予补助。",
)


MERGED_BODY = (
    "上一年度营业收入低于500万元的企业，按研发投入的20%给予补助；"
    "上一年度营业收入不低于1000万元的企业，按研发投入的30%给予补助。"
)
MERGED_QUOTE = MERGED_BODY


def revenue_expression(comparator: str) -> dict:
    return {
        "op": "compare",
        "factKey": "revenue",
        "period": "assessment",
        "comparator": comparator,
        "value": "10000000",
        "unit": "CNY",
    }


def test_save_criteria_reports_a_policy_level_unsatisfiable_finding(client, auth, repository):
    """两条条件各自合法，合起来却永远不成立——只能作为政策级 finding 报出来。

    对应 M3 的 SRDI-004/005：`revenue>=1000万` 与 `revenue<1000万` 并列。
    """

    draft = seed_draft(repository, text=CONTRADICTION_BODY)
    response = save_criteria(
        client,
        auth,
        draft,
        [
            criterion_payload(draft, quote=CONTRADICTION_QUOTES[0],
                              expression=revenue_expression("gte")),
            criterion_payload(draft, quote=CONTRADICTION_QUOTES[1],
                              expression=revenue_expression("lt")),
        ],
    )

    assert response.status_code == 200
    body = response.json()
    # 两个条款本身都没问题，卡住发布的只有那一条政策级 finding。
    assert [item["validationStatus"] for item in body["criteria"]] == ["passed", "passed"]
    policy_findings = [
        item for item in body["validationFindings"] if item["criterionId"] is None
    ]
    assert [item["code"] for item in policy_findings] == ["POLICY_UNSATISFIABLE"]
    assert "revenue" in policy_findings[0]["message"]
    assert policy_findings[0]["sourceSpans"], "政策级 finding 也要带两边的引用"


def test_publish_is_blocked_while_a_policy_level_finding_remains(client, auth, repository):
    draft = seed_draft(repository, text=CONTRADICTION_BODY)
    saved = save_criteria(
        client,
        auth,
        draft,
        [
            criterion_payload(draft, quote=CONTRADICTION_QUOTES[0],
                              expression=revenue_expression("gte")),
            criterion_payload(draft, quote=CONTRADICTION_QUOTES[1],
                              expression=revenue_expression("lt")),
        ],
    ).json()

    response = client.post(
        f"/api/v1/admin/policies/{draft.id}/publish",
        headers=headers(auth, saved["draft"]["revision"]),
        json={"version": draft.version, "reviewConfirmed": True},
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "CRITERIA_NOT_VALIDATED"
    assert "互斥" in error["message"]
    assert [item["code"] for item in error["details"]["findings"]] == ["POLICY_UNSATISFIABLE"]


def test_publish_succeeds_once_the_branches_are_merged_into_one_or(client, auth, repository):
    """同样的两条阈值，写进同一条 or 之后不再算矛盾，可以发布。"""

    draft = seed_draft(repository, text=MERGED_BODY)
    merged = {
        "op": "or",
        "children": [
            {**revenue_expression("lt"), "value": "5000000"},
            revenue_expression("gte"),
        ],
    }
    saved = save_criteria(
        client, auth, draft, [criterion_payload(draft, expression=merged, quote=MERGED_QUOTE)]
    ).json()
    codes = [item["code"] for item in saved["validationFindings"]]
    assert [item["validationStatus"] for item in saved["criteria"]] == ["passed"], codes
    assert codes == []

    response = client.post(
        f"/api/v1/admin/policies/{draft.id}/publish",
        headers=headers(auth, saved["draft"]["revision"]),
        json={"version": draft.version, "reviewConfirmed": True},
    )
    assert response.status_code == 200


MANUAL_REVIEW_EXPRESSION = {
    "op": "manual_review",
    "reason": "「国家非限制和禁止行业」指向《产业结构调整指导目录》，没有 GB/T 码，须人工核查",
}
INDUSTRY_QUOTE = "第二条 本通知不适用于批发和零售业等限制类行业。"


def test_a_manual_review_criterion_does_not_block_publishing(client, auth, repository):
    """M2 在 PR #9 的 P1：判不了的条件不能只剩「退回待核实、整版政策发不出去」一条路。"""

    draft = seed_draft(repository)
    saved = save_criteria(
        client,
        auth,
        draft,
        [criterion_payload(draft, quote=INDUSTRY_QUOTE, expression=MANUAL_REVIEW_EXPRESSION)],
    ).json()

    # 校验状态仍是 passed——「passed」说的是引文与结构没问题，不是「这条判定为真」。
    assert [item["validationStatus"] for item in saved["criteria"]] == ["passed"]
    assert saved["validationFindings"] == []

    response = client.post(
        f"/api/v1/admin/policies/{draft.id}/publish",
        headers=headers(auth, saved["draft"]["revision"]),
        json={"version": draft.version, "reviewConfirmed": True},
    )
    assert response.status_code == 200


def test_a_manual_review_criterion_still_fails_on_a_bad_quote(client, auth, repository):
    draft = seed_draft(repository)
    saved = save_criteria(
        client,
        auth,
        draft,
        [
            criterion_payload(
                draft,
                quote="本条由税务机关另行核定",
                expression=MANUAL_REVIEW_EXPRESSION,
            )
        ],
    ).json()

    assert [item["validationStatus"] for item in saved["criteria"]] == ["failed"]
    assert any(finding["code"].startswith("SPAN_") for finding in saved["validationFindings"])


# ------------------------------------------------------------------ API-07


def test_publish_requires_review_confirmation(client, auth, repository):
    draft = seed_draft(repository)
    response = client.post(
        f"/api/v1/admin/policies/{draft.id}/publish",
        headers=headers(auth, draft.revision),
        json={"version": draft.version, "reviewConfirmed": False},
    )
    assert response.status_code == 422


def test_publish_without_criteria_is_rejected(client, auth, repository):
    draft = seed_draft(repository)
    response = client.post(
        f"/api/v1/admin/policies/{draft.id}/publish",
        headers=headers(auth, draft.revision),
        json={"version": draft.version, "reviewConfirmed": True},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "CRITERIA_REQUIRED"


def test_publish_returns_policy_detail_and_blocks_second_publish(client, auth, repository):
    draft = seed_draft(repository)
    saved = save_criteria(client, auth, draft, [criterion_payload(draft)]).json()

    response = client.post(
        f"/api/v1/admin/policies/{draft.id}/publish",
        headers=headers(auth, saved["draft"]["revision"]),
        json={"version": draft.version, "reviewConfirmed": True},
    )

    assert response.status_code == 200
    detail = response.json()
    assert detail["status"] == "published"
    assert detail["version"] == draft.version
    assert detail["criteria"][0]["validationStatus"] == "passed"
    assert detail["preparationTemplate"]["handlingMode"] in {
        "tax_self_assessment",
        "grant_application",
    }
    assert response.headers["etag"]

    again = client.post(
        f"/api/v1/admin/policies/{draft.id}/publish",
        headers=headers(auth, response.headers["etag"].strip('"')),
        json={"version": draft.version, "reviewConfirmed": True},
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "POLICY_ALREADY_PUBLISHED"


def test_published_version_cannot_be_extracted_or_edited(client, auth, repository):
    draft = seed_draft(repository)
    saved = save_criteria(client, auth, draft, [criterion_payload(draft)]).json()
    client.post(
        f"/api/v1/admin/policies/{draft.id}/publish",
        headers=headers(auth, saved["draft"]["revision"]),
        json={"version": draft.version, "reviewConfirmed": True},
    )

    extract = client.post(
        f"/api/v1/admin/policies/{draft.id}/extract",
        headers=headers(auth, uuid4(), key=uuid4()),
        json={"version": draft.version},
    )
    assert extract.status_code == 409
    assert extract.json()["error"]["code"] == "POLICY_ALREADY_PUBLISHED"

    update = save_criteria(client, auth, draft, [criterion_payload(draft)], revision=uuid4())
    assert update.status_code == 409


@pytest.mark.parametrize("review_finding", ["none", "conflict"])
def test_conflict_requires_two_sources_before_publish(client, auth, repository, review_finding):
    draft = seed_draft(repository)
    response = save_criteria(
        client, auth, draft, [criterion_payload(draft, review_finding=review_finding)]
    )
    body = response.json()
    if review_finding == "conflict":
        assert body["criteria"][0]["validationStatus"] == "failed"
        assert "CONFLICT_NEEDS_TWO_SPANS" in {item["code"] for item in body["validationFindings"]}
    else:
        assert body["criteria"][0]["validationStatus"] == "passed"
