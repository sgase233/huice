"""补问排序（FR-05）：一次只给一个全局第一问，且顺序可复现。"""

from __future__ import annotations

from datetime import date

import pytest

from app.domain.questioning import DEFAULT_COST, DEFAULT_SENSITIVITY, PendingAssessment, choose_next_question

YEAR = (date(2026, 1, 1), date(2026, 12, 31))
Q1 = (date(2026, 1, 1), date(2026, 3, 31))

INCOME = ("taxableIncome", *YEAR)
INDUSTRY = ("industry", *YEAR)
REGION = ("region", *YEAR)
EMPLOYEES = ("employeeCountStart", *Q1)


def pending(policy_id, *missing, result="insufficient_evidence") -> PendingAssessment:
    return PendingAssessment(policy_id=policy_id, result=result, missing=list(missing))


def test_more_affected_policies_win_over_cheaper_facts():
    question = choose_next_question(
        [pending("a", INCOME, EMPLOYEES), pending("b", INCOME)]
    )

    # 收入命中 2 项待定政策，人数只命中 1 项：前者优先，即使人数更便宜。
    assert question is not None
    assert question.fact_ref == INCOME
    assert "2 项待定政策" in question.reason


def test_tie_on_reach_breaks_by_cost_then_sensitivity():
    # 同为 1 项待定政策：establishedDate 成本 1 低于 taxableIncome 的 2（§5 1–5 档）。
    cost_first = choose_next_question([pending("a", INCOME, ("establishedDate", *YEAR))])
    assert cost_first is not None
    assert cost_first.fact_ref[0] == "establishedDate"

    # 同为 1 项、成本与敏感度都为 1（§5）：档位打平后按 key 字典序，先问 industry。
    sense_first = choose_next_question([pending("a", INDUSTRY, REGION)])
    assert sense_first is not None
    assert sense_first.fact_ref == INDUSTRY

    # 敏感度仍有区分力时以它为准：两者成本相同，敏感度 2 的键让位于敏感度 1 的键。
    sense_second = choose_next_question([pending("a", ("employeeCountStart", *Q1), REGION)])
    assert sense_second is not None
    assert sense_second.fact_ref == REGION


def test_final_tiebreak_is_key_then_period():
    # 同样成本与敏感度，按 key / 期初 / 期末字典序固定。
    question = choose_next_question(
        [pending("a", ("assetsEnd", *Q1), ("assetsStart", *YEAR))]
    )
    assert question is not None
    assert question.fact_ref == ("assetsEnd", *Q1)


def test_only_missing_facts_of_insufficient_results_are_considered():
    question = choose_next_question(
        [
            pending("a", INCOME, result="not_satisfied"),
            pending("b", INCOME, result="conflict"),
            pending("c", REGION),
        ]
    )

    assert question is not None
    assert question.fact_ref == REGION


def test_skipped_fact_refs_are_dropped_entirely():
    assessments = [pending("a", REGION, INCOME)]

    assert choose_next_question(assessments, [INCOME]).fact_ref == REGION
    assert choose_next_question(assessments, [INCOME, REGION]) is None
    # 跳过的是「key+期间」，别的期间的同名事实仍然可以问。
    assert choose_next_question(assessments, [("region", *Q1)]).fact_ref == REGION


def test_no_pending_assessments_means_no_question():
    assert choose_next_question([]) is None
    assert choose_next_question([pending("a")]) is None


def test_same_policy_never_counts_twice_for_one_fact_ref():
    question = choose_next_question([pending("a", INCOME, INCOME), pending("b", REGION)])

    # 重复的引用去重后，两项各命中 1 项政策，于是回到成本/敏感度比较。
    assert question is not None
    assert question.fact_ref == REGION
    assert "1 项待定政策" in question.reason


def test_order_is_independent_of_input_order():
    refs = [INCOME, INDUSTRY, EMPLOYEES, REGION]
    forward = choose_next_question([pending(index, *refs) for index in range(4)])
    backward = choose_next_question([pending(index, *reversed(refs)) for index in reversed(range(4))])

    assert forward is not None and backward is not None
    assert forward.fact_ref == backward.fact_ref


def test_custom_rank_tables_override_the_defaults():
    costs = dict(DEFAULT_COST) | {"taxableIncome": 1}
    sensitivity = dict(DEFAULT_SENSITIVITY) | {"taxableIncome": 1}

    question = choose_next_question([pending("a", INCOME, EMPLOYEES)], costs=costs, sensitivity=sensitivity)

    assert question is not None
    assert question.fact_ref == INCOME
    assert "获取成本档位 1" in question.reason


def test_unknown_key_falls_back_to_the_last_tier():
    question = choose_next_question([pending("a", ("patentCount", *YEAR), INCOME)])

    assert question is not None
    assert question.fact_ref == INCOME  # 已知键成本更低，先问


def test_question_carries_a_prompt_and_rank_one():
    question = choose_next_question([pending("a", REGION)])

    assert question is not None
    assert question.rank == 1
    assert "地区" in question.prompt


@pytest.mark.parametrize(
    ("ref", "fragment"),
    [
        (("patentCount", *Q1), "2026-01-01 至 2026-03-31"),
        (INCOME, "应纳税所得额"),
    ],
)
def test_prompt_falls_back_to_the_period_for_unknown_keys(ref, fragment):
    question = choose_next_question([pending("a", ref)])

    assert question is not None
    assert fragment in question.prompt
