"""条款抽取（API-05）的单元用例：只覆盖「能不能可靠抽出」与「抽错时是否退回人工」。"""

from __future__ import annotations

from uuid import uuid4

from app.agents.extraction import ExtractionSource, extract_criteria
from app.domain.citations import quote_hash
from app.integrations.llm import LlmResult

BODY_URL = "https://example.gov.cn/policy/2026/01.html"


def body(text: str, *, kind: str = "body") -> ExtractionSource:
    return ExtractionSource(id=uuid4(), source_url=BODY_URL, text=text, kind=kind)


def extract(text: str, **kwargs):
    return extract_criteria(version="v1.0", sources=[body(text)], **kwargs)


class FakeLlm:
    """返回预置 JSON 的假模型；``mode`` 必须是 deepseek 才会走模型分支。"""

    mode = "deepseek"

    def __init__(self, text: str, *, model: str = "deepseek-chat") -> None:
        self._text = text
        self._model = model

    def complete(self, *, system: str, user: str, max_tokens: int = 1024) -> LlmResult:
        return LlmResult(text=self._text, model=self._model, prompt_tokens=12, completion_tokens=34, elapsed_ms=5)


# ------------------------------------------------------------------ 规则路径


def test_threshold_sentence_becomes_compare_node_with_verbatim_quote():
    text = "第一条 年应纳税所得额不超过300万元的中小企业，减按25%计入应纳税所得额。"
    outcome = extract(text)

    assert outcome.status == "ok"
    assert len(outcome.criteria) == 1
    criterion = outcome.criteria[0]
    assert criterion.expression == {
        "op": "compare",
        "factKey": "taxableIncome",
        "period": "assessment",
        "comparator": "lte",
        "value": "3000000",
        "unit": "CNY",
    }
    span = criterion.spans[0]
    assert span.quote == text  # 引文与原文一字不差，含句号
    assert span.section == "第一条"
    assert span.source_version == "v1.0"
    assert span.text_hash == quote_hash(text)


def test_amount_units_are_normalised_to_cny():
    text = "研发费用不低于1.5亿元的企业可以申报。"
    outcome = extract(text)

    assert outcome.criteria[0].expression["value"] == "150000000"
    assert outcome.criteria[0].expression["comparator"] == "gte"


def test_established_date_becomes_date_node():
    text = "第二条 2020年6月30日以前成立的企业均可申请。"
    outcome = extract(text)

    expression = outcome.criteria[0].expression
    assert expression["op"] == "date"
    assert expression["factKey"] == "establishedDate"
    assert expression["comparator"] == "lte"
    assert expression["value"] == "2020-06-30"


def test_industry_exclusion_is_flagged_for_manual_annotation():
    text = "第三条 本通知不适用于批发和零售业等限制类行业。"
    outcome = extract(text)

    assert outcome.criteria == []
    assert outcome.status == "needs_review"
    assert [finding.code for finding in outcome.findings] == ["EXTRACTION_INDUSTRY_NEEDS_MANUAL"]
    assert outcome.findings[0].source_spans[0].section == "第三条"


def test_same_threshold_repeated_is_extracted_once():
    text = (
        "年应纳税所得额不超过300万元，可减按25%计入。\n"
        "年应纳税所得额不超过300万元，可减按25%计入。\n"
    )
    assert len(extract(text).criteria) == 1


def test_sentence_without_actionable_rule_yields_no_criteria_finding():
    outcome = extract("本通知自发布之日起施行。")

    assert outcome.criteria == []
    assert outcome.status == "needs_review"
    assert [finding.code for finding in outcome.findings] == ["EXTRACTION_NO_CRITERIA"]


def test_missing_body_is_not_invented():
    outcome = extract_criteria(version="v1.0", sources=[])
    assert outcome.criteria == []
    assert [finding.code for finding in outcome.findings] == ["EXTRACTION_NO_BODY"]
    assert outcome.usage is not None and outcome.usage.mode == "fixture"


def test_fixture_mode_does_not_use_the_model_even_if_given():
    from app.integrations.llm import FixtureLlmClient

    outcome = extract_criteria(
        version="v1.0", sources=[body("年应纳税所得额不超过300万元。")], llm=FixtureLlmClient()
    )
    assert outcome.usage.mode == "fixture"
    assert len(outcome.criteria) == 1


# ------------------------------------------------------------------ 模型路径


def test_model_output_is_accepted_only_when_the_quote_exists():
    text = "年应纳税所得额不超过300万元。"
    payload = (
        '[{"title":"所得税上限","expression":{"op":"compare","factKey":"taxableIncome",'
        '"period":"assessment","comparator":"lte","value":"3000000","unit":"CNY"},'
        f'"quote":"{text}","section":"第一条"}},'
        '{"title":"编造的条款","expression":{"op":"compare","factKey":"rdExpense",'
        '"period":"assessment","comparator":"gte","value":"1","unit":"CNY"},'
        '"quote":"原文里没有这句。","section":"第二条"}]'
    )
    outcome = extract(text, llm=FakeLlm(payload))

    assert [criterion.title for criterion in outcome.criteria] == ["所得税上限"]
    assert [finding.code for finding in outcome.findings] == ["EXTRACTION_QUOTE_NOT_IN_BODY"]
    assert outcome.status == "needs_review"
    assert outcome.usage.mode == "deepseek"
    assert outcome.usage.model_version == "deepseek-chat"
    assert outcome.usage.input_tokens == 12


def test_model_expression_is_revalidated_against_the_contract():
    text = "年应纳税所得额不超过300万元。"
    payload = (
        '[{"title":"坏表达式","expression":{"op":"sum","value":"1"},'
        f'"quote":"{text}"}}]'
    )
    outcome = extract(text, llm=FakeLlm(payload))

    assert outcome.criteria == []
    assert "EXTRACTION_EXPRESSION_INVALID" in {finding.code for finding in outcome.findings}


def test_model_non_json_output_falls_back_to_manual():
    outcome = extract("年应纳税所得额不超过300万元。", llm=FakeLlm("抱歉，我无法完成。"))

    assert outcome.criteria == []
    assert [finding.code for finding in outcome.findings] == ["EXTRACTION_OUTPUT_INVALID"]


def test_model_item_missing_fields_is_reported_not_guessed():
    payload = '[{"title":"只有标题"}]'
    outcome = extract("年应纳税所得额不超过300万元。", llm=FakeLlm(payload))

    assert outcome.criteria == []
    assert "EXTRACTION_ITEM_INCOMPLETE" in {finding.code for finding in outcome.findings}


def test_model_prompt_lists_every_unit_including_ratio():
    """rc.3 起 rdExpenseRevenueRatio 只能用 ratio：提示词漏了这个单位，
    模型给出的比例条件会被单位校验拒掉，抽取白跑一遍。"""

    captured: dict[str, str] = {}

    class Capturing(FakeLlm):
        def complete(self, *, system, user, max_tokens=1024):
            captured["system"] = system
            return super().complete(system=system, user=user, max_tokens=max_tokens)

    extract("年应纳税所得额不超过300万元。", llm=Capturing("[]"))

    system = captured["system"]
    assert "CNY/person/code/date/boolean/ratio" in system
    assert "rdExpenseRevenueRatio" in system


def test_model_prompt_lists_every_fact_key():
    """抽取提示词里的键白名单必须跟 ``FACT_UNITS`` 同步。

    漏一个键不会报错：模型照样抽取，但写回时被 ``FactKey`` 字面量拒掉，表现为
    一条「抽取到了却存不进去」的条款，排查时很容易归咎于模型。
    """

    from app.domain.rules import FACT_UNITS

    captured: dict[str, str] = {}

    class Capturing(FakeLlm):
        def complete(self, *, system, user, max_tokens=1024):
            captured["system"] = system
            return super().complete(system=system, user=user, max_tokens=max_tokens)

    extract("年应纳税所得额不超过300万元。", llm=Capturing("[]"))

    missing = sorted(key for key in FACT_UNITS if key not in captured["system"].replace("\n", ""))
    assert missing == [], f"抽取提示词缺少这些键：{missing}"
