"""抽取阶段的两个数：幻觉条款率的分母、已仲裁条件的覆盖。

这里用假的模型客户端，但走的是真的 ``extract_criteria`` 模型分支——引文校验、
``manual_review`` 拒绝、表达式校验都是既有逻辑，不能在这里重写一遍。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from app.ablation.extraction import run, run_policy, summarise
from app.integrations.llm import LlmResult


@pytest.fixture(scope="module")
def dataset():
    from app.ablation.dataset import load_dataset

    return load_dataset()


@dataclass
class FakeModel:
    items: list[dict]
    mode: str = "deepseek"
    model_name: str = "fake-deepseek"
    usage_known: bool = True

    def complete(self, *, system: str, user: str, max_tokens: int = 1024) -> LlmResult:
        return LlmResult(
            text=json.dumps(self.items, ensure_ascii=False),
            model=self.model_name,
            prompt_tokens=1000,
            completion_tokens=200,
            elapsed_ms=50,
            usage_known=self.usage_known,
        )


def _policy(dataset):
    return dataset.policies[("CN-TAX-SME-2023-012", "财政部 税务总局公告2023年第12号")]


def test_hallucinated_quote_is_counted_and_dropped(dataset):
    criterion = dataset.criteria[("CN-TAX-SME-2023-012", "SME-002")]
    model = FakeModel(
        items=[
            {"title": "应纳税所得额", "expression": criterion.expression,
             "quote": criterion.spans[0], "section": "第一条"},
            {"title": "编的条款", "expression": criterion.expression,
             "quote": "这句话在正文里根本不存在", "section": "第二条"},
        ]
    )
    row = run_policy(dataset, _policy(dataset), llm=model)
    assert row.accepted == 1
    assert row.hallucinated == 1
    assert row.produced == 2  # 一条收下、一条因引文不实丢弃
    assert row.hallucination_rate == pytest.approx(0.5)
    assert row.codes["EXTRACTION_QUOTE_NOT_IN_BODY"] == 1


def test_manual_review_from_model_is_rejected(dataset):
    criterion = dataset.criteria[("CN-TAX-SME-2023-012", "SME-002")]
    model = FakeModel(
        items=[
            {"title": "模型想卸责", "expression": {"op": "manual_review", "reason": "判不了"},
             "quote": criterion.spans[0], "section": "第一条"},
        ]
    )
    row = run_policy(dataset, _policy(dataset), llm=model)
    assert row.accepted == 0
    assert "EXTRACTION_MANUAL_REVIEW_NOT_ALLOWED" in row.codes
    assert row.hallucinated == 0  # 引文是真的，只是算子不允许


def test_coverage_counts_adjudicated_criteria_found_in_quotes(dataset):
    criterion = dataset.criteria[("CN-TAX-SME-2023-012", "SME-002")]
    model = FakeModel(
        items=[
            {"title": "应纳税所得额", "expression": criterion.expression,
             "quote": criterion.spans[0], "section": "第一条"},
        ]
    )
    row = run_policy(dataset, _policy(dataset), llm=model)
    assert row.expected == 4  # 小微企业政策有 4 条已仲裁条件
    assert row.matched == 1
    assert row.coverage == pytest.approx(0.25)


def test_usage_is_carried_per_policy(dataset):
    criterion = dataset.criteria[("CN-TAX-SME-2023-012", "SME-002")]
    model = FakeModel(
        items=[{"title": "t", "expression": criterion.expression, "quote": criterion.spans[0]}]
    )
    row = run_policy(dataset, _policy(dataset), llm=model)
    assert (row.usage_prompt, row.usage_completion, row.elapsed_ms) == (1000, 200, 50)
    assert row.model == "fake-deepseek"


def test_model_failure_does_not_break_the_round(dataset):
    class Boom:
        mode = "deepseek"
        model_name = "boom"

        def complete(self, **_):
            raise RuntimeError("上游 502")

    row = run_policy(dataset, _policy(dataset), llm=Boom())  # type: ignore[arg-type]
    assert row.status == "error"
    assert "RuntimeError" in (row.error or "")
    assert row.produced == 0


def test_summary_aggregates_rates_over_all_policies(dataset):
    criterion = dataset.criteria[("CN-TAX-SME-2023-012", "SME-002")]
    rows = [
        run_policy(
            dataset,
            _policy(dataset),
            llm=FakeModel(
                items=[{"title": "t", "expression": criterion.expression,
                        "quote": criterion.spans[0]}]
            ),
        )
    ]
    summary = summarise(rows)
    assert summary["policies"] == 1
    assert summary["produced"] == 1
    assert summary["hallucination_rate"] == 0.0
    assert summary["coverage"] == pytest.approx(1 / 4)


def test_run_covers_every_scored_policy_version(dataset):
    model = FakeModel(items=[])
    rows = run(dataset, model)  # type: ignore[arg-type]
    assert {row.policy_code for row in rows} == {case.policy_code for case in dataset.cases}
    assert all(row.produced == 0 for row in rows)
