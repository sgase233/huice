"""指标口径：分母、硬门槛、无依据断言、引用可证实、时效、用量未知。

每个指标都有「怎么算」的定义，这里逐条钉住；口径一改，报告里的数就跟着变，
所以断言写的是具体条数与分母，不是「大于零」。
"""

from __future__ import annotations

import io
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


def _dump(run, path):
    """把一份内存里的 Run 写成 JSONL，供 ``main()`` 的端到端用例读回。"""

    header = {
        "record": "header",
        "scheme": run.scheme,
        "model": run.model,
        "prompt_version": run.prompt_version,
        "manifest": run.manifest,
    }
    by_case: dict[str, list[dict]] = {}
    for (case_id, _code), item in run.verdicts.items():
        by_case.setdefault(case_id, []).append(item)
    lines = [json.dumps(header, ensure_ascii=False)]
    for case_id in sorted(by_case):
        lines.append(
            json.dumps(
                {"case_id": case_id, "outcome": "ok", "usage_known": False,
                 "payload": {"results": by_case[case_id]}},
                ensure_ascii=False,
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


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
    # 它的归属改成 2023 年第7号之后会变成可验证，届时分母为 146、本列表为空——
    # 那次改动必须与 CSV 同一个提交，并同步 test_ablation_dataset 的同类断言。
    assert result["unverifiable_criteria_skipped"] == ["RD-001"]
    assert result["denominator"] == 140
    assert result["supported"] == 140


def test_quote_support_flags_unverifiable_quote(dataset):
    labels = dataset.scored_labels()
    run = _run(dataset, quote="这句话不在任何原文里")
    result = quote_support(dataset, run, labels)
    assert result["supported"] == 0
    # 146 条标签减去 RD-001 的 6 条（出处原文不可用，不进分母）。归属修正后为 146。
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


def test_load_run_rejects_a_repeated_case(tmp_path):
    path = tmp_path / "run.jsonl"
    header = json.dumps({"record": "header", "scheme": "baseline", "manifest": {}})
    record = json.dumps({"case_id": "C001", "outcome": "ok", "payload": {"results": []}})
    path.write_text(f"{header}\n{record}\n{record}\n", encoding="utf-8")

    with pytest.raises(MetricsError, match="出现了不止一次"):
        load_run(path)


def test_load_run_rejects_a_repeated_criterion(tmp_path):
    path = tmp_path / "run.jsonl"
    header = json.dumps({"record": "header", "scheme": "baseline", "manifest": {}})
    item = {"criterionId": "SME-001", "result": "satisfied", "reason": "r",
            "quote": None, "facts": []}
    record = json.dumps({"case_id": "C001", "outcome": "ok",
                         "payload": {"results": [item, item]}})
    path.write_text(f"{header}\n{record}\n", encoding="utf-8")

    with pytest.raises(MetricsError, match="两条结论"):
        load_run(path)


def test_an_unanswered_condition_shrinks_the_denominator_and_is_reported(dataset):
    """缺一条结论时旧写法把它悄悄剔出分母，一致率照样好看：这里要求点名并标记不完整。"""

    run = _run(dataset)
    missing_key = dataset.scored_labels()[0].key
    del run.verdicts[missing_key]

    summary = summarise(dataset, run, on=date(2026, 11, 6))

    assert summary["complete"] is False
    assert summary["accuracy"]["missing"] == [f"{missing_key[0]}/{missing_key[1]}"]
    text = render(
        {"same_input": True, "runs": {"baseline": summary}},
        names=(("baseline", "基线"),),
    )
    assert "未返回条数" in text
    assert "不能当作完整对照" in text


def test_an_unanswered_hard_gate_case_is_not_counted_as_correct(dataset):
    # 标注「不满足」却没有结论的，既不是漏判也不是判对：单列未返回
    run = _run(dataset)
    labels = dataset.scored_labels()
    target = next(label for label in labels if label.result == "not_satisfied")
    del run.verdicts[target.key]

    result = hard_gate_misses(run, labels)

    assert f"{target.case_id}/{target.criterion_code}" in result["unreturned"]
    assert f"{target.case_id}/{target.criterion_code}" not in result["cases"]
    assert result["denominator"] == 11
    assert result["judged"] == 10


def test_main_exits_four_when_a_condition_is_unanswered(dataset, tmp_path):
    from app.ablation.metrics import main

    run = _run(dataset)
    del run.verdicts[dataset.scored_labels()[0].key]
    other = _run(dataset, scheme="pipeline")
    baseline_path = tmp_path / "baseline.jsonl"
    pipeline_path = tmp_path / "pipeline.jsonl"
    _dump(run, baseline_path)
    _dump(other, pipeline_path)

    code = main(
        ["--baseline", str(baseline_path), "--pipeline", str(pipeline_path), "--as-of", "2026-11-06"],
        dataset=dataset,
        stream=io.StringIO(),
    )

    assert code == 4


def test_compare_marks_different_inputs(dataset):
    left = _run(dataset, scheme="baseline")
    right = _run(dataset, scheme="pipeline", manifest={"评价/labels.csv": "0" * 64})
    payload = compare(dataset, {"baseline": left, "pipeline": right}, on=date(2026, 11, 6))
    assert payload["same_input"] is False
    text = render(payload, names=(("baseline", "基线"), ("pipeline", "现方案")))
    assert "输入不可比" in text
    assert "四态一致率" in text


def test_compare_refuses_when_one_manifest_is_missing(dataset):
    """一份有清单、一份没有：过滤掉空清单后只剩一个，旧写法会判成「同输入」。"""

    left = _run(dataset, scheme="baseline")
    right = _run(dataset, scheme="pipeline", manifest={})
    payload = compare(dataset, {"baseline": left, "pipeline": right}, on=date(2026, 11, 6))

    assert payload["same_input"] is False
    assert any("pipeline" in problem and "没有输入清单" in problem for problem in payload["input_problems"])


def test_compare_refuses_when_both_manifests_are_empty(dataset):
    runs = {
        "baseline": _run(dataset, scheme="baseline", manifest={}),
        "pipeline": _run(dataset, scheme="pipeline", manifest={}),
    }

    payload = compare(dataset, runs, on=date(2026, 11, 6))

    assert payload["same_input"] is False
    assert len(payload["input_problems"]) == 2  # 两份各自点名


def test_compare_refuses_when_the_two_runs_agree_but_the_data_has_moved(dataset):
    """两份旧跑数彼此一致，但标注已经改过：旧写法只看两份互相一致，仍判「同输入」。"""

    stale = {name: "0" * 64 for name in dataset.manifest()}
    runs = {
        "baseline": _run(dataset, scheme="baseline", manifest=stale),
        "pipeline": _run(dataset, scheme="pipeline", manifest=stale),
    }

    payload = compare(dataset, runs, on=date(2026, 11, 6))

    assert payload["same_input"] is False
    assert any("与当前数据不一致" in problem for problem in payload["input_problems"])


def test_compare_refuses_when_the_manifests_list_different_files(dataset):
    full = dataset.manifest()
    short = {key: full[key] for key in list(full)[:2]}
    runs = {
        "baseline": _run(dataset, scheme="baseline", manifest=full),
        "pipeline": _run(dataset, scheme="pipeline", manifest=short),
    }

    payload = compare(dataset, runs, on=date(2026, 11, 6))

    assert payload["same_input"] is False
    assert any("清单缺" in problem for problem in payload["input_problems"])
    assert any("互不相同" in problem for problem in payload["input_problems"])


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
