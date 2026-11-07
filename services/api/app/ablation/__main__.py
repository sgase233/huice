"""``python -m app.ablation <子命令>``。

五个子命令对应消融对照的各步，任何一步失败都不影响前一步的产物：

    extraction    现方案的冷启动：把正文抽成条件，数幻觉条款与覆盖
    baseline      单智能体基线，一次调用判完一个 case
    pipeline      现方案，灌临时库后走真实 ``assess()``
    metrics       两个 JSONL 比指标，表格直接贴进评测报告
    review-sheet  分层抽 30 条生成人工复核单

用 ``-h`` 看各步参数。
"""

from __future__ import annotations

import sys

from . import baseline, extraction, metrics, pipeline, review

COMMANDS = {
    "extraction": extraction.main,
    "baseline": baseline.main,
    "pipeline": pipeline.main,
    "metrics": metrics.main,
    "review-sheet": review.main,
}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print(__doc__)
        return 0 if args else 2
    name = args.pop(0)
    command = COMMANDS.get(name)
    if command is None:
        print(f"没有 {name} 这个子命令；可选：{'、'.join(COMMANDS)}", file=sys.stderr)
        return 2
    return command(args)


if __name__ == "__main__":
    raise SystemExit(main())
