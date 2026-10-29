"""补问排序（FR-05）：一次只给一个全局第一问，且顺序可复现。"""

from __future__ import annotations

from datetime import date

import pytest

from app.domain.questioning import (
    DEFAULT_COST,
    DEFAULT_SENSITIVITY,
    PROMPTS,
    PendingAssessment,
    choose_next_question,
)

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


def test_credit_record_question_asks_in_the_same_direction_as_the_key():
    """``creditRecord`` 的语义是「诚信记录良好」（true = 好）。

    补问若问成「是否存在限制情形」，用户答「是」会被存成 true，
    规则就按「记录良好」反向判断。问法与键的语义必须同向。
    """

    question = choose_next_question([pending("a", ("creditRecord", *YEAR))])

    assert question is not None
    assert "良好" in question.prompt
    assert "是否存在" not in question.prompt


def test_rd_add_deduction_question_is_positive_too():
    question = choose_next_question([pending("a", ("rdAddDeductionClaimed", *YEAR))])

    assert question is not None
    # true = 期间内各年度均已申报；问句要写明「每个年度都申报填是」。
    assert "每个年度都申报填「是」" in question.prompt


#: 补问文案是按 key 静态定义的，并不知道这次 ``factRef.period`` 是哪一年；界面会把期间
#: 单独展示。文案里一旦出现相对时间词，用户就可能照文案字面填成别的年份——错年份的值
#: 仍是合法十进制数，精度/符号/范围校验全过，于是判错也不报错（fail-wrong，不是 fail-safe）。
#: 「上一期间」不在此列：增量公式本来就要相对上一期间说明。
RELATIVE_TIME_WORDS = (
    "本年度",
    "上年度",
    "前年度",
    "近两年",
    "本次判断",
    "该年度",
    "该季度",
)


def test_prompt_text_never_anchors_on_a_relative_period():
    offenders = {
        key: word
        for key, prompt in PROMPTS.items()
        for word in RELATIVE_TIME_WORDS
        if word in prompt
    }

    assert offenders == {}, f"补问文案不许写相对时间词，改成「该期间」：{offenders}"


def test_expense_increase_prompt_states_the_formula():
    """增量是标注方预计算的值，用户自己算也必须得到同一个数，否则差一个年度的量级。"""

    prompt = PROMPTS["rdExpenseIncrease"]

    assert "该期间的研发费用" in prompt
    assert "上一期间的研发费用" in prompt
    assert "负数" in prompt


# ------------------------------------------------- rc.4 新增键的补问（2026-10-29）


def test_every_fact_key_has_a_prompt_and_both_tiers():
    """事实字典扩键时最容易漏的就是这三张静态表：漏一条补问，页面只能显示键名。"""

    from app.domain.rules import FACT_UNITS

    assert set(FACT_UNITS) - set(PROMPTS) == set()
    assert set(FACT_UNITS) - set(DEFAULT_COST) == set()
    assert set(FACT_UNITS) - set(DEFAULT_SENSITIVITY) == set()


def test_specific_market_question_asks_for_a_date_not_a_number_of_years():
    """年限是相对量：问「几年」会存下一个随判断年份漂移的数，同一份快照重算结果不同。"""

    question = choose_next_question([pending("a", ("specificMarketSince", *YEAR))])

    assert question is not None
    assert "YYYY-MM-DD" in question.prompt
    assert "不是年数" in question.prompt


def test_independent_legal_entity_question_is_positive_too():
    question = choose_next_question([pending("a", ("independentLegalEntity", *YEAR))])

    assert question is not None
    assert "具有独立法人资格" in question.prompt
    assert "是否不具有" not in question.prompt
