"""引用校验：逐字证据、哈希、冲突两处来源与阈值方向（FR-02）。

验收锚点：数字方向抽错（「超过 300 万元」写成「不超过」）不能发布。
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.domain.citations import (
    CriterionInput,
    SourceSpanCheck,
    SourceText,
    quote_hash,
    validate_criteria,
    validate_criterion,
)

VERSION = "v1.0"
SOURCE_URL = "https://www.sz.gov.cn/zwgk/zfxxgkml/zcfg/content/post_1.html"
SOURCE = SourceText(
    id=uuid4(),
    source_url=SOURCE_URL,
    text=(
        "第一条 在本市登记的中小企业，年应纳税所得额不超过300万元的，减按25%计入应纳税所得额。\n"
        "第二条 年应纳税所得额超过300万元的，不适用本通知规定的优惠。\n"
        "第三条 本通知不适用于批发和零售业等限制类行业。\n"
        "第四条 企业应于2026年6月30日前完成申报。"
    ),
)
SOURCES = {SOURCE.id: SOURCE}


def span(quote: str, **overrides) -> SourceSpanCheck:
    values = {
        "source_id": SOURCE.id,
        "source_url": SOURCE_URL,
        "source_version": VERSION,
        "page": None,
        "section": "第一条",
        "quote": quote,
        "text_hash": quote_hash(quote),
    }
    values.update(overrides)
    return SourceSpanCheck(**values)


def criterion(*spans: SourceSpanCheck, review_finding: str = "none", expression=None) -> CriterionInput:
    return CriterionInput(
        id=uuid4(),
        title="合成演示阈值",
        review_finding=review_finding,
        spans=tuple(spans),
        expression=expression,
    )


def codes(verdict) -> set[str]:
    return {finding.code for finding in verdict.findings}


def test_verbatim_quote_passes():
    verdict = validate_criteria([criterion(span("年应纳税所得额不超过300万元"))], SOURCES, VERSION)[0]
    assert verdict.status == "passed"
    assert verdict.findings == []


def test_whitespace_differences_are_tolerated():
    verdict = validate_criteria([criterion(span("年应纳税所得额\n不超过  300万元"))], SOURCES, VERSION)[0]
    assert verdict.status == "passed"


def test_quote_missing_from_source_fails():
    verdict = validate_criteria([criterion(span("年应纳税所得额不超过500万元"))], SOURCES, VERSION)[0]
    assert verdict.status == "failed"
    assert "SPAN_QUOTE_NOT_FOUND" in codes(verdict)


def test_text_hash_must_match_the_quote():
    bad = span("年应纳税所得额不超过300万元", text_hash="0" * 64)
    verdict = validate_criteria([criterion(bad)], SOURCES, VERSION)[0]
    assert "SPAN_TEXT_HASH_MISMATCH" in codes(verdict)


def test_unknown_source_fails():
    foreign = span("年应纳税所得额不超过300万元", source_id=uuid4())
    verdict = validate_criteria([criterion(foreign)], SOURCES, VERSION)[0]
    assert "SPAN_SOURCE_UNKNOWN" in codes(verdict)


def test_source_url_and_version_must_match():
    span_row = span("年应纳税所得额不超过300万元", source_version="v9.9")
    verdict = validate_criteria([criterion(span_row)], SOURCES, VERSION)[0]
    assert "SPAN_SOURCE_VERSION_MISMATCH" in codes(verdict)

    other_url = span("年应纳税所得额不超过300万元", source_url=SOURCE_URL + "?x=1")
    verdict = validate_criteria([criterion(other_url)], SOURCES, VERSION)[0]
    assert "SPAN_SOURCE_URL_MISMATCH" in codes(verdict)


def test_page_or_section_is_required():
    blind = span("年应纳税所得额不超过300万元", section=None)
    verdict = validate_criteria([criterion(blind)], SOURCES, VERSION)[0]
    assert "SPAN_LOCATION_MISSING" in codes(verdict)


def test_criterion_without_spans_fails():
    verdict = validate_criteria([criterion()], SOURCES, VERSION)[0]
    assert verdict.status == "failed"
    assert "CRITERION_NO_SPANS" in codes(verdict)


def test_conflict_needs_two_spans():
    verdict = validate_criteria(
        [criterion(span("年应纳税所得额不超过300万元"), review_finding="conflict")], SOURCES, VERSION
    )[0]
    assert "CONFLICT_NEEDS_TWO_SPANS" in codes(verdict)


def test_conflict_with_identical_spans_fails():
    quote = "年应纳税所得额不超过300万元"
    verdict = validate_criteria(
        [criterion(span(quote), span(quote), review_finding="conflict")], SOURCES, VERSION
    )[0]
    assert "CONFLICT_SPANS_IDENTICAL" in codes(verdict)


def test_reviewed_conflict_record_can_be_published():
    """人工确认过的冲突可以发布：两处来源都在原文里，规则引擎固定返回 conflict。"""
    verdict = validate_criteria(
        [
            criterion(
                span("年应纳税所得额不超过300万元的，减按25%计入应纳税所得额"),
                span("年应纳税所得额超过300万元的，不适用本通知规定的优惠"),
                review_finding="conflict",
            )
        ],
        SOURCES,
        VERSION,
    )[0]
    # 两处来源都在原文里、标注了冲突：这是可发布的冲突记录，engine 会返回 conflict。
    assert verdict.status == "passed"


def test_opposite_direction_in_quotes_blocks_even_without_conflict_flag():
    verdict = validate_criteria(
        [
            criterion(
                span("年应纳税所得额不超过300万元"),
                span("年应纳税所得额超过300万元"),
            )
        ],
        SOURCES,
        VERSION,
    )[0]
    assert verdict.status == "failed"
    assert "CITATION_THRESHOLD_CONFLICT" in codes(verdict)


def test_ast_direction_must_match_the_quote():
    """把「不超过」抽成「不低于」是数字方向抽错，必须判为不支持。"""
    expression = {
        "op": "compare",
        "factKey": "taxableIncome",
        "period": "assessment",
        "comparator": "gte",
        "value": "3000000",
        "unit": "CNY",
    }
    verdict = validate_criteria(
        [criterion(span("年应纳税所得额不超过300万元"), expression=expression)], SOURCES, VERSION
    )[0]
    assert verdict.status == "failed"
    assert "CITATION_THRESHOLD_MISMATCH" in codes(verdict)


def test_ast_matching_quote_with_unit_conversion_passes():
    expression = {
        "op": "compare",
        "factKey": "taxableIncome",
        "period": "assessment",
        "comparator": "lte",
        "value": "3000000",
        "unit": "CNY",
    }
    verdict = validate_criteria(
        [criterion(span("年应纳税所得额不超过300万元"), expression=expression)], SOURCES, VERSION
    )[0]
    assert verdict.status == "passed"


def test_threshold_absent_from_quote_fails():
    expression = {
        "op": "compare",
        "factKey": "taxableIncome",
        "period": "assessment",
        "comparator": "lte",
        "value": "3000000",
        "unit": "CNY",
    }
    verdict = validate_criteria(
        [criterion(span("在本市登记的中小企业"), expression=expression)], SOURCES, VERSION
    )[0]
    assert "CITATION_THRESHOLD_NOT_FOUND" in codes(verdict)


def test_industry_exclusion_needs_a_matching_cue():
    expression = {
        "op": "industry_exclusion",
        "factKey": "industry",
        "period": "assessment",
        "excluded": ["F51"],
    }
    ok = validate_criteria(
        [criterion(span("本通知不适用于批发和零售业等限制类行业"), expression=expression)],
        SOURCES,
        VERSION,
    )[0]
    assert ok.status == "passed"

    bad = validate_criteria(
        [criterion(span("年应纳税所得额不超过300万元"), expression=expression)], SOURCES, VERSION
    )[0]
    assert "CITATION_INDUSTRY_NOT_FOUND" in codes(bad)


def test_date_condition_must_appear_in_quote():
    expression = {
        "op": "date",
        "factKey": "establishedDate",
        "period": "assessment",
        "comparator": "lte",
        "value": "2026-06-30",
    }
    verdict = validate_criteria(
        [criterion(span("企业应于2026年6月30日前完成申报"), expression=expression)],
        SOURCES,
        VERSION,
    )[0]
    assert verdict.status == "passed"

    wrong = validate_criteria(
        [criterion(span("企业应于2026年6月30日前完成申报"), expression={**expression, "value": "2026-07-31"})],
        SOURCES,
        VERSION,
    )[0]
    assert "CITATION_DATE_NOT_FOUND" in codes(wrong)


def test_duplicate_criterion_ids_are_rejected():
    first = criterion(span("年应纳税所得额不超过300万元"))
    duplicate = CriterionInput(
        id=first.id,
        title=first.title,
        review_finding=first.review_finding,
        spans=first.spans,
        expression=first.expression,
    )
    verdicts = validate_criteria([first, duplicate], SOURCES, VERSION)
    assert verdicts[0].status == "passed"
    assert verdicts[1].status == "failed"
    assert "CRITERION_DUPLICATE_ID" in codes(verdicts[1])


@pytest.mark.parametrize("bad_value", ["", "1e6", "abc"])
def test_ast_decimal_values_never_crash_the_checker(bad_value):
    expression = {
        "op": "compare",
        "factKey": "taxableIncome",
        "period": "assessment",
        "comparator": "lte",
        "value": bad_value,
        "unit": "CNY",
    }
    verdict = validate_criterion(
        criterion(span("年应纳税所得额不超过300万元"), expression=expression), SOURCES, version=VERSION
    )
    assert verdict.status in ("passed", "failed")


# --------------------------------- rc.3 新增金额键的引文数值核对（2026-10-29 补）


EXTENDED_SOURCE_TEXT = (
    "第八条 上年度营业收入不低于1000万元。\n"
    "第九条 近两年新增股权融资不低于2000万元。\n"
    "第十条 研发费用增量不低于200万元。\n"
    "第十一条 研发费用不低于100万元。\n"
    "第十二条 上年度研发费用占营业收入不低于3%。"
)
EXTENDED = SourceText(id=uuid4(), source_url=SOURCE_URL, text=EXTENDED_SOURCE_TEXT)
EXTENDED_SOURCES = {EXTENDED.id: EXTENDED}


def extended_span(quote: str) -> SourceSpanCheck:
    return span(quote, source_id=EXTENDED.id)


def compare(fact_key: str, value: str, unit: str, comparator: str = "gte") -> dict:
    return {
        "op": "compare",
        "factKey": fact_key,
        "period": "assessment",
        "comparator": comparator,
        "value": value,
        "unit": unit,
    }


@pytest.mark.parametrize(
    ("fact_key", "value", "unit", "quote"),
    [
        ("revenue", "10000000.00", "CNY", "上年度营业收入不低于1000万元"),
        ("equityFinancing", "20000000.00", "CNY", "近两年新增股权融资不低于2000万元"),
        ("rdExpenseIncrease", "2000000.00", "CNY", "研发费用增量不低于200万元"),
    ],
)
def test_new_amount_keys_are_checked_against_the_quote(fact_key, value, unit, quote):
    verdict = validate_criterion(
        criterion(extended_span(quote), expression=compare(fact_key, value, unit)),
        EXTENDED_SOURCES,
        version=VERSION,
    )

    assert verdict.status == "passed", codes(verdict)


def test_a_mismatched_amount_on_a_new_key_is_reported():
    verdict = validate_criterion(
        criterion(extended_span("上年度营业收入不低于1000万元"), expression=compare("revenue", "20000000.00", "CNY")),
        EXTENDED_SOURCES,
        version=VERSION,
    )

    assert "CITATION_THRESHOLD_MISMATCH" in codes(verdict)


def test_expense_increase_is_not_satisfied_by_a_plain_expense_quote():
    """增量单独成族：只有「研发费用」阈值的引文不能支持「研发费用增量」的条件。"""

    verdict = validate_criterion(
        criterion(extended_span("研发费用不低于100万元"), expression=compare("rdExpenseIncrease", "1000000.00", "CNY")),
        EXTENDED_SOURCES,
        version=VERSION,
    )

    assert "CITATION_THRESHOLD_NOT_FOUND" in codes(verdict)


def test_ratio_is_deliberately_not_compared_against_a_percentage_quote():
    """3% 与 0.03 不是同一个字符串，按金额阈值比对必然误报，故不做数值核对。"""

    verdict = validate_criterion(
        criterion(
            extended_span("上年度研发费用占营业收入不低于3%"),
            expression=compare("rdExpenseRevenueRatio", "0.03", "ratio"),
        ),
        EXTENDED_SOURCES,
        version=VERSION,
    )

    assert verdict.status == "passed", codes(verdict)
