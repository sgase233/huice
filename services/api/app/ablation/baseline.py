"""单智能体基线：一个 case 一次调用，把回答落成 JSONL。

两个硬约束：

* **每个 case 最多一次重试**，用的还是同一个提示词、同一个 ``max_tokens``；
  重试次数与预算一并写进 JSONL 表头，报告里照抄。
* **模型名与提示词版本写在每一行**，不靠「跑的时候记得改成什么」。

日志复用服务端的 ``model_call`` 行（``purpose=baseline``），格式与抽取、解释两条
路径一致，云端 ``journalctl -u huice-api | grep model_call`` 能一起捞出来。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, TextIO

from ..agents.orchestrator import _log_model_call  # 同一格式，见模块文档
from ..errors import ApiProblem
from ..integrations.llm import LlmClient, LlmResult, build_llm_client
from ..config import Settings
from .dataset import Case, Dataset, load_dataset
from .prompt import MAX_TOKENS, PROMPT_VERSION, SYSTEM_PROMPT, build_user_message, parse_response

#: 每个 case 允许的重试次数。与提示词一起冻结：改这个数就不是同一轮实验了。
RETRIES = 1

PURPOSE = "baseline"


@dataclass(frozen=True, slots=True)
class CaseOutcome:
    case_id: str
    enterprise: str
    policy_code: str
    document: str
    outcome: str  # ok / format_error / api_error
    attempts: int
    model: str
    prompt_version: str
    elapsed_ms: int
    usage_prompt: int
    usage_completion: int
    usage_known: bool
    payload: dict[str, Any] | None
    error: str | None


def run_case(llm: LlmClient, dataset: Dataset, case: Case) -> CaseOutcome:
    enterprise = dataset.enterprises[case.enterprise]
    user = build_user_message(dataset, case)
    expected = list(case.criterion_codes)
    attempts = 0
    error: str | None = None
    elapsed_ms = 0
    usage_prompt = 0
    usage_completion = 0
    usage_known = False

    while attempts <= RETRIES:
        attempts += 1
        try:
            result: LlmResult = llm.complete(
                system=SYSTEM_PROMPT, user=user, max_tokens=MAX_TOKENS
            )
        except ApiProblem as exc:
            _log_model_call(
                purpose=PURPOSE,
                usage=None,
                usage_unknown=exc.usage_unknown,
                version=f"{case.case_id}@{case.policy_code}",
            )
            error = f"{exc.code}: {exc.message}"
            if not exc.retryable:
                break
            continue

        _log_model_call(
            purpose=PURPOSE,
            usage=result,
            version=f"{case.case_id}@{case.policy_code}",
        )
        elapsed_ms += result.elapsed_ms
        usage_known = result.usage_known
        usage_prompt = result.prompt_tokens if usage_known else 0
        usage_completion = result.completion_tokens if usage_known else 0
        try:
            payload = parse_response(result.text, expected=expected)
        except Exception as exc:  # BaselineFormatError 或解析里没料到的形状
            error = f"{type(exc).__name__}: {exc}"
            continue
        return CaseOutcome(
            case_id=case.case_id,
            enterprise=case.enterprise,
            policy_code=case.policy_code,
            document=case.document,
            outcome="ok",
            attempts=attempts,
            model=result.model,
            prompt_version=PROMPT_VERSION,
            elapsed_ms=elapsed_ms,
            usage_prompt=usage_prompt,
            usage_completion=usage_completion,
            usage_known=usage_known,
            payload=payload,
            error=None,
        )

    return CaseOutcome(
        case_id=case.case_id,
        enterprise=case.enterprise,
        policy_code=case.policy_code,
        document=case.document,
        outcome="api_error" if error and error[:3] in {"503", "429"} else "format_error",
        attempts=attempts,
        model=llm.model_name,
        prompt_version=PROMPT_VERSION,
        elapsed_ms=elapsed_ms,
        usage_prompt=usage_prompt,
        usage_completion=usage_completion,
        usage_known=usage_known,
        payload=None,
        error=error,
    )


def run(
    dataset: Dataset,
    llm: LlmClient,
    *,
    only: Sequence[str] | None = None,
    limit: int | None = None,
) -> Iterator[CaseOutcome]:
    cases: Iterable[Case] = dataset.cases
    if only:
        wanted = {case_id.strip().upper() for case_id in only}
        cases = [case for case in cases if case.case_id in wanted]
        missing = wanted - {case.case_id for case in cases}
        if missing:
            raise ValueError(f"没有这些 case：{'、'.join(sorted(missing))}")
    for index, case in enumerate(cases, start=1):
        if limit is not None and index > limit:
            break
        yield run_case(llm, dataset, case)


def write_jsonl(
    stream: TextIO,
    dataset: Dataset,
    outcomes: Iterable[CaseOutcome],
    *,
    scheme: str,
    model: str,
    prompt_version: str = PROMPT_VERSION,
    extra: dict[str, Any] | None = None,
) -> int:
    """表头一行 + 每个 case 一行。表头带输入文件的 sha256，报表里照抄就能复现。

    两个方案共用这一个写出函数：形状一致，``metrics.load_run`` 才不用版本分支。
    ``prompt_version`` 对基线是提示词版本，对流水线是规则版本；``extra`` 用来记
    该方案特有的口径（如判断时点 ``asOf``）。
    """

    header: dict[str, Any] = {
        "record": "header",
        "scheme": scheme,
        "prompt_version": prompt_version,
        "model": model,
        "max_tokens": MAX_TOKENS,
        "retries": RETRIES,
        "manifest": dataset.manifest(),
        "cases": len(dataset.cases),
        "labels": len(dataset.scored_labels()),
    }
    if extra:
        header.update(extra)
    stream.write(json.dumps(header, ensure_ascii=False) + "\n")
    count = 0
    for outcome in outcomes:
        stream.write(json.dumps(asdict(outcome), ensure_ascii=False) + "\n")
        count += 1
    return count


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    llm: LlmClient | None = None,
    dataset: Dataset | None = None,
    stream: TextIO | None = None,
) -> int:
    """``python -m app.ablation baseline --out FILE``。"""

    import argparse
    import sys

    parser = argparse.ArgumentParser(prog="python -m app.ablation baseline", description=__doc__)
    parser.add_argument("--out", type=Path, help="JSONL 输出路径；不给就打到 stdout")
    parser.add_argument("--case", action="append", help="只跑指定 case，可重复")
    parser.add_argument("--limit", type=int, help="只跑前 N 个 case（冒烟用）")
    parser.add_argument("--data", type=Path, help="data/ 目录，默认仓库根下的 data")
    args = parser.parse_args(argv)

    data = dataset if dataset is not None else load_dataset(args.data)
    resolved = settings if settings is not None else Settings.from_env()
    client = llm if llm is not None else build_llm_client(resolved)

    if client.mode == "fixture":
        print(
            "MODEL_MODE=fixture：没有真实模型调用，结果只能证明脚本能跑通，不能进报告。",
            file=sys.stderr,
        )

    outcomes = list(run(data, client, only=args.case, limit=args.limit))

    if stream is not None or args.out is None:
        count = write_jsonl(
            stream or sys.stdout, data, outcomes, scheme="single_agent", model=client.model_name
        )
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", encoding="utf-8", newline="\n") as handle:
            count = write_jsonl(
                handle, data, outcomes, scheme="single_agent", model=client.model_name
            )

    failures = [item for item in outcomes if item.outcome != "ok"]
    print(f"完成 {count} 个 case，其中失败 {len(failures)} 个", file=sys.stderr)
    for item in failures:
        print(f"  {item.case_id} {item.outcome}（{item.attempts} 次）：{item.error}", file=sys.stderr)
    return 0 if not failures else 2
