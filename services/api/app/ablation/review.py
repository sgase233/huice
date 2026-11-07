"""人工复核单：每个方案按四态分层抽 30 条，生成一张可以直接计时的 CSV。

为什么分层而不是随机抽 30 条：标注里 ``satisfied`` 占一半、``not_satisfied`` 只有
11 条，纯随机抽样大概率一条硬门槛都抽不到，人工复核就等于只验了「满足」这一类。
分层后每个非空类别至少 1 条，其余按比例分配，条数固定便于换算每人每分钟多少条。

抽样**可复现**：随机种子由「种子值 + 方案 + 类别」拼成，同一份 JSONL 每次抽到的是
同一批，复核记录才能对应回具体条件。留空的只有复核人自己填的几列。

引文一列先由程序判一次「能否在原文里逐字找到」，让复核人只需要判断「这条结论对不对」，
不必再回去翻正文；找不到原文的条件（见 ``Dataset.unverifiable_criteria``）标「原文缺失」。
"""

from __future__ import annotations

import csv
import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from .dataset import Case, Dataset, Label
from .metrics import Run, load_run
from .prompt import quote_supported

#: 每个方案抽多少条。
SAMPLE_SIZE = 30

#: 抽样种子：换一个值就是另一批样本，报告里要写用了哪个。
SEED = 20261106

#: 固定类别顺序，保证并列时的取舍不随字典顺序变。
STATES = ("satisfied", "not_satisfied", "insufficient_evidence", "conflict")

#: 底数：硬门槛误判（标注不满足却判满足）只有人工看得见，30 条里至少留 5 条。
FLOORS = {"not_satisfied": 5}

COLUMNS = (
    "方案",
    "case_id",
    "条件代号",
    "企业",
    "政策版本",
    "条款",
    "原文片段",
    "标注结论",
    "方案结论",
    "方案理由",
    "方案引文",
    "引文可证实",
    "与标注一致",
    "复核结论",
    "耗时秒",
    "备注",
)


def allocate(
    counts: Mapping[str, int], size: int, *, floors: Mapping[str, int] | None = None
) -> dict[str, int]:
    """按比例分配名额，最大余数法；每个非空类别先给底数（默认 1 条）。

    纯按比例会把 ``not_satisfied`` 抽到 3 条左右——那是硬门槛误判唯一能被人工看见
    的地方，样本太少等于没复核，所以给它单独抬高底数（见 ``FLOORS``）。
    """

    floors = floors or {}
    present = {state: counts.get(state, 0) for state in STATES if counts.get(state, 0) > 0}
    total = sum(present.values())
    if total <= size:
        return dict(present)
    base = {state: min(floors.get(state, 1), present[state]) for state in present}
    if len(present) >= size:
        return {state: 1 for state in present}
    while sum(base.values()) > size:  # 底数之和超过名额时先削最低优先级的类别
        budget_hog = max(base, key=lambda state: (base[state], -STATES.index(state)))
        base[budget_hog] -= 1
    remaining = size - sum(base.values())
    spare = {state: present[state] - base[state] for state in present}
    spare_total = sum(spare.values())
    if spare_total:
        weights = {state: spare[state] * remaining / spare_total for state in present}
        for state in present:
            base[state] += int(weights[state])
        left = size - sum(base.values())
        order = sorted(present, key=lambda state: (-(weights[state] % 1), STATES.index(state)))
        for state in order[:left]:
            base[state] += 1
    return base


@dataclass(frozen=True, slots=True)
class Sample:
    scheme: str
    case: Case
    label: Label
    verdict: Mapping[str, Any]


def _verdicts_by_state(run: Run, labels: Sequence[Label]) -> dict[str, list[tuple[Label, Mapping[str, Any]]]]:
    buckets: dict[str, list[tuple[Label, Mapping[str, Any]]]] = {state: [] for state in STATES}
    for label in labels:
        item = run.verdicts.get(label.key)
        if item is None:
            continue
        state = str(item["result"])
        if state in buckets:
            buckets[state].append((label, item))
    return buckets


def sample(
    dataset: Dataset, runs: Mapping[str, Run], *, size: int = SAMPLE_SIZE, seed: int = SEED
) -> list[Sample]:
    """每个方案各抽 ``size`` 条，按方案结论分层。"""

    labels = dataset.scored_labels()
    picked: list[Sample] = []
    for scheme, run in runs.items():
        buckets = _verdicts_by_state(run, labels)
        quota = allocate(
            {state: len(items) for state, items in buckets.items()}, size, floors=FLOORS
        )
        for state in STATES:
            items = buckets[state]
            if not items:
                continue
            take = min(quota.get(state, 0), len(items))
            chooser = random.Random(f"{seed}:{scheme}:{state}")
            for label, item in chooser.sample(items, take):
                picked.append(
                    Sample(scheme=scheme, case=dataset.case_for(label.case_id), label=label, verdict=item)
                )
    return picked


def _quote_state(dataset: Dataset, sample: Sample) -> str:
    quote = sample.verdict.get("quote")
    if not (quote or "").strip():
        return "未引用"
    key = (sample.label.policy_code, sample.label.criterion_code)
    if key in dataset.unverifiable_criteria():
        return "原文缺失"
    return "是" if quote_supported(dataset, sample.case, sample.label.criterion_code, quote) else "否"


def write_sheet(stream: TextIO, dataset: Dataset, samples: Iterable[Sample]) -> int:
    writer = csv.writer(stream)
    writer.writerow(COLUMNS)
    count = 0
    for sample in samples:
        criterion = dataset.criterion(sample.case, sample.label.criterion_code)
        policy = dataset.policies[(sample.case.policy_code, sample.case.document)]
        writer.writerow(
            [
                sample.scheme,
                sample.label.case_id,
                sample.label.criterion_code,
                dataset.enterprises[sample.case.enterprise].name,
                policy.document,
                criterion.section,
                criterion.source_span,
                sample.label.result,
                sample.verdict.get("result"),
                sample.verdict.get("reason"),
                sample.verdict.get("quote") or "",
                _quote_state(dataset, sample),
                "是" if sample.verdict.get("result") == sample.label.result else "否",
                "",
                "",
                "",
            ]
        )
        count += 1
    return count


def main(argv: Sequence[str] | None = None, *, dataset: Dataset | None = None) -> int:
    """``python -m app.ablation review-sheet --baseline a.jsonl --pipeline b.jsonl``。"""

    import argparse
    import sys

    from .dataset import load_dataset

    parser = argparse.ArgumentParser(
        prog="python -m app.ablation review-sheet", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--pipeline", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True, help="复核单 CSV 路径")
    parser.add_argument("--size", type=int, default=SAMPLE_SIZE, help="每方案抽多少条")
    parser.add_argument("--seed", type=int, default=SEED, help="抽样种子，报告里要写")
    parser.add_argument("--data", type=Path, help="data/ 目录，默认仓库根下的 data")
    args = parser.parse_args(argv)

    data = dataset if dataset is not None else load_dataset(args.data)
    runs = {"baseline": load_run(args.baseline), "pipeline": load_run(args.pipeline)}
    samples = sample(data, runs, size=args.size, seed=args.seed)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig：Excel 直接双击打开不乱码，复核人不用先换编码。
    with args.out.open("w", encoding="utf-8-sig", newline="") as handle:
        count = write_sheet(handle, data, samples)
    print(f"抽了 {count} 条（种子 {args.seed}），写入 {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover - 由 __main__ 分发
    raise SystemExit(main())
