"""现方案的冷启动那一半：把政策正文抽成条件，再数它抽得对不对。

单智能体基线的输入里**已经给了**条件清单，所以两边看起来是同一批条件；但现方案
的条件是**自己抽出来的**，抽错、抽漏、编造引文的成本要单独算，否则「同口径比较」
就把抽取阶段的错误藏起来了。这个模块只跑 ``extract_criteria``，不碰判断阶段。

三个数：

* **幻觉条款率** = 引文在正文里找不到的条款数 ÷ 模型实际给出的条款数。抽取器已经把
  这类条款丢弃并记 ``EXTRACTION_QUOTE_NOT_IN_BODY``，这里只是把丢弃数还原成分母。
  只有现方案有这个数——基线没有抽取阶段。
* **条件覆盖** = 已仲裁条件里，抽取结果能对上原文片段的比例。对上按原文片段逐字比对，
  不比表达式：表达式写法可以不同，引文对不上就是没读到那一句。
* **冷启动用量** = 每个政策一次调用的 token 与耗时；与基线的「每 case 一次调用」
  分开报，不要混成一个总数。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO
from uuid import uuid4

from ..agents.extraction import ExtractionSource, extract_criteria
from ..integrations.llm import LlmClient, build_llm_client
from ..config import Settings
from .dataset import Dataset, Policy, normalize_for_match

#: 计入「模型给出的条款数」的校验码：每个码对应一个被丢弃的条款。
ITEM_CODES = (
    "EXTRACTION_QUOTE_NOT_IN_BODY",
    "EXTRACTION_ITEM_INCOMPLETE",
    "EXTRACTION_EXPRESSION_INVALID",
    "EXTRACTION_MANUAL_REVIEW_NOT_ALLOWED",
)


@dataclass(frozen=True, slots=True)
class PolicyExtraction:
    policy_code: str
    document: str
    status: str
    accepted: int
    produced: int
    hallucinated: int
    expected: int
    matched: int
    codes: dict[str, int]
    model: str | None
    usage_prompt: int
    usage_completion: int
    usage_known: bool
    elapsed_ms: int
    error: str | None = None

    @property
    def hallucination_rate(self) -> float | None:
        return self.hallucinated / self.produced if self.produced else None

    @property
    def coverage(self) -> float | None:
        return self.matched / self.expected if self.expected else None

    def as_json(self) -> dict[str, Any]:
        payload = {
            "policy_code": self.policy_code,
            "document": self.document,
            "status": self.status,
            "accepted": self.accepted,
            "produced": self.produced,
            "hallucinated": self.hallucinated,
            "hallucination_rate": self.hallucination_rate,
            "expected": self.expected,
            "matched": self.matched,
            "coverage": self.coverage,
            "codes": self.codes,
            "model": self.model,
            "usage_prompt": self.usage_prompt,
            "usage_completion": self.usage_completion,
            "usage_known": self.usage_known,
            "elapsed_ms": self.elapsed_ms,
            "error": self.error,
        }
        return payload


def _matched(dataset: Dataset, policy: Policy, quotes: Iterable[str]) -> tuple[int, int]:
    """已仲裁条件里，有多少条能在抽取结果的引文里找到自己的原文片段。"""

    haystack = [normalize_for_match(quote) for quote in quotes if quote]
    # 与 ``pipeline.seed`` 同一套归属规则：本版本的条件，加上出处是**另一份文件**
    # 而非同一政策其它版本的条件（研发加计、专精特新引自上位文件或申报通知）。
    other_versions = {doc for (code, doc) in dataset.policies if code == policy.code}
    expected = 0
    matched = 0
    for (code, _), item in dataset.criteria.items():
        if code != policy.code:
            continue
        if item.document != policy.document and item.document in other_versions:
            continue  # 属于本政策的另一个版本，不是这一版该抽的
        expected += 1
        if any(normalize_for_match(span) in text for span in item.spans for text in haystack):
            matched += 1
    return matched, expected


def run_policy(
    dataset: Dataset, policy: Policy, *, llm: LlmClient, model_version: str | None = None
) -> PolicyExtraction:
    body = dataset.source_text(policy)
    source = ExtractionSource(
        id=uuid4(), source_url=policy.source_url, text=body, kind="body"
    )
    codes: dict[str, int] = {}
    try:
        outcome = extract_criteria(
            version=policy.document, sources=[source], llm=llm, model_version=model_version
        )
    except Exception as exc:  # noqa: BLE001 - 单个政策失败不该毁掉整轮
        return PolicyExtraction(
            policy_code=policy.code,
            document=policy.document,
            status="error",
            accepted=0,
            produced=0,
            hallucinated=0,
            expected=0,
            matched=0,
            codes=codes,
            model=getattr(llm, "model_name", None),
            usage_prompt=0,
            usage_completion=0,
            usage_known=False,
            elapsed_ms=0,
            error=f"{type(exc).__name__}: {exc}",
        )

    for finding in outcome.findings:
        codes[finding.code] = codes.get(finding.code, 0) + 1
    hallucinated = codes.get("EXTRACTION_QUOTE_NOT_IN_BODY", 0)
    produced = len(outcome.criteria) + sum(codes.get(code, 0) for code in ITEM_CODES)
    quotes = [span.quote for criterion in outcome.criteria for span in criterion.spans]
    matched, expected = _matched(dataset, policy, quotes)
    usage = outcome.usage
    return PolicyExtraction(
        policy_code=policy.code,
        document=policy.document,
        status=outcome.status,
        accepted=len(outcome.criteria),
        produced=produced,
        hallucinated=hallucinated,
        expected=expected,
        matched=matched,
        codes=codes,
        model=getattr(usage, "model_version", None) if usage else None,
        usage_prompt=usage.input_tokens if usage else 0,
        usage_completion=usage.output_tokens if usage else 0,
        usage_known=outcome.usage_known,
        elapsed_ms=usage.elapsed_ms if usage else 0,
    )


def run(dataset: Dataset, llm: LlmClient) -> list[PolicyExtraction]:
    """对进指标的每个政策版本各抽一次。"""

    policies: dict[tuple[str, str], Policy] = {}
    for case in dataset.cases:
        policies[(case.policy_code, case.document)] = dataset.policies[
            (case.policy_code, case.document)
        ]
    return [
        run_policy(dataset, policy, llm=llm) for _, policy in sorted(policies.items())
    ]


def summarise(rows: Sequence[PolicyExtraction]) -> dict[str, Any]:
    produced = sum(row.produced for row in rows)
    hallucinated = sum(row.hallucinated for row in rows)
    expected = sum(row.expected for row in rows)
    matched = sum(row.matched for row in rows)
    known = [row for row in rows if row.usage_known]
    return {
        "policies": len(rows),
        "errors": [row.policy_code for row in rows if row.error],
        "accepted": sum(row.accepted for row in rows),
        "produced": produced,
        "hallucinated": hallucinated,
        "hallucination_rate": hallucinated / produced if produced else None,
        "expected": expected,
        "matched": matched,
        "coverage": matched / expected if expected else None,
        "usage_unknown_policies": len(rows) - len(known),
        "total_prompt": sum(row.usage_prompt for row in known),
        "total_completion": sum(row.usage_completion for row in known),
        "elapsed_ms": sum(row.elapsed_ms for row in rows),
    }


def write_jsonl(stream: TextIO, dataset: Dataset, rows: Sequence[PolicyExtraction], *, llm: LlmClient) -> int:
    stream.write(
        json.dumps(
            {
                "record": "header",
                "scheme": "extraction",
                "model": llm.model_name,
                "mode": llm.mode,
                "manifest": dataset.manifest(),
                "summary": summarise(rows),
            },
            ensure_ascii=False,
        )
        + "\n"
    )
    for row in rows:
        stream.write(json.dumps(row.as_json(), ensure_ascii=False) + "\n")
    return len(rows)


def _client_from_env() -> LlmClient:
    """读服务端配置；读不到（没配库）就退回规则抽取，而不是直接崩在参数校验上。

    这个模块不连库，``DATABASE_URL`` 与它无关；为了一个无关变量拒绝跑抽取没有道理。
    """

    import sys

    from ..config import ConfigError
    from ..integrations.llm import FixtureLlmClient

    try:
        return build_llm_client(Settings.from_env())
    except ConfigError as exc:
        print(f"读不到服务端配置（{type(exc).__name__}），按 fixture 规则抽取运行。", file=sys.stderr)
        return FixtureLlmClient()


def main(
    argv: Sequence[str] | None = None,
    *,
    dataset: Dataset | None = None,
    llm: LlmClient | None = None,
    stream: TextIO | None = None,
) -> int:
    """``python -m app.ablation extraction --out FILE``。"""

    import argparse
    import sys

    from .dataset import load_dataset

    parser = argparse.ArgumentParser(
        prog="python -m app.ablation extraction", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--out", type=Path, help="JSONL 输出路径；不给就打到 stdout")
    parser.add_argument("--data", type=Path, help="data/ 目录，默认仓库根下的 data")
    args = parser.parse_args(argv)

    data = dataset if dataset is not None else load_dataset(args.data)
    client = llm if llm is not None else _client_from_env()
    if client.mode != "deepseek":
        print(
            f"MODEL_MODE={client.mode}：这不是模型抽取，结果只能证明脚本能跑通，不能进报告。",
            file=sys.stderr,
        )

    rows = run(data, client)
    if stream is not None or args.out is None:
        write_jsonl(stream or sys.stdout, data, rows, llm=client)
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", encoding="utf-8", newline="\n") as handle:
            write_jsonl(handle, data, rows, llm=client)

    summary = summarise(rows)
    print(
        f"抽了 {summary['policies']} 项政策：给出 {summary['produced']} 条、"
        f"引文不实 {summary['hallucinated']} 条、覆盖 {summary['matched']}/{summary['expected']}",
        file=sys.stderr,
    )
    for row in rows:
        if row.error:
            print(f"  {row.policy_code} 失败：{row.error}", file=sys.stderr)
    return 0 if not summary["errors"] else 2
