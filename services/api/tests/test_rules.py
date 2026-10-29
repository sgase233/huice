"""条件 AST：结构校验、四态求值、硬门槛与未知事实（FR-01 / FR-04）。

验收锚点来自分工计划 10/28 一行：「行业排除→不满足」「缺事实→证据不足」。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.domain.rules import (
    CONFLICT,
    FACT_UNITS,
    INSUFFICIENT,
    NOT_SATISFIED,
    POLICY_UNSATISFIABLE,
    SATISFIED,
    CriterionRule,
    FactSnapshot,
    FactTable,
    aggregate,
    evaluate_expression,
    evaluate_policy,
    policy_contradictions,
    validate_expression,
)

YEAR = (date(2026, 1, 1), date(2026, 12, 31))


def fact(key: str, value, start: date = YEAR[0], end: date = YEAR[1]) -> FactSnapshot:
    return FactSnapshot(key=key, period_start=start, period_end=end, unit=FACT_UNITS[key], value=value)


def table(*facts: FactSnapshot) -> FactTable:
    return FactTable(facts)


def compare(key: str, comparator: str, value, *, unit: str | None = None, period=None) -> dict:
    return {
        "op": "compare",
        "factKey": key,
        "period": period or "assessment",
        "comparator": comparator,
        "value": value,
        "unit": unit or FACT_UNITS.get(key, "CNY"),
    }


INDUSTRY_GATE = {
    "op": "industry_exclusion",
    "factKey": "industry",
    "period": "assessment",
    "excluded": ["F51", "F52"],
}


# ------------------------------------------------------------------ 结构校验


@pytest.mark.parametrize(
    "expression, code",
    [
        ({"op": "sum"}, "EXPRESSION_OP_UNKNOWN"),
        ({"op": "and", "children": [compare("taxableIncome", "gte", "1")]}, "EXPRESSION_CHILDREN"),
        ({"op": "not"}, "EXPRESSION_CHILD"),
        (compare("unknownKey", "gte", "1"), "EXPRESSION_FACT_KEY"),
        (compare("taxableIncome", "gt", "1", unit="person"), "EXPRESSION_UNIT"),
        (compare("taxableIncome", "gt", "1e6"), "EXPRESSION_VALUE"),
        (compare("industry", "gt", "C39"), "EXPRESSION_COMPARATOR"),
        (compare("rdActivity", "eq", "true"), "EXPRESSION_VALUE"),
        ({"op": "industry_exclusion", "factKey": "industry", "period": "assessment", "excluded": []}, "EXPRESSION_EXCLUDED"),
        ({"op": "industry_exclusion", "factKey": "industry", "period": "assessment", "excluded": ["F51", "F51"]}, "EXPRESSION_EXCLUDED"),
        (
            {
                "op": "quarterly_average",
                "metric": "employeeCount",
                "period": {"startDate": "2026-01-01", "endDate": "2026-06-30"},
                "comparator": "gte",
                "value": "10",
                "unit": "person",
            },
            "EXPRESSION_PERIOD",
        ),
        (
            {
                "op": "quarterly_average",
                "metric": "employeeCount",
                "period": "assessment",
                "comparator": "gte",
                "value": "10",
                "unit": "CNY",
            },
            "EXPRESSION_UNIT",
        ),
        ({"op": "compare", "factKey": "taxableIncome", "period": {"startDate": "2026-12-31", "endDate": "2026-01-01"}, "comparator": "gte", "value": "1", "unit": "CNY"}, "EXPRESSION_PERIOD"),
    ],
)
def test_expression_problems_are_reported(expression, code):
    codes = [problem.code for problem in validate_expression(expression)]
    assert code in codes


def test_valid_expressions_pass_validation():
    assert validate_expression(compare("taxableIncome", "gte", "3000000.00")) == []
    assert validate_expression(INDUSTRY_GATE) == []
    assert validate_expression({"op": "not", "child": INDUSTRY_GATE}) == []


def test_nesting_depth_is_limited():
    expression: dict = compare("taxableIncome", "gte", "1")
    for _ in range(10):
        expression = {"op": "not", "child": expression}
    assert "EXPRESSION_TOO_DEEP" in {problem.code for problem in validate_expression(expression)}


def test_node_budget_is_counted_over_the_whole_tree():
    """节点上限是整棵表达式累计，不是每个 and/or 各算各的。

    满扇出的一棵浅树就能超过上限：单看每层都「只有 2 个子项」，展开的节点数却是
    指数级的。
    """

    expression: dict = compare("taxableIncome", "gte", "1")
    # 每层翻倍：7 层 and 就有 2^7 = 128 个叶子，加内部节点共 255 个。
    for _ in range(7):
        expression = {"op": "and", "children": [expression, expression]}

    codes = {problem.code for problem in validate_expression(expression)}
    assert codes == {"EXPRESSION_TOO_LARGE"}


def test_node_budget_boundary_is_exactly_the_limit():
    """上限含根节点：199 个子项 + 根 = 200 允许，200 个子项 + 根 = 201 拒绝。"""

    child = compare("taxableIncome", "gte", "1")

    allowed = {"op": "and", "children": [child] * 199}
    assert validate_expression(allowed) == []

    rejected = {"op": "and", "children": [child] * 200}
    assert {problem.code for problem in validate_expression(rejected)} == {"EXPRESSION_TOO_LARGE"}


def test_a_long_not_chain_is_rejected_without_recursion_error():
    """``not`` 串成长链时按节点数先拒绝，不能先撞 Python 的递归上限。"""

    expression: dict = compare("taxableIncome", "gte", "1")
    for _ in range(300):
        expression = {"op": "not", "child": expression}

    codes = {problem.code for problem in validate_expression(expression)}
    assert codes == {"EXPRESSION_TOO_LARGE"}


# ------------------------------------------------------------------ 四态求值


def test_industry_in_exclusion_list_is_not_satisfied():
    """10/28 验收：行业排除命中就是「不满足」，而不是相似度下的「可能满足」。"""
    outcome = evaluate_expression(INDUSTRY_GATE, table(fact("industry", "F51")), period=YEAR)
    assert outcome.result == NOT_SATISFIED
    assert "排除清单" in outcome.reason


def test_industry_outside_list_is_satisfied():
    outcome = evaluate_expression(INDUSTRY_GATE, table(fact("industry", "C39")), period=YEAR)
    assert outcome.result == SATISFIED


def test_missing_fact_is_insufficient_not_satisfied():
    """10/28 验收：缺事实是「证据不足」，零值才是已知值。"""
    outcome = evaluate_expression(compare("taxableIncome", "gte", "3000000"), table(), period=YEAR)
    assert outcome.result == INSUFFICIENT
    assert outcome.missing == [("taxableIncome", YEAR[0], YEAR[1])]


def test_null_value_is_unknown_not_zero():
    outcome = evaluate_expression(
        compare("taxableIncome", "lte", "0"), table(fact("taxableIncome", None)), period=YEAR
    )
    assert outcome.result == INSUFFICIENT


def test_zero_is_a_known_value():
    outcome = evaluate_expression(
        compare("taxableIncome", "lte", "0"), table(fact("taxableIncome", Decimal("0"))), period=YEAR
    )
    assert outcome.result == SATISFIED


def test_threshold_boundary_uses_decimal_not_float():
    fact_row = table(fact("taxableIncome", Decimal("3000000")))
    assert evaluate_expression(compare("taxableIncome", "gte", "3000000"), fact_row, period=YEAR).result == SATISFIED
    assert evaluate_expression(compare("taxableIncome", "gt", "3000000"), fact_row, period=YEAR).result == NOT_SATISFIED
    assert evaluate_expression(compare("taxableIncome", "lte", "3000000"), fact_row, period=YEAR).result == SATISFIED


def test_hard_gate_is_not_masked_by_missing_facts_in_and():
    expression = {
        "op": "and",
        "children": [INDUSTRY_GATE, compare("taxableIncome", "gte", "3000000")],
    }
    outcome = evaluate_expression(expression, table(fact("industry", "F51")), period=YEAR)
    assert outcome.result == NOT_SATISFIED


def test_and_with_unknown_child_is_insufficient():
    expression = {
        "op": "and",
        "children": [INDUSTRY_GATE, compare("taxableIncome", "gte", "3000000")],
    }
    outcome = evaluate_expression(expression, table(fact("industry", "C39")), period=YEAR)
    assert outcome.result == INSUFFICIENT


def test_or_branch_that_holds_is_not_blocked_by_unrelated_missing_fact():
    expression = {
        "op": "or",
        "children": [
            compare("taxableIncome", "gte", "3000000"),
            compare("rdExpense", "gte", "500000"),
        ],
    }
    outcome = evaluate_expression(
        expression, table(fact("taxableIncome", Decimal("3200000"))), period=YEAR
    )
    assert outcome.result == SATISFIED


def test_or_without_any_holding_branch_is_not_satisfied():
    expression = {
        "op": "or",
        "children": [
            compare("taxableIncome", "gte", "3000000"),
            compare("rdExpense", "gte", "500000"),
        ],
    }
    outcome = evaluate_expression(
        expression,
        table(fact("taxableIncome", Decimal("1")), fact("rdExpense", Decimal("2"))),
        period=YEAR,
    )
    assert outcome.result == NOT_SATISFIED


def test_not_inverts_known_results_but_not_unknown():
    inner = compare("rdActivity", "eq", True)
    assert evaluate_expression({"op": "not", "child": inner}, table(fact("rdActivity", True)), period=YEAR).result == NOT_SATISFIED
    assert evaluate_expression({"op": "not", "child": inner}, table(fact("rdActivity", False)), period=YEAR).result == SATISFIED
    assert evaluate_expression({"op": "not", "child": inner}, table(), period=YEAR).result == INSUFFICIENT


def test_conflict_node_propagates_through_or():
    """冲突不得被 OR 的成立分支掩盖（实现约定 2.2）。"""
    broken = {"op": "unsupported_operator"}
    expression = {"op": "or", "children": [broken, compare("taxableIncome", "gte", "1")]}
    outcome = evaluate_expression(expression, table(fact("taxableIncome", Decimal("5"))), period=YEAR)
    assert outcome.result == CONFLICT


# ----------------------------------------------------------------- 季度平均


def quarterly(comparator: str = "gte", value: str = "10") -> dict:
    return {
        "op": "quarterly_average",
        "metric": "employeeCount",
        "period": {"startDate": "2026-01-01", "endDate": "2026-12-31"},
        "comparator": comparator,
        "value": value,
        "unit": "person",
    }


def quarterly_facts(*, gaps: tuple[int, ...] = ()) -> FactTable:
    rows: list[FactSnapshot] = []
    quarters = [
        (date(2026, 1, 1), date(2026, 3, 31)),
        (date(2026, 4, 1), date(2026, 6, 30)),
        (date(2026, 7, 1), date(2026, 9, 30)),
        (date(2026, 10, 1), date(2026, 12, 31)),
    ]
    for index, (start, end) in enumerate(quarters):
        if index in gaps:
            continue
        rows.append(fact("employeeCountStart", Decimal("10"), start, end))
        rows.append(fact("employeeCountEnd", Decimal("14"), start, end))
    return table(*rows)


def test_quarterly_average_uses_midpoints_then_mean():
    """每季度先 (期初+期末)/2，再对四季取平均；中间值要保留。"""
    outcome = evaluate_expression(quarterly("gte", "12"), quarterly_facts(), period=YEAR)
    assert outcome.result == SATISFIED
    assert outcome.calculation is not None
    assert outcome.calculation["average"] == "12"
    assert len(outcome.calculation["inputs"]) == 4
    assert outcome.calculation["inputs"][0]["midpoint"] == "12"


def test_quarterly_average_with_one_missing_input_is_insufficient():
    outcome = evaluate_expression(quarterly("gte", "12"), quarterly_facts(gaps=(2,)), period=YEAR)
    assert outcome.result == INSUFFICIENT
    assert len(outcome.missing) == 2
    assert outcome.calculation is None


def test_quarterly_average_requires_full_natural_year_period():
    expression = quarterly()
    expression["period"] = {"startDate": "2026-01-01", "endDate": "2026-06-30"}
    outcome = evaluate_expression(expression, quarterly_facts(), period=YEAR)
    assert outcome.result == INSUFFICIENT


# ------------------------------------------------------------------ 政策级


def policy(*criteria: CriterionRule) -> list[CriterionRule]:
    return list(criteria)


def test_policy_level_priority_is_conflict_then_not_satisfied():
    criteria = policy(
        CriterionRule(id="c1", title="冲突条款", expression=INDUSTRY_GATE, review_finding="conflict"),
        CriterionRule(id="c2", title="行业门槛", expression=INDUSTRY_GATE),
    )
    outcome = evaluate_policy(
        policy_id="p1",
        version="v1",
        criteria=criteria,
        facts=table(fact("industry", "F51")),
        period=YEAR,
    )
    assert outcome.result == CONFLICT
    assert outcome.criteria[0].result == CONFLICT


def test_policy_level_all_satisfied():
    criteria = policy(CriterionRule(id="c1", title="行业门槛", expression=INDUSTRY_GATE))
    outcome = evaluate_policy(
        policy_id="p1", version="v1", criteria=criteria, facts=table(fact("industry", "C39")), period=YEAR
    )
    assert outcome.result == SATISFIED
    assert outcome.dependencies == [("industry", YEAR[0], YEAR[1])]


@pytest.mark.parametrize(
    "results, expected",
    [
        ([CONFLICT, NOT_SATISFIED], CONFLICT),
        ([NOT_SATISFIED, INSUFFICIENT], NOT_SATISFIED),
        ([INSUFFICIENT, SATISFIED], INSUFFICIENT),
        ([SATISFIED, SATISFIED], SATISFIED),
    ],
)
def test_aggregate_priority(results, expected):
    from app.domain.rules import Evaluation

    assert aggregate(Evaluation(result, "", [], []) for result in results) == expected


# ------------------------------------------------------ 事实字典扩展（rc.3，2026-10-28）


def test_new_keys_are_registered_with_their_units():
    assert FACT_UNITS["revenue"] == "CNY"
    assert FACT_UNITS["equityFinancing"] == "CNY"
    assert FACT_UNITS["rdExpenseIncrease"] == "CNY"
    assert FACT_UNITS["rdExpenseRevenueRatio"] == "ratio"
    assert FACT_UNITS["rdAddDeductionClaimed"] == "boolean"
    assert FACT_UNITS["creditRecord"] == "boolean"


@pytest.mark.parametrize(
    "value, code",
    [("3%", "EXPRESSION_VALUE"), ("-0.1", "EXPRESSION_VALUE"), ("", "EXPRESSION_VALUE")],
)
def test_ratio_condition_rejects_non_decimal_and_negative_values(value, code):
    """比例只接受非负十进制字符串：`3%` 这种写法在这里拦下，不让它进库。"""
    problem_codes = [item.code for item in validate_expression(compare("rdExpenseRevenueRatio", "gte", value))]
    assert code in problem_codes


def test_ratio_condition_rejects_a_wrong_unit():
    expression = {**compare("rdExpenseRevenueRatio", "gte", "0.03"), "unit": "CNY"}
    assert "EXPRESSION_UNIT" in [item.code for item in validate_expression(expression)]


def test_ratio_is_compared_numerically():
    expression = compare("rdExpenseRevenueRatio", "gte", "0.03")
    assert validate_expression(expression) == []
    assert (
        evaluate_expression(
            expression, table(fact("rdExpenseRevenueRatio", Decimal("0.035"))), period=YEAR
        ).result
        == SATISFIED
    )
    assert (
        evaluate_expression(
            expression, table(fact("rdExpenseRevenueRatio", Decimal("0.02"))), period=YEAR
        ).result
        == NOT_SATISFIED
    )


def test_a_negative_expense_increment_is_allowed_and_compared():
    """``rdExpenseIncrease`` 是「本年减上年」的增量，下降时为负，校验不能拦。"""
    expression = compare("rdExpenseIncrease", "gte", "2000000")
    assert validate_expression(expression) == []
    outcome = evaluate_expression(
        expression, table(fact("rdExpenseIncrease", Decimal("-500000"))), period=YEAR
    )
    assert outcome.result == NOT_SATISFIED


@pytest.mark.parametrize("key", ["rdAddDeductionClaimed", "creditRecord"])
def test_new_boolean_keys_only_accept_booleans(key):
    assert validate_expression(compare(key, "eq", True)) == []
    assert "EXPRESSION_VALUE" in [
        item.code for item in validate_expression(compare(key, "eq", "true"))
    ]
    assert "EXPRESSION_COMPARATOR" in [
        item.code for item in validate_expression(compare(key, "gte", True))
    ]
    assert (
        evaluate_expression(compare(key, "eq", True), table(fact(key, False)), period=YEAR).result
        == NOT_SATISFIED
    )


def test_an_unknown_key_is_still_rejected():
    assert "EXPRESSION_FACT_KEY" in [
        item.code
        for item in validate_expression(
            {"op": "compare", "factKey": "invented", "period": "assessment", "comparator": "gte",
             "value": "1", "unit": "CNY"}
        )
    ]


# ------------------------------------------- 跨条款「合取为空」（政策永远不满足）

SRDI_PERIOD = {"startDate": "2024-01-01", "endDate": "2024-12-31"}


def rule(criterion_id: str, expression: dict) -> CriterionRule:
    return CriterionRule(id=criterion_id, title=criterion_id, expression=expression)


def test_srdi_revenue_pair_is_reported_as_unsatisfiable():
    """M3 的 SRDI-004/005 原样：``revenue>=1000万`` 与 ``revenue<1000万`` 并列。

    ``logic_group`` 不参与合并（政策级一律 AND），这两条同时生效时政策对任何企业
    都只能是 ``not_satisfied``——用户看到「不达标」，实际是「或」被写成了「且」。
    """

    found = policy_contradictions(
        [
            rule("SRDI-004", compare("revenue", "gte", "10000000.00", period=SRDI_PERIOD)),
            rule("SRDI-005", compare("revenue", "lt", "10000000.00", period=SRDI_PERIOD)),
        ]
    )
    assert [item.code for item in found] == [POLICY_UNSATISFIABLE]
    assert set(found[0].criterion_ids) == {"SRDI-004", "SRDI-005"}
    assert "revenue" in found[0].message
    assert "SRDI-004" in found[0].message and "SRDI-005" in found[0].message


def test_the_same_contradiction_is_caught_under_the_assessment_period():
    found = policy_contradictions(
        [
            rule("A", compare("taxableIncome", "lte", "3000000.00")),
            rule("B", compare("taxableIncome", "gt", "5000000.00")),
        ]
    )
    assert len(found) == 1


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("gte", "lte"),  # 闭区间有解：正好等于阈值
        ("gte", "gt"),
        ("lt", "lte"),
        ("ne", "gte"),  # 大于阈值就一定不等于阈值
        ("ne", "lt"),
        ("ne", "ne"),
    ],
)
def test_overlapping_bounds_are_not_reported(left, right):
    assert (
        policy_contradictions(
            [
                rule("A", compare("revenue", left, "10000000.00")),
                rule("B", compare("revenue", right, "10000000.00")),
            ]
        )
        == []
    )


def test_equal_point_ranges_are_disjoint():
    found = policy_contradictions(
        [
            rule("A", compare("revenue", "eq", "10000000.00")),
            rule("B", compare("revenue", "ne", "10000000.00")),
        ]
    )
    assert len(found) == 1


@pytest.mark.parametrize(
    ("key", "left", "right"),
    [
        ("region", ("eq", "440300"), ("ne", "440300")),
        ("region", ("eq", "440300"), ("eq", "440100")),
        ("rdActivity", ("eq", True), ("eq", False)),
        ("rdActivity", ("eq", True), ("ne", True)),
    ],
)
def test_code_and_boolean_pairs_only_conflict_on_the_same_value(key, left, right):
    found = policy_contradictions(
        [rule("A", compare(key, left[0], left[1])), rule("B", compare(key, right[0], right[1]))]
    )
    assert len(found) == 1


def test_different_periods_or_facts_are_never_compared():
    other_year = {"startDate": "2025-01-01", "endDate": "2025-12-31"}
    assert (
        policy_contradictions(
            [
                rule("A", compare("revenue", "gte", "10000000.00", period=SRDI_PERIOD)),
                rule("B", compare("revenue", "lt", "10000000.00", period=other_year)),
            ]
        )
        == []
    )
    # assessment 展开成哪一段要等运行时才知道，草稿阶段不拿它和写死的日期比。
    assert (
        policy_contradictions(
            [
                rule("A", compare("revenue", "gte", "10000000.00")),
                rule("B", compare("revenue", "lt", "10000000.00", period=SRDI_PERIOD)),
            ]
        )
        == []
    )
    assert (
        policy_contradictions(
            [
                rule("A", compare("revenue", "gte", "10000000.00", period=SRDI_PERIOD)),
                rule("B", compare("rdExpense", "lt", "10000000.00", period=SRDI_PERIOD)),
            ]
        )
        == []
    )


def test_mutually_exclusive_branches_inside_one_or_are_fine():
    """两条互斥条件只要都写在同一个 or 节点里，就不是矛盾。"""

    expression = {
        "op": "or",
        "children": [
            compare("revenue", "lt", "10000000.00", period=SRDI_PERIOD),
            compare("revenue", "gte", "10000000.00", period=SRDI_PERIOD),
        ],
    }
    assert validate_expression(expression) == []
    assert policy_contradictions([rule("SRDI", expression)]) == []


def test_the_merged_or_ast_for_srdi_does_not_trip_the_check():
    """给 M3 的修法：把两档合并成一条 or，检测器不再报。"""

    merged = {
        "op": "or",
        "children": [
            {
                "op": "and",
                "children": [
                    compare("revenue", "lt", "10000000.00", period=SRDI_PERIOD),
                    compare("equityFinancing", "gte", "20000000.00",
                            period={"startDate": "2023-01-01", "endDate": "2024-12-31"}),
                ],
            },
            {
                "op": "and",
                "children": [
                    compare("revenue", "gte", "10000000.00", period=SRDI_PERIOD),
                    compare("rdExpenseRevenueRatio", "gte", "0.03", period=SRDI_PERIOD),
                ],
            },
        ],
    }
    assert validate_expression(merged) == []
    assert (
        policy_contradictions(
            [
                rule("SRDI-001", merged),
                rule("SRDI-002", compare("rdExpense", "gte", "1000000.00", period=SRDI_PERIOD)),
            ]
        )
        == []
    )


def test_every_disjoint_pair_is_reported_once():
    found = policy_contradictions(
        [
            rule("A", compare("revenue", "gte", "10000000.00")),
            rule("B", compare("revenue", "lt", "10000000.00")),
            rule("C", compare("revenue", "eq", "5000000.00")),
        ]
    )
    # A×B、A×C（5e6 < 1e7 → gte 1e7 与 eq 5e6 无交集）、B×C 有解。
    assert sorted(item.criterion_ids for item in found) == [("A", "B"), ("A", "C")]


def test_an_expression_that_is_not_an_atomic_compare_is_skipped():
    assert policy_contradictions([rule("A", INDUSTRY_GATE)]) == []
    assert policy_contradictions([rule("A", {})]) == []
