"""指标计算：同一份标注上比两个方案，输出可以直接贴进评测报告的表。

两个方案的 JSONL 用同一个逐条件形状，所以这里不分支：

    {"case_id": "C001", "payload": {"results": [{criterionId, result, reason, quote, facts}], ...},
     "usage_prompt": 1234, "usage_completion": 56, "usage_known": true, "outcome": "ok", ...}

指标定义（对齐策划案第 7 节，逐条写明「怎么算」而不是「大概多好」）：

* **四态一致率**：与 ``adjudicated_result`` 相同的条数 ÷ 分母。分母由标注决定
  （当前 146 条，排除 2008 版那 2 条，见 ``dataset.EXCLUDED_VERSIONS``），dev /
  holdout 分列。标注只有三类，方案输出 ``conflict`` 一律算不一致，并单列条数。
  没返回结论的条件不进分母，但会单列「未返回条数」——分母变小会抬高一致率，
  有未返回项时不能当作完整对照。
* **硬门槛误判**：标注 ``not_satisfied`` 却判 ``satisfied`` 的条数 ÷ 标注为
  ``not_satisfied`` 且返回了结论的条数（当前 11 条），附 Wilson 95% 区间；这个
  分母下区间很宽，报告只说「实测 0 例」，不说「零风险」。没返回的同样单列，
  不按「没误判」处理。
* **无依据断言率**：结论是 satisfied 或 not_satisfied（不是「证据不足」），却既
  没有引用原文、也没有列任何企业事实的条数 ÷ 全部确定结论条数。这是「幻觉」
  在判断阶段的等价物：抽取阶段的幻觉条款率只有现方案有（基线没有抽取阶段）。
* **引用可证实率**：给出的引文能在原文里逐字找到的比例。原文不可用的条件
  （``Dataset.unverifiable_criteria``）不进分母，并会在结果里逐个列出来。
* **时效识别**：已过适用期的 case 里，方案标出「已过期 / 申报窗口已关闭」的
  比例。分母按运行当天的日期算，报告必须写运行日期。
* **token**：冷启动（把政策装进方案的那部分）与边际（每个 case）分开报；
  用量未知的调用不补 0，单列条数。
"""

from __future__ import annotations

import json
import math
import statistics
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from .dataset import LABEL_RESULTS, Case, Dataset, Label
from .prompt import quote_supported

DEFINITE = ("satisfied", "not_satisfied")
EXPIRY_FLAGS = ("policy_expired", "window_closed")


class MetricsError(Exception):
    """跑数文件与数据对不上时抛出；消息里点名 case 与条件。"""


@dataclass(slots=True)
class Run:
    scheme: str
    model: str
    prompt_version: str
    manifest: dict[str, str]
    verdicts: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    case_flags: dict[str, dict[str, Any]] = field(default_factory=dict)
    usage: dict[str, dict[str, Any]] = field(default_factory=dict)
    failures: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return f"{self.scheme}（{self.model}，{self.prompt_version}）"


def load_run(path: Path) -> Run:
    """读一份 JSONL。表头必须在第一行，缺表头就不知道该按哪一轮实验解释。"""

    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        raise MetricsError(f"{path.name} 是空的")
    header = json.loads(lines[0])
    if header.get("record") != "header":
        raise MetricsError(f"{path.name} 第一行不是表头")
    run = Run(
        scheme=str(header.get("scheme") or ""),
        model=str(header.get("model") or ""),
        prompt_version=str(header.get("prompt_version") or ""),
        manifest=dict(header.get("manifest") or {}),
    )
    seen: set[str] = set()
    for raw in lines[1:]:
        record = json.loads(raw)
        case_id = str(record.get("case_id") or "")
        if not case_id:
            raise MetricsError(f"{path.name} 有一行没有 case_id")
        # 同一个 case 出现两行时，后一行会静默盖掉前一行：指标照算，但算的是哪一条没人知道
        if case_id in seen:
            raise MetricsError(
                f"{path.name} 的 {case_id} 出现了不止一次；重复行会互相覆盖，先合并再算指标"
            )
        seen.add(case_id)
        if record.get("outcome") != "ok" or not record.get("payload"):
            run.failures[case_id] = record
            continue
        payload = record["payload"]
        run.usage[case_id] = {
            "prompt": int(record.get("usage_prompt") or 0),
            "completion": int(record.get("usage_completion") or 0),
            "known": bool(record.get("usage_known")),
            "elapsed_ms": int(record.get("elapsed_ms") or 0),
            "attempts": int(record.get("attempts") or 1),
        }
        run.case_flags[case_id] = {
            "expired": _expired_flag(payload),
            "summary": payload.get("summary") or "",
        }
        for item in payload.get("results") or []:
            code = str(item.get("criterionId") or "")
            if not code:
                raise MetricsError(f"{path.name} 的 {case_id} 有结论没写 criterionId")
            if (case_id, code) in run.verdicts:
                raise MetricsError(
                    f"{path.name} 的 {case_id}/{code} 有两条结论；重复会让后一条覆盖前一条，"
                    "先确认哪一条是真的"
                )
            run.verdicts[(case_id, code)] = item
    return run


def _expired_flag(payload: Mapping[str, Any]) -> bool:
    """两个方案各自怎么表达「已过期」：基线给 ``policyExpired``，流水线给
    ``staleReasons`` 里的代码。这里统一成布尔，口径以「该政策版本已过适用期」为准。"""

    if payload.get("policyExpired") is True:
        return True
    reasons = payload.get("staleReasons") or []
    for reason in reasons:
        code = reason.get("code") if isinstance(reason, dict) else str(reason)
        if code in EXPIRY_FLAGS:
            return True
    return False


# ------------------------------------------------------------------ 单项指标


def confusion(run: Run, labels: Sequence[Label]) -> dict[str, Any]:
    matrix: Counter[tuple[str, str]] = Counter()
    missing: list[str] = []
    for label in labels:
        item = run.verdicts.get((label.case_id, label.criterion_code))
        if item is None:
            missing.append(f"{label.case_id}/{label.criterion_code}")
            continue
        matrix[(label.result, str(item["result"]))] += 1
    total = sum(matrix.values())
    correct = sum(count for (truth, got), count in matrix.items() if truth == got)
    by_truth = {name: sum(count for (truth, _), count in matrix.items() if truth == name)
                for name in LABEL_RESULTS}
    return {
        "matrix": {f"{truth}->{got}": count for (truth, got), count in sorted(matrix.items())},
        "total": total,
        "correct": correct,
        "accuracy": correct / total if total else None,
        "by_truth": by_truth,
        "conflicts": sum(count for (_, got), count in matrix.items() if got == "conflict"),
        "missing": missing,
    }


def hard_gate_misses(run: Run, labels: Sequence[Label]) -> dict[str, Any]:
    """标注「不满足」被判成「满足」——这是最贵的错误，单独数。"""

    truths = [label for label in labels if label.result == "not_satisfied"]
    # 没返回结论的不能算「没误判」：那是没判，不是判对了（M2 在 #41 的 P2）
    unreturned = [
        f"{label.case_id}/{label.criterion_code}"
        for label in truths
        if (label.case_id, label.criterion_code) not in run.verdicts
    ]
    missed = [
        f"{label.case_id}/{label.criterion_code}"
        for label in truths
        if run.verdicts.get((label.case_id, label.criterion_code), {}).get("result") == "satisfied"
    ]
    judged = len(truths) - len(unreturned)
    return {
        "denominator": len(truths),
        "judged": judged,
        "unreturned": unreturned,
        "count": len(missed),
        "rate": len(missed) / judged if judged else None,
        "wilson": wilson(len(missed), judged),
        "cases": missed,
    }


def unsupported_assertions(run: Run, labels: Sequence[Label]) -> dict[str, Any]:
    """给出确定结论，却既没引用原文也没列事实。"""

    definite = 0
    unsupported: list[str] = []
    for label in labels:
        item = run.verdicts.get((label.case_id, label.criterion_code))
        if item is None or item.get("result") not in DEFINITE:
            continue
        definite += 1
        if not (item.get("quote") or "").strip() and not (item.get("facts") or []):
            unsupported.append(f"{label.case_id}/{label.criterion_code}")
    return {
        "denominator": definite,
        "count": len(unsupported),
        "rate": len(unsupported) / definite if definite else None,
        "cases": unsupported,
    }


def quote_support(dataset: Dataset, run: Run, labels: Sequence[Label]) -> dict[str, Any]:
    """引文能否逐字落到原文；出处原文不可用的条件不进分母，并在结果里列出。"""

    skip = dataset.unverifiable_criteria()
    checked = 0
    supported = 0
    unsupported: list[str] = []
    for label in labels:
        if (label.policy_code, label.criterion_code) in skip:
            continue
        item = run.verdicts.get((label.case_id, label.criterion_code))
        if item is None:
            continue
        quote = item.get("quote")
        if not (quote or "").strip():
            continue  # 没引用不在这里扣分，由「无依据断言率」去说
        checked += 1
        case = dataset.case_for(label.case_id)
        if quote_supported(dataset, case, label.criterion_code, quote):
            supported += 1
        else:
            unsupported.append(f"{label.case_id}/{label.criterion_code}")
    return {
        "denominator": checked,
        "supported": supported,
        "rate": supported / checked if checked else None,
        "unverifiable_criteria_skipped": sorted(f"{code}" for _, code in skip),
        "cases": unsupported,
    }


def timeliness(dataset: Dataset, run: Run, *, on: date) -> dict[str, Any]:
    """已过适用期的 case 里，方案有没有把「过期」说出来。"""

    expired: list[Case] = []
    for case in dataset.cases:
        policy = dataset.policies[(case.policy_code, case.document)]
        if policy.effective_to is not None and policy.effective_to < on:
            expired.append(case)
    flagged = [case.case_id for case in expired if run.case_flags.get(case.case_id, {}).get("expired")]
    return {
        "as_of": on.isoformat(),
        "denominator": len(expired),
        "flagged": len(flagged),
        "rate": len(flagged) / len(expired) if expired else None,
        "cases": sorted(case.case_id for case in expired),
        "missed": sorted(case.case_id for case in expired if case.case_id not in flagged),
    }


def tokens(run: Run) -> dict[str, Any]:
    known = [item for item in run.usage.values() if item["known"]]
    unknown = len(run.usage) - len(known)
    per_case = [item["prompt"] + item["completion"] for item in known]
    return {
        "cases": len(run.usage),
        "usage_unknown_cases": unknown,
        "total_prompt": sum(item["prompt"] for item in known),
        "total_completion": sum(item["completion"] for item in known),
        "total": sum(per_case),
        "median_per_case": statistics.median(per_case) if per_case else None,
        "median_elapsed_ms": statistics.median([item["elapsed_ms"] for item in known])
        if known else None,
    }


def wilson(successes: int, trials: int, z: float = 1.96) -> list[float | None]:
    """Wilson 区间。11 条样本下的 0 例不是「零风险」，报告要带这个区间。"""

    if trials == 0:
        return [None, None]
    phat = successes / trials
    denominator = 1 + z * z / trials
    centre = (phat + z * z / (2 * trials)) / denominator
    margin = z * math.sqrt(phat * (1 - phat) / trials + z * z / (4 * trials * trials)) / denominator
    return [max(0.0, centre - margin), min(1.0, centre + margin)]


# ------------------------------------------------------------------ 汇总


def summarise(dataset: Dataset, run: Run, *, on: date) -> dict[str, Any]:
    scored = dataset.scored_labels()
    dev = [label for label in scored if label.split == "dev"]
    holdout = [label for label in scored if label.split == "holdout"]
    accuracy = confusion(run, scored)
    summary = {
        "scheme": run.scheme,
        "model": run.model,
        "prompt_version": run.prompt_version,
        "labels": len(scored),
        "failures": sorted(run.failures),
        "accuracy": accuracy,
        "accuracy_dev": confusion(run, dev),
        "accuracy_holdout": confusion(run, holdout),
        "hard_gate": hard_gate_misses(run, scored),
        "unsupported": unsupported_assertions(run, scored),
        "quotes": quote_support(dataset, run, scored),
        "timeliness": timeliness(dataset, run, on=on),
        "tokens": tokens(run),
    }
    # 完整对照：每个预期 case/条件都要有结论。缺的已在 ``accuracy.missing`` 里点名；
    # 重复在 ``load_run`` 就拒绝了，不会走到这里。
    summary["complete"] = not accuracy["missing"]
    return summary


def compare(dataset: Dataset, runs: Mapping[str, Run], *, on: date) -> dict[str, Any]:
    """两个方案的汇总 + 输入是否同一份的核对。

    三个条件全满足才算同一份输入：每份清单都存在且键集合与当前数据一致；两份清单彼此
    一致；清单与**现在读到的** ``dataset.manifest()`` 一致。只把两份跑数头互相比是不够
    的——标注改过之后，两份旧跑数仍然互相一致，指标却已经不在同一份输入上了
    （M2 在 #41 的 P1）。
    """

    expected = dataset.manifest()
    manifests = {name: dict(run.manifest) for name, run in runs.items()}
    problems: list[str] = []
    for name, manifest in manifests.items():
        if not manifest:
            problems.append(f"{name}：跑数头里没有输入清单，证明不了跑的是哪份数据")
            continue
        missing = sorted(set(expected) - set(manifest))
        extra = sorted(set(manifest) - set(expected))
        if missing:
            problems.append(f"{name}：清单缺 {', '.join(missing)}")
        if extra:
            problems.append(f"{name}：清单多出 {', '.join(extra)}")
        changed = sorted(key for key in set(manifest) & set(expected) if manifest[key] != expected[key])
        if changed:
            problems.append(f"{name}：与当前数据不一致的输入 {', '.join(changed)}")
    if len({json.dumps(manifest, sort_keys=True) for manifest in manifests.values()}) > 1:
        problems.append("两份跑数的输入清单互不相同")
    return {
        "as_of": on.isoformat(),
        "same_input": not problems,
        "input_problems": problems,
        "manifests": manifests,
        "runs": {name: summarise(dataset, run, on=on) for name, run in runs.items()},
    }


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def render(payload: Mapping[str, Any], *, names: Iterable[tuple[str, str]]) -> str:
    """把汇总渲染成 Markdown 表。``names`` 是 (键, 显示名) 的列表，顺序即列顺序。"""

    rows: list[list[str]] = []
    runs = payload["runs"]

    def row(label: str, getter, *, pct: bool = False) -> None:
        # 按行声明是不是比例：早先按「值是 float」判断，token 中位数 5250.5 被当成
        # 比例印成「525050.0%」（2026-11-06 实跑发现的）。
        cells = []
        for key, _ in names:
            value = getter(runs[key])
            cells.append(_pct(value) if pct else ("—" if value is None else str(value)))
        rows.append([label, *cells])

    header = ["指标", *(display for _, display in names)]

    # 分母不写死在行名里：缺条件时实际分母会变小，行名还写「146 条」就是在骗人（M2 的 P2）
    row("四态一致率", lambda run: run["accuracy"]["accuracy"], pct=True)
    row("　dev", lambda run: run["accuracy_dev"]["accuracy"], pct=True)
    row("　holdout", lambda run: run["accuracy_holdout"]["accuracy"], pct=True)
    row("　正确条数", lambda run: f"{run['accuracy']['correct']}/{run['accuracy']['total']}")
    row("　分母（应判条数）", lambda run: run["labels"])
    row("　未返回条数", lambda run: len(run["accuracy"]["missing"]))
    row("硬门槛误判", lambda run: run["hard_gate"]["count"])
    row("　误判率", lambda run: run["hard_gate"]["rate"], pct=True)
    row("　分母（标注不满足）", lambda run: run["hard_gate"]["denominator"])
    row("　其中未返回", lambda run: len(run["hard_gate"]["unreturned"]))
    row("无依据断言率", lambda run: run["unsupported"]["rate"], pct=True)
    row("　分母", lambda run: run["unsupported"]["denominator"])
    row("引用可证实率", lambda run: run["quotes"]["rate"], pct=True)
    row("　分母", lambda run: run["quotes"]["denominator"])
    row("时效识别率", lambda run: run["timeliness"]["rate"], pct=True)
    row("　分母", lambda run: run["timeliness"]["denominator"])
    row("token 合计", lambda run: run["tokens"]["total"])
    row("　中位每 case", lambda run: run["tokens"]["median_per_case"])
    row("　用量未知条数", lambda run: run["tokens"]["usage_unknown_cases"])
    row("失败 case 数", lambda run: len(run["failures"]))

    lines = ["| " + " | ".join(header) + " |", "| --- |" + " --- |" * len(names)]
    lines.extend("| " + " | ".join(cells) + " |" for cells in rows)
    if not payload.get("same_input"):
        lines.append("")
        lines.append("> 输入不可比（清单缺失、两份不一致，或与当前数据不符）：指标不能并排比较。")
        for problem in payload.get("input_problems") or []:
            lines.append(f"> - {problem}")
    if any(not sub.get("complete", True) for sub in runs.values()):
        lines.append("")
        lines.append(
            "> 有条件没返回结论（见「未返回条数」）：指标只覆盖已返回的部分，"
            "不能当作完整对照——分母变小会抬高一致率。"
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None, *, dataset: Dataset | None = None,
         stream: Any | None = None) -> int:
    """``python -m app.ablation metrics --baseline a.jsonl --pipeline b.jsonl``。"""

    import argparse
    import sys

    from .dataset import load_dataset

    parser = argparse.ArgumentParser(
        prog="python -m app.ablation metrics", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--baseline", type=Path, required=True, help="单智能体基线的 JSONL")
    parser.add_argument("--pipeline", type=Path, required=True, help="现方案的 JSONL")
    parser.add_argument("--data", type=Path, help="data/ 目录，默认仓库根下的 data")
    parser.add_argument(
        "--as-of", dest="as_of", help="时效识别按哪一天算（YYYY-MM-DD），默认今天"
    )
    parser.add_argument("--json", type=Path, help="另存一份汇总 JSON，供复核与留证")
    args = parser.parse_args(argv)

    data = dataset if dataset is not None else load_dataset(args.data)
    on = date.fromisoformat(args.as_of) if args.as_of else date.today()
    runs = {
        "baseline": load_run(args.baseline),
        "pipeline": load_run(args.pipeline),
    }
    payload = compare(data, runs, on=on)

    out = stream or sys.stdout
    out.write(
        render(payload, names=(("baseline", runs["baseline"].label), ("pipeline", runs["pipeline"].label)))
    )
    out.write("\n")
    if args.json is not None:
        args.json.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    if not payload["same_input"]:
        print("两份跑数文件的输入不可比，先查输入再谈指标。", file=sys.stderr)
        return 3
    if any(sub["failures"] for sub in payload["runs"].values()):
        print("有 case 没跑成功，指标不完整，详见分段 JSON。", file=sys.stderr)
        return 2
    if any(not sub["complete"] for sub in payload["runs"].values()):
        print("有条件没返回结论，指标只覆盖已返回的部分，不能当作完整对照。", file=sys.stderr)
        return 4
    return 0
