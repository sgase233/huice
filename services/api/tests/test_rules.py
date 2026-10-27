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
    SATISFIED,
    CriterionRule,
    FactSnapshot,
    FactTable,
    aggregate,
    evaluate_expression,
    evaluate_policy,
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
