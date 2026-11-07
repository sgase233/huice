"""人工复核单：分层名额、可复现抽样、表格列。

复核是人工计时的，抽歪了（比如 30 条全是「满足」）整轮复核就白做；这里钉住
「每个非空类别至少 1 条」与「同样的输入抽到同样的样本」。
"""

from __future__ import annotations

import csv
import io
from datetime import date

import pytest

from app.ablation.metrics import Run, compare
from app.ablation.review import COLUMNS, FLOORS, SAMPLE_SIZE, allocate, sample, write_sheet


@pytest.fixture(scope="module")
def dataset():
    from app.ablation.dataset import load_dataset

    return load_dataset()


def _runs(dataset, *, conflict_case: str | None = None):
    runs = {}
    for scheme in ("baseline", "pipeline"):
        run = Run(scheme=scheme, model="m", prompt_version="v", manifest=dataset.manifest())
        for label in dataset.scored_labels():
            result = label.result
            if conflict_case and label.case_id == conflict_case:
                result = "conflict"
            run.verdicts[label.key] = {
                "criterionId": label.criterion_code,
                "result": result,
                "reason": "理由",
                "quote": None,
                "facts": [],
            }
        runs[scheme] = run
    return runs


def test_allocate_gives_every_present_state_at_least_one():
    quota = allocate({"satisfied": 100, "not_satisfied": 5, "insufficient_evidence": 40}, 30)
    assert sum(quota.values()) == 30
    assert quota["not_satisfied"] >= 1
    assert quota["satisfied"] > quota["insufficient_evidence"] > quota["not_satisfied"]


def test_allocate_keeps_enough_hard_gate_samples():
    """硬门槛误判是人工复核唯一能看见的地方，不能被比例分配压到几条。"""

    quota = allocate(
        {"satisfied": 71, "not_satisfied": 11, "insufficient_evidence": 64}, 30, floors=FLOORS
    )
    assert sum(quota.values()) == 30
    assert quota["not_satisfied"] >= FLOORS["not_satisfied"]


def test_allocate_returns_everything_when_sample_is_big(dataset):
    counts = {"satisfied": 3, "not_satisfied": 1}
    assert allocate(counts, 30) == counts


def test_allocate_ignores_absent_states():
    quota = allocate({"satisfied": 10, "conflict": 0}, 10)
    assert quota == {"satisfied": 10}


def test_sample_is_stratified_and_reproducible(dataset):
    runs = _runs(dataset)
    first = sample(dataset, runs, size=SAMPLE_SIZE, seed=7)
    again = sample(dataset, runs, size=SAMPLE_SIZE, seed=7)
    assert [(item.scheme, item.label.key) for item in first] == [
        (item.scheme, item.label.key) for item in again
    ]
    per_scheme: dict[str, int] = {}
    states: dict[str, set[str]] = {}
    for item in first:
        per_scheme[item.scheme] = per_scheme.get(item.scheme, 0) + 1
        states.setdefault(item.scheme, set()).add(item.verdict["result"])
    assert per_scheme == {"baseline": 30, "pipeline": 30}
    assert states["baseline"] == {"satisfied", "not_satisfied", "insufficient_evidence"}


def test_sample_covers_conflict_when_present(dataset):
    runs = _runs(dataset, conflict_case="C001")
    picked = sample(dataset, runs, size=SAMPLE_SIZE, seed=7)
    assert any(item.verdict["result"] == "conflict" for item in picked)


def test_sheet_has_the_columns_the_reviewer_fills(dataset):
    runs = _runs(dataset)
    picked = sample(dataset, runs, size=4, seed=1)
    buffer = io.StringIO()
    count = write_sheet(buffer, dataset, picked)
    assert count == len(picked)
    rows = list(csv.reader(io.StringIO(buffer.getvalue())))
    assert tuple(rows[0]) == COLUMNS
    assert len(rows) == count + 1
    # 复核人自己填的三列留空，其余都由程序填好。
    for row in rows[1:]:
        assert row[COLUMNS.index("复核结论")] == ""
        assert row[COLUMNS.index("耗时秒")] == ""
        assert row[COLUMNS.index("引文可证实")] in {"未引用", "是", "否", "原文缺失"}


def test_compare_reports_the_date_used_for_timeliness(dataset):
    runs = _runs(dataset)
    payload = compare(dataset, runs, on=date(2026, 11, 6))
    assert payload["as_of"] == "2026-11-06"
    assert payload["same_input"] is True
