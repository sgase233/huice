"""结果解释（FR-06/FR-08）的直接单元用例：只改措辞，不改结论。

模型失败必须退回已存说明并给出降级警告——这条路径在接口层只被间接覆盖，
降级逻辑一旦退化（比如把失败当成功、或把确定性文本换掉），接口测试也发现不了。
"""

from __future__ import annotations

from app.agents.explanation import (
    FIXTURE_WARNING,
    CriterionLine,
    Explanation,
    explain,
)
from app.errors import ApiProblem
from app.integrations.llm import MODEL_UNAVAILABLE_MESSAGE, LlmResult

TITLE = "深圳市中小企业所得税优惠"
CRITERIA = (
    CriterionLine(title="应纳税所得额", result="satisfied", reason="3,000,000.00 ≤ 3,000,000.00"),
    CriterionLine(title="从业人数", result="not_satisfied", reason="350 人 > 300 人"),
)


class FakeLlm:
    """``mode='deepseek'`` 才会走模型分支；``text=None`` 表示调用直接失败。"""

    mode = "deepseek"

    def __init__(self, text: str | None = "模型改写后的说明。", *, mode: str = "deepseek") -> None:
        self._text = text
        self.mode = mode
        self.calls = 0

    def complete(self, *, system: str, user: str, max_tokens: int = 1024) -> LlmResult:
        self.calls += 1
        if self._text is None:
            raise RuntimeError("模型服务不可达")
        return LlmResult(
            text=self._text, model="deepseek-chat", prompt_tokens=12, completion_tokens=34, elapsed_ms=5
        )


def run(**kwargs) -> Explanation:
    return explain(
        policy_title=TITLE,
        result="not_satisfied",
        criteria=CRITERIA,
        handling_mode="tax_self_assessment",
        **kwargs,
    )


def test_without_a_model_the_explanation_is_rule_generated():
    outcome = run()

    assert outcome.mode == "fixture"
    assert outcome.warnings == [FIXTURE_WARNING]
    assert TITLE in outcome.text
    assert "不满足" in outcome.text
    assert "从业人数" in outcome.text


def test_a_fixture_client_is_not_called_even_if_it_could_answer():
    llm = FakeLlm("这段文本不应出现。", mode="fixture")
    outcome = run(llm=llm)

    assert llm.calls == 0
    assert outcome.mode == "fixture"
    assert "这段文本不应出现" not in outcome.text


def test_a_model_failure_falls_back_to_the_saved_explanation():
    baseline = run().text
    outcome = run(llm=FakeLlm(None))

    assert outcome.mode == "saved"
    assert outcome.warnings == [MODEL_UNAVAILABLE_MESSAGE]
    # 降级只换来源，正文必须与规则文本逐字一致：模型失败不能让判断看起来像换了一个结论。
    assert outcome.text == baseline


def test_an_empty_model_answer_also_falls_back():
    baseline = run().text
    outcome = run(llm=FakeLlm("   \n"))

    assert outcome.mode == "saved"
    assert outcome.warnings == [MODEL_UNAVAILABLE_MESSAGE]
    assert outcome.text == baseline


def test_a_successful_model_answer_is_used_verbatim():
    outcome = run(llm=FakeLlm("  本次不满足，从业人数超过上限。  "))

    assert outcome.mode == "deepseek"
    assert outcome.text == "本次不满足，从业人数超过上限。"
    assert outcome.warnings == ["解释由模型改写，结论以规则结果为准。"]


def test_a_successful_model_call_keeps_its_usage_for_recording():
    outcome = run(llm=FakeLlm())

    assert outcome.usage is not None
    assert (outcome.usage.prompt_tokens, outcome.usage.completion_tokens) == (12, 34)
    assert outcome.usage.model == "deepseek-chat"


def test_paths_that_do_not_call_the_model_carry_no_usage():
    # 未调模型的路径不能凭空有用量，否则费用统计会把规则路径算成模型调用。
    assert run().usage is None
    assert run(llm=FakeLlm(mode="fixture")).usage is None
    failed = run(llm=FakeLlm(None))
    assert failed.usage is None
    # 调用失败要留痕：调用方据此记一行 outcome=error，而不是当成「没调用」。
    assert failed.call_failed


def test_an_empty_answer_still_records_the_tokens_it_cost():
    # 模型回了空串也是调用成功：token 已经产生，用量必须留下，只是措辞退回去。
    outcome = run(llm=FakeLlm("   "))

    assert outcome.mode == "saved"
    assert not outcome.call_failed
    assert outcome.usage is not None
    assert (outcome.usage.prompt_tokens, outcome.usage.completion_tokens) == (12, 34)


def test_a_failure_that_may_have_billed_is_reported_as_unknown():
    class TimedOut:
        mode = "deepseek"

        def complete(self, **kwargs):
            raise ApiProblem(
                503, "DEPENDENCY_UNAVAILABLE", MODEL_UNAVAILABLE_MESSAGE,
                retryable=True, usage_unknown=True,
            )

    outcome = run(llm=TimedOut())

    assert outcome.call_failed
    # 超时/连接中断时上游可能已计费，不能记成 0。
    assert outcome.usage_unknown


def test_a_rate_limited_call_is_not_reported_as_billed():
    class RateLimited:
        mode = "deepseek"

        def complete(self, **kwargs):
            raise ApiProblem(429, "UPSTREAM_RATE_LIMITED", "限流", retryable=True)

    outcome = run(llm=RateLimited())

    assert outcome.call_failed
    # 429 是上游明确拒绝，没有产生 token，不能算进「可能已计费」。
    assert not outcome.usage_unknown


def test_missing_facts_are_named_in_the_baseline_once():
    outcome = explain(
        policy_title=TITLE,
        result="insufficient_evidence",
        criteria=(CriterionLine(title="资产总额", result="insufficient_evidence", reason="缺少资产总额"),),
        handling_mode="tax_self_assessment",
        missing_keys=("assetsEnd", "assetsEnd", "employeeCountEnd"),
    )

    assert "仍需补充的事实：assetsEnd、employeeCountEnd。" in outcome.text
    assert outcome.mode == "fixture"
