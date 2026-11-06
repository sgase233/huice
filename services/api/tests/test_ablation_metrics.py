"""指标口径：分母、硬门槛、无依据断言、引用可证实、时效、用量未知。

每个指标都有「怎么算」的定义，这里逐条钉住；口径一改，报告里的数就跟着变，
所以断言写的是具体条数与分母，不是「大于零」。
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from app.ablation.metrics import (
    MetricsError,
    Run,
    compare,
    confusion,
    hard_gate_misses,
    load_run,
    quote_support,
    render,
    summarise,
    timeliness,
    tokens,
    unsupported_assertions,
    wilson,
)


@pytest.fixture(scope="module")
def dataset():
    from app.ablation.dataset import load_dataset

    return load_dataset()


def _run(dataset, *, scheme="baseline", manifest=None, result_map=None, quote="", facts=None):
    """按标注生成一份「全对」的跑数结果，再按需要改个别条件。"""

    run = Run(
        scheme=scheme,
        model="test-model",
        prompt_version="single-agent@1",
        manifest=manifest if manifest is not None else dataset.manifest(),
    )
    for label in dataset.scored_labels():
        result = (result_map or {}).get(label.key, label.result)
        run.verdicts[label.key] = {
            "criterionId": label.criterion_code,
            "result": result,
            "reason": "测试",
            "quote": quote,
            "facts": facts if facts is not None else [],
        }
        run.case_flags.setdefault(label.case_id, {"expired": False, "summary": ""})
    return run


def test_perfect_run_scores_hundred_percent(dataset):
    run = _run(dataset, quote="")
    summary = summarise(dataset, run, on=date(2026, 11, 6))
    assert summary["accuracy"]["total"] == 146
    assert summary["accuracy"]["accuracy"] == 1.0
    assert summary["accuracy_dev"]["total"] == 120
    assert summary["accuracy_holdout"]["total"] == 26
    assert summary["hard_gate"]["count"] == 0
    assert summary["hard_gate"]["denominator"] == 11


def test_conflict_counts_as_wrong_and_is_reported(dataset):
    labels = dataset.scored_labels()
    target = labels[0]
    run = _run(dataset, result_map={target.key: "conflict"})
    result = confusion(run, labels)
    assert result["correct"] == 145
    assert result["conflicts"] == 1


def test_hard_gate_miss_is_counted_separately(dataset):
    labels = dataset.scored_labels()
    gate = next(label for label in labels if label.result == "not_satisfied")
    run = _run(dataset, result_map={gate.key: "satisfied"})
    result = hard_gate_misses(run, labels)
    assert result["count"] == 1
    assert result["denominator"] == 11
    assert result["cases"] == [f"{gate.case_id}/{gate.criterion_code}"]


def test_unsupported_assertion_means_no_quote_and_no_facts(dataset):
    labels = dataset.scored_labels()
    definite = [label for label in labels if label.result in {"satisfied", "not_satisfied"}]
    run = _run(dataset)  # 全部结论都不带引文与事实
    result = unsupported_assertions(run, labels)
    assert result["denominator"] == len(definite)
    assert result["count"] == len(definite)


def test_quote_support_skips_criterion_without_usable_source(dataset):
    labels = dataset.scored_labels()
    run = _run(dataset, quote="")
    for label in labels:
        # 每条条件引它自己的原文片段：这才是「引用可证实」该看的。
        criterion = dataset.criterion(dataset.case_for(label.case_id), label.criterion_code)
        run.verdicts[label.key]["quote"] = criterion.spans[0]
    result = quote_support(dataset, run, labels)
    # 除 RD-001 外都有引文可查；RD-001 出现在 6 个 case 里，这 6 条都不进分母。
    assert result["unverifiable_criteria_skipped"] == ["RD-001"]
    assert result["denominator"] == 140
    assert result["supported"] == 140


def test_quote_support_flags_unverifiable_quote(dataset):
    labels = dataset.scored_labels()
    run = _run(dataset, quote="这句话不在任何原文里")
    result = quote_support(dataset, run, labels)
    assert result["supported"] == 0
    assert len(result["cases"]) == 140


def test_timeliness_counts_only_expired_cases(dataset):
    run = _run(dataset)
    on = date(2026, 11, 6)
    result = timeliness(dataset, run, on=on)
    assert result["denominator"] == 12  # SRDI 6 个 + 高企 6 个
    assert result["flagged"] == 0
    for case_id in result["cases"]:
        run.case_flags[case_id] = {"expired": True, "summary": ""}
    assert timeliness(dataset, run, on=on)["flagged"] == 12


def test_tokens_do_not_fill_unknown_usage_with_zero(dataset):
    run = _run(dataset)
    run.usage["C001"] = {"prompt": 100, "completion": 20, "known": True, "elapsed_ms": 5, "attempts": 1}
    run.usage["C002"] = {"prompt": 0, "completion": 0, "known": False, "elapsed_ms": 7, "attempts": 1}
    result = tokens(run)
    assert result["cases"] == 2
    assert result["usage_unknown_cases"] == 1
    assert result["total"] == 120


def test_wilson_interval_is_wide_at_eleven_samples():
    low, high = wilson(0, 11)
    assert low == pytest.approx(0.0, abs=1e-12)
    assert 0.2 < high < 0.3
    assert wilson(0, 0) == [None, None]


def test_load_run_requires_header_and_round_trips(tmp_path):
    path = tmp_path / "run.jsonl"
    header = {"record": "header", "scheme": "pipeline", "model": "m", "prompt_version": "v",
              "manifest": {"a": "b"}}
    record = {
        "case_id": "C001",
        "outcome": "ok",
        "usage_prompt": 1,
        "usage_completion": 2,
        "usage_known": True,
        "elapsed_ms": 3,
        "attempts": 1,
        "payload": {
            "results": [{"criterionId": "SME-001", "result": "satisfied", "reason": "r",
                         "quote": None, "facts": []}],
            "staleReasons": ["policy_expired"],
        },
    }
    path.write_text(
        json.dumps(header, ensure_ascii=False) + "\n" + json.dumps(record, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    run = load_run(path)
    assert run.scheme == "pipeline"
    assert run.case_flags["C001"]["expired"] is True
    assert run.verdicts[("C001", "SME-001")]["result"] == "satisfied"

    path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    with pytest.raises(MetricsError, match="表头"):
        load_run(path)


def test_failed_case_is_listed_not_dropped(tmp_path):
    path = tmp_path / "run.jsonl"
    header = json.dumps({"record": "header", "scheme": "baseline", "manifest": {}})
    failed = json.dumps({"case_id": "C009", "outcome": "format_error", "error": "少给了一条"})
    path.write_text(f"{header}\n{failed}\n", encoding="utf-8")
    run = load_run(path)
    assert list(run.failures) == ["C009"]
    assert run.verdicts == {}


def test_compare_marks_different_inputs(dataset):
    left = _run(dataset, scheme="baseline")
    right = _run(dataset, scheme="pipeline", manifest={"评价/labels.csv": "0" * 64})
    payload = compare(dataset, {"baseline": left, "pipeline": right}, on=date(2026, 11, 6))
    assert payload["same_input"] is False
    text = render(payload, names=(("baseline", "基线"), ("pipeline", "现方案")))
    assert "输入哈希不一致" in text
    assert "四态一致率" in text


def test_render_has_one_column_per_scheme(dataset):
    runs = {"baseline": _run(dataset, scheme="baseline"), "pipeline": _run(dataset, scheme="pipeline")}
    payload = compare(dataset, runs, on=date(2026, 11, 6))
    lines = render(payload, names=(("baseline", "基线"), ("pipeline", "现方案"))).splitlines()
    assert lines[0].count("|") == 4
    assert "输入哈希不一致" not in "\n".join(lines)


def test_render_does_not_print_token_counts_as_percentages(dataset):
    """token 数是计数不是比例：5250.5 曾被印成「525050.0%」（2026-11-06 实跑发现）。

    渲染表格前按「值是 float 就是比例」判断，基线每 case 中位 token 是小数，
    一进报告就是明显算错的数字。
    """

    run = _run(dataset)
    run.usage = {"C001": {"prompt": 1000, "completion": 500, "known": True, "elapsed_ms": 2000}}
    payload = compare(dataset, {"baseline": run}, on=date(2026, 11, 6))
    lines = render(payload, names=(("baseline", "基线"),)).splitlines()
    token_row = next(line for line in lines if "token 合计" in line)
    median_row = next(line for line in lines if "中位每 case" in line)
    assert token_row.split("|")[2].strip() == "1500"
    assert median_row.split("|")[2].strip() == "1500"
    assert "%" not in median_row
