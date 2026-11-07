"""基线提示词与回答解析：形状不对就报错，不猜、不补。

解析这一层决定了「格式错误」与「判断错误」分得开不开：把模型少给一条当成
``insufficient_evidence``，等于替模型把失误算成结论，硬门槛误判会被稀释。
"""

from __future__ import annotations

import json

import pytest

from app.ablation.prompt import (
    MAX_TOKENS,
    PROMPT_VERSION,
    RESULTS,
    BaselineFormatError,
    build_user_message,
    parse_response,
    quote_supported,
)


@pytest.fixture(scope="module")
def dataset():
    from app.ablation.dataset import load_dataset

    return load_dataset()


def _case(dataset, case_id: str):
    return dataset.case_for(case_id)


def _answer(codes, result="satisfied", **extra):
    payload = {
        "results": [
            {"criterionId": code, "result": result, "reason": "r", "quote": None, "facts": []}
            for code in codes
        ],
        "policyExpired": False,
        "summary": "s",
    }
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


def test_prompt_version_and_budget_are_frozen():
    assert PROMPT_VERSION == "single-agent@1"
    assert MAX_TOKENS == 2048
    assert RESULTS == ("satisfied", "not_satisfied", "insufficient_evidence", "conflict")


def test_user_message_carries_policy_facts_and_criteria(dataset):
    case = _case(dataset, "C001")
    message = build_user_message(dataset, case)
    assert case.document in message
    for code in case.criterion_codes:
        assert code in message
    enterprise = dataset.enterprises[case.enterprise]
    for fact in enterprise.facts:
        assert fact.key in message
    # 正文必须整段给到，不能只给条件片段——否则引用校验那一项没法比。
    assert len(message) > len(dataset.source_text(dataset.policies[(case.policy_code, case.document)]))


def test_parse_ok_returns_results_in_given_order(dataset):
    case = _case(dataset, "C001")
    payload = parse_response(_answer(list(reversed(case.criterion_codes))), expected=list(case.criterion_codes))
    assert [item["criterionId"] for item in payload["results"]] == list(case.criterion_codes)
    assert payload["policyExpired"] is False


def test_parse_strips_markdown_fence():
    text = "```json\n" + _answer(["A"], result="not_satisfied") + "\n```"
    assert parse_response(text, expected=["A"])["results"][0]["result"] == "not_satisfied"


def test_parse_rejects_missing_criterion():
    with pytest.raises(BaselineFormatError, match="少给了"):
        parse_response(_answer(["A"]), expected=["A", "B"])


def test_parse_rejects_unknown_criterion():
    with pytest.raises(BaselineFormatError, match="不在给定的清单"):
        parse_response(_answer(["Z"]), expected=["A"])


def test_parse_rejects_duplicate_criterion():
    text = json.dumps({"results": [
        {"criterionId": "A", "result": "satisfied", "reason": "", "quote": None, "facts": []},
        {"criterionId": "A", "result": "satisfied", "reason": "", "quote": None, "facts": []},
    ]})
    with pytest.raises(BaselineFormatError, match="两次"):
        parse_response(text, expected=["A"])


def test_parse_rejects_result_outside_four_values():
    with pytest.raises(BaselineFormatError, match="不在四值里"):
        parse_response(_answer(["A"], result="maybe"), expected=["A"])


def test_parse_rejects_non_string_quote():
    text = json.dumps({"results": [
        {"criterionId": "A", "result": "satisfied", "reason": "", "quote": 3, "facts": []},
    ]})
    with pytest.raises(BaselineFormatError, match="quote"):
        parse_response(text, expected=["A"])


def test_parse_rejects_bad_facts_shape():
    text = json.dumps({"results": [
        {"criterionId": "A", "result": "satisfied", "reason": "", "quote": None, "facts": ["x"]},
    ]})
    with pytest.raises(BaselineFormatError, match="facts"):
        parse_response(text, expected=["A"])


def test_parse_rejects_non_json():
    with pytest.raises(BaselineFormatError, match="不是合法 JSON"):
        parse_response("结论：满足", expected=["A"])


def test_quote_supported_is_verbatim_after_normalisation(dataset):
    case = _case(dataset, "C002")
    criterion = dataset.criterion(case, "SME-002")
    assert quote_supported(dataset, case, "SME-002", criterion.spans[0])
    # 归一化只抹平全半角与空白，改字就应判不支持。
    assert not quote_supported(dataset, case, "SME-002", criterion.spans[0] + "（补一句）")
    assert not quote_supported(dataset, case, "SME-002", None)
