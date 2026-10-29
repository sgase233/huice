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
    threshold_bounds,
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


# ------------------------------------------------ 地方通知的中文句式（2026-10-29 硬化）
#
# 真实地方通知很少用「不低于 200 万元」这种前置写法，更常见的是「200 万元（含）以上」
# 「在 1000 万元以下」「达到 2000 万元以上」，以及「两个会计年度（含）以上」这类相对
# 期间。以下用例把这几类写法锁进契约，避免正确的 AST 被误判成「引文不支持」。

LOCAL_NOTICE_TEXT = (
    "第七条 企业开展研发活动中实际发生的研发费用，未形成无形资产计入当期损益的，"
    "在按规定据实扣除的基础上，再按照实际发生额的一定比例在税前加计扣除。"
    "第八条（一）在本市实际从事经营活动；注册成立时间在两个会计年度（含）以上；"
    "近两年均已申报享受研发费用税前加计扣除；且上年度研发费用增量达200万元（含）以上；"
    "当年度已申报享受研发费用税前加计扣除；且上年度研发费用达200万元（含）以上；"
    "单位诚信记录良好。\n"
    "第八条（二）上年度研发费用总额不低于100万元；且占营业收入总额比重不低于3%；"
    "上年度营业收入总额在1000万元以上；或上年度营业收入总额在1000万元以下但近2年新增"
    "股权融资总额达到2000万元以上。"
)
LOCAL = SourceText(id=uuid4(), source_url=SOURCE_URL, text=LOCAL_NOTICE_TEXT)
LOCAL_SOURCES = {LOCAL.id: LOCAL}


def local_span(quote: str) -> SourceSpanCheck:
    return span(quote, source_id=LOCAL.id, section="第八条")


#: 专精特新一类条款的原文：同一类「时长型」表述的四种写法，外加一句只有**期间**词的
#: 反例（「上年度」），用来锁住「期间词不算时长」这条边界。
MARKET_TEXT = (
    "第九条 企业须具有独立法人资格，并满足下列条件之一："
    "（一）从事该细分市场满3年（含）以上；"
    "（二）连续三年从事本细分市场业务；"
    "（三）在本细分市场经营不少于5年；"
    "（四）近10年一直从事该细分市场业务。\n"
    "第十条 上年度开始从事该细分市场业务的企业不适用前述年限要求。"
)
MARKET = SourceText(id=uuid4(), source_url=SOURCE_URL, text=MARKET_TEXT)
MARKET_SOURCES = {MARKET.id: MARKET}


def market_span(quote: str) -> SourceSpanCheck:
    return span(quote, source_id=MARKET.id, section="第九条")


@pytest.mark.parametrize(
    ("quote", "expected"),
    [
        ("上年度营业收入总额在1000万元以上", [(1_000_0000, "gte")]),
        ("上年度营业收入总额在1000万元以下", [(1_000_0000, "lte")]),
        ("上年度研发费用增量达200万元（含）以上", [(200_0000, "gte")]),
        ("近2年新增股权融资总额达到2000万元以上", [(2000_0000, "gte")]),
    ],
)
def test_suffix_and_verb_threshold_phrasings_are_recognised(quote, expected):
    bounds = threshold_bounds(quote)
    assert [(int(bound.amount), bound.direction) for bound in bounds] == expected


def test_houzhih_threshold_supports_a_greedy_revenue_condition():
    """「在 1000 万元以上」是后置写法，抽不到就会把正确 AST 判成没有阈值。"""

    verdict = validate_criterion(
        criterion(local_span("上年度营业收入总额在1000万元以上"), expression=compare("revenue", "10000000.00", "CNY")),
        LOCAL_SOURCES,
        version=VERSION,
    )

    assert verdict.status == "passed", codes(verdict)


def test_dadao_is_a_lower_bound_not_a_strict_one():
    """「达到 2000 万元以上」含本数：与 AST 的 gte 一致，不该报方向不一致。"""

    verdict = validate_criterion(
        criterion(
            local_span("近2年新增股权融资总额达到2000万元以上"),
            expression=compare("equityFinancing", "20000000.00", "CNY"),
        ),
        LOCAL_SOURCES,
        version=VERSION,
    )

    assert verdict.status == "passed", codes(verdict)


def test_han_ben_shu_difference_between_lt_and_lte_is_tolerated():
    """「1000 万元以下」含本数（lte），AST 写 lt 仍是同一侧，不判为方向抽错。"""

    verdict = validate_criterion(
        criterion(
            local_span("上年度营业收入总额在1000万元以下"),
            expression=compare("revenue", "10000000.00", "CNY", comparator="lt"),
        ),
        LOCAL_SOURCES,
        version=VERSION,
    )

    assert verdict.status == "passed", codes(verdict)


def test_a_zero_threshold_needs_no_number_in_the_quote():
    """「实际发生的研发费用」对应 rdExpense > 0：0 是存在性条件，不是引文会写的阈值。"""

    zero = {
        "op": "compare",
        "factKey": "rdExpense",
        "period": "assessment",
        "comparator": "gt",
        "value": "0",
        "unit": "CNY",
    }
    verdict = validate_criterion(
        criterion(local_span("企业开展研发活动中实际发生的研发费用"), expression=zero),
        LOCAL_SOURCES,
        version=VERSION,
    )

    assert verdict.status == "passed", codes(verdict)


def test_a_non_zero_threshold_still_needs_a_number_in_the_quote():
    expression = {
        "op": "compare",
        "factKey": "rdExpense",
        "period": "assessment",
        "comparator": "gt",
        "value": "1000000",
        "unit": "CNY",
    }
    verdict = validate_criterion(
        criterion(local_span("企业开展研发活动中实际发生的研发费用"), expression=expression),
        LOCAL_SOURCES,
        version=VERSION,
    )

    assert "CITATION_THRESHOLD_NOT_FOUND" in codes(verdict)


def test_relative_period_phrase_supports_a_pinned_absolute_date():
    """原文写「两个会计年度（含）以上」，AST 里是换算后的绝对日期，应视为受支持。"""

    expression = {
        "op": "date",
        "factKey": "establishedDate",
        "period": "assessment",
        "comparator": "lte",
        "value": "2023-12-31",
    }
    verdict = validate_criterion(
        criterion(local_span("注册成立时间在两个会计年度（含）以上"), expression=expression),
        LOCAL_SOURCES,
        version=VERSION,
    )

    assert verdict.status == "passed", codes(verdict)


def test_a_date_without_any_period_phrase_is_still_reported():
    expression = {
        "op": "date",
        "factKey": "establishedDate",
        "period": "assessment",
        "comparator": "lte",
        "value": "2023-12-31",
    }
    verdict = validate_criterion(
        criterion(local_span("上年度研发费用达200万元（含）以上"), expression=expression),
        LOCAL_SOURCES,
        version=VERSION,
    )

    assert "CITATION_DATE_NOT_FOUND" in codes(verdict)


@pytest.mark.parametrize(
    "quote",
    [
        "从事该细分市场满3年（含）以上",
        "连续三年从事本细分市场业务",
        "在本细分市场经营不少于5年",
        "近10年一直从事该细分市场业务",
    ],
)
def test_relative_period_wording_is_recognised_in_any_number(quote):
    """rc.4 起 ``specificMarketSince`` 是日期键，而原文几乎只会写年数。

    逐条枚举的写法表只能覆盖到「满两年」「近三年」这种恰好出现过的数字，换个数字
    （「满3年」「近10年」）就漏；这里锁住数字/汉字的通配形式。
    """

    expression = {
        "op": "date",
        "factKey": "specificMarketSince",
        "period": "assessment",
        "comparator": "lte",
        "value": "2023-12-31",
    }
    verdict = validate_criterion(
        criterion(market_span(quote), expression=expression),
        MARKET_SOURCES,
        version=VERSION,
    )

    assert verdict.status == "passed", codes(verdict)


def test_a_period_word_is_not_a_duration_word():
    """「上年度」是期间不是时长：放宽成通配后仍不能拿它当绝对日期的证据。"""

    expression = {
        "op": "date",
        "factKey": "specificMarketSince",
        "period": "assessment",
        "comparator": "lte",
        "value": "2023-12-31",
    }
    verdict = validate_criterion(
        criterion(market_span("上年度开始从事该细分市场业务"), expression=expression),
        MARKET_SOURCES,
        version=VERSION,
    )

    assert "CITATION_DATE_NOT_FOUND" in codes(verdict)


def _revenue_pair(operator: str) -> dict:
    lower = compare("revenue", "10000000.00", "CNY", comparator="gte")
    upper = compare("revenue", "10000000.00", "CNY", comparator="lt")
    return {"op": operator, "children": [lower, upper]}


def test_complementary_bounds_inside_one_or_are_not_a_conflict():
    """「1000 万元以上，或在 1000 万元以下」是互补 OR 分支，不是没处理完的冲突。"""

    verdict = validate_criterion(
        criterion(
            local_span("上年度营业收入总额在1000万元以上"),
            local_span("上年度营业收入总额在1000万元以下"),
            expression=_revenue_pair("or"),
        ),
        LOCAL_SOURCES,
        version=VERSION,
    )

    assert verdict.status == "passed", codes(verdict)


def test_complementary_bounds_merged_with_and_still_report_a_conflict():
    """纯 AND 合并后永远不成立，引文层必须先挡住，让人工确认。"""

    verdict = validate_criterion(
        criterion(
            local_span("上年度营业收入总额在1000万元以上"),
            local_span("上年度营业收入总额在1000万元以下"),
            expression=_revenue_pair("and"),
        ),
        LOCAL_SOURCES,
        version=VERSION,
    )

    assert "CITATION_THRESHOLD_CONFLICT" in codes(verdict)


def test_the_same_or_branch_is_not_treated_as_exclusive():
    """两条阈值落在同一个 OR 分支里，互补就是真矛盾，不能因为外层有 or 就放过。"""

    expression = {
        "op": "or",
        "children": [
            _revenue_pair("and"),
            compare("rdExpense", "1000000.00", "CNY"),
        ],
    }
    verdict = validate_criterion(
        criterion(
            local_span("上年度营业收入总额在1000万元以上"),
            local_span("上年度营业收入总额在1000万元以下"),
            expression=expression,
        ),
        LOCAL_SOURCES,
        version=VERSION,
    )

    assert "CITATION_THRESHOLD_CONFLICT" in codes(verdict)


def test_mismatch_message_does_not_double_count_the_unit():
    """报错消息曾把已换算成元的数值再带上「万元」，看起来像放大了一万倍。"""

    verdict = validate_criterion(
        criterion(
            local_span("上年度营业收入总额在1000万元以上"),
            expression=compare("revenue", "20000000.00", "CNY"),
        ),
        LOCAL_SOURCES,
        version=VERSION,
    )

    message = next(f.message for f in verdict.findings if f.code == "CITATION_THRESHOLD_MISMATCH")
    assert "20000000万元" not in message
    assert "原文以「万元」计" in message
