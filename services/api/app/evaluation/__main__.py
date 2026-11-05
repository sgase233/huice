"""评测输入快照的命令行入口（``docs/product/EVALUATION_FREEZE.md`` §5.2）。

    python -m app.evaluation inputs --out snapshot.json \
        --dataset-dir data/evaluation \
        --dataset-file labels.csv --dataset-file criteria_review.csv \
        --dataset-file enterprises.csv
    python -m app.evaluation verify snapshot.json --dataset-dir data/evaluation

``inputs`` 从库里导出政策/条件与企业/事实的映射，写入一份 JSON；``verify`` 拿那份
JSON 对当前库重算一遍，逐项列出差异，非零退出即「与签认时不一致，停止评测」。

数据集只认 ``--dataset-file`` 显式列出的文件（可重复），不按目录扫全部 CSV：
材料复核表、标注表也在同一个目录里，但它们不是评测输入，扫进来会让 ``verify``
在复核改动后误报。``verify`` 核对的文件由签认清单本身决定；两侧 ``codeCommit``
都是 ``null`` 时它会提示「代码身份未证明」——没记不等于证明相同。

连接串只从环境变量 ``DATABASE_URL`` 读：命令行参数会留在 shell 历史与 ``ps`` 里，
错误信息也只说变量名，不打印值。部署机上用 ``systemd-run`` 带上服务同款环境变量：

    systemd-run --quiet --pipe --wait --collect \\
        -p EnvironmentFile=/etc/huice/api.env \\
        -p WorkingDirectory=/opt/huice/app/services/api \\
        /opt/huice/venv/bin/python -m app.evaluation inputs --out /tmp/eval-inputs.json

**第 4 项「结果导出」的输入侧在 ``freeze-report``**：它把下载到的 API-13 报告裁成
不含「截至下载时刻的有效性」的冻结产物，剔除按固定标记整行/整段删除、可重复得到同一
份字节；原始报告与下载时间、两侧哈希由调用方另存审计。条件级结果仍只在报告正文里
（库里只落政策级结果），这是本轮记录在案的缺口。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg

from .report_freeze import ReportError, freeze_report
from .snapshot import collect_input_snapshot, diff_snapshots

PROGRAM = "python -m app.evaluation"


def _connect() -> psycopg.Connection:
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        raise SystemExit(f"{PROGRAM}：缺少环境变量 DATABASE_URL（只列变量名，不输出值）")
    try:
        return psycopg.connect(dsn)
    except psycopg.Error:
        # psycopg 的报错里可能带 DSN；这里只说事实，不带原文
        raise SystemExit(f"{PROGRAM}：无法连接数据库（连接串不打印）") from None


def _dataset_hashes(
    directory: str | None, files: list[str] | None
) -> dict[str, str] | None:
    """按**显式列出的文件**算整文件 sha256。

    早先按目录扫 ``*.csv``，会把 ``material_review_sheet.csv``、``*_annotation_sheet.csv``
    这类非评测输入也算进签认清单——材料复核一改，``verify`` 就误报「数据集文件哈希不同」，
    而它们本来就不是要被冻结的输入（M3 复核提出）。现在只认 ``--dataset-file`` 列出的
    文件；``--dataset-dir`` 退化为基准目录。
    """

    base = Path(directory) if directory else Path.cwd()
    if directory is not None and not base.is_dir():
        raise SystemExit(f"{PROGRAM}：数据集目录不存在：{directory}")
    if not files:
        if directory is not None:
            raise SystemExit(
                f"{PROGRAM}：--dataset-dir 只作为 --dataset-file 的基准目录，"
                "请用 --dataset-file 显式列出签认的评测输入 CSV（目录扫描会把"
                " material_review_sheet.csv 这类非评测输入也算进去）"
            )
        return None

    hashes: dict[str, str] = {}
    for name in files:
        path = Path(name)
        if not path.is_absolute():
            path = base / path
        if not path.is_file():
            raise SystemExit(f"{PROGRAM}：数据集文件不存在：{name}")
        # 整文件哈希：与冻结声明 §3 的 sha256sum 口径一致
        hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return dict(sorted(hashes.items()))


def _config_from_env(use_config: bool) -> dict[str, str] | None:
    return dict(os.environ) if use_config else None


def _build_manifest(
    args: argparse.Namespace, *, files: list[str] | None = None
) -> dict[str, Any]:
    collected_at = None
    if args.collected_at:
        try:
            collected_at = datetime.fromisoformat(args.collected_at)
        except ValueError:
            raise SystemExit(f"{PROGRAM}：--collected-at 需要 ISO 8601 时间") from None

    # 先把不依赖数据库的参数校验完再连库：参数错了不该先去碰生产库
    dataset = _dataset_hashes(args.dataset_dir, args.dataset_file if files is None else files)
    with _connect() as conn:
        return collect_input_snapshot(
            conn,
            config=_config_from_env(not args.no_config),
            dataset=dataset,
            code_commit=args.commit,
            collected_at=collected_at,
        )


def _dump(manifest: dict[str, Any]) -> bytes:
    return (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _cmd_inputs(args: argparse.Namespace) -> int:
    payload = _dump(_build_manifest(args))
    if args.out == "-":
        sys.stdout.buffer.write(payload)
        return 0
    Path(args.out).write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    print(f"已写入 {args.out}（sha256 {digest}）", file=sys.stderr)
    return 0


def _load_manifest(path: str) -> dict[str, Any]:
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise SystemExit(f"{PROGRAM}：读不到清单 {path}：{exc.strerror}") from None
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise SystemExit(f"{PROGRAM}：{path} 不是 UTF-8 的 JSON") from None
    if not isinstance(manifest, dict) or "manifestVersion" not in manifest:
        raise SystemExit(f"{PROGRAM}：{path} 不是输入清单（缺少 manifestVersion）")
    return manifest


def _cmd_verify(args: argparse.Namespace) -> int:
    expected = _load_manifest(args.manifest)
    # 核对哪些文件由**签认清单**决定：签认时列了谁就查谁，不跟着目录里的新增文件走
    signed = list((expected.get("dataset") or {}).keys())
    actual = _build_manifest(args, files=signed or args.dataset_file)
    problems = diff_snapshots(expected, actual)
    if problems:
        print("与签认清单不一致：", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    # 两侧都 null 时 diff 无差异，但「没记」不等于「证明相同」，得说出来（M3 复核提出）
    if expected.get("codeCommit") is None and actual.get("codeCommit") is None:
        print(
            "提示：两侧 codeCommit 都是 null，代码身份未证明——"
            "要证明同一份代码，导出与核对时都要用 --commit 记录提交号",
            file=sys.stderr,
        )
    print(
        "一致：{} 个政策版本、{} 个企业".format(
            len(actual.get("policies", [])), len(actual.get("enterprises", []))
        )
    )
    return 0


def _cmd_freeze_report(args: argparse.Namespace) -> int:
    path = Path(args.report)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SystemExit(f"{PROGRAM}：读不到报告 {args.report}：{exc.strerror}") from None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise SystemExit(f"{PROGRAM}：{args.report} 不是 UTF-8 文本") from None
    try:
        frozen = freeze_report(text)
    except ReportError as exc:
        raise SystemExit(f"{PROGRAM}：{exc}") from None

    payload = frozen.text.encode("utf-8")
    if args.out == "-":
        sys.stdout.buffer.write(payload)
    else:
        Path(args.out).write_bytes(payload)
    # 审计信息走 stderr：--out - 时 stdout 必须是干净的冻结正文
    print(
        json.dumps(
            {
                "source": args.report,
                "sourceSha256": hashlib.sha256(raw).hexdigest(),
                "frozenSha256": hashlib.sha256(payload).hexdigest(),
                "downloadedAt": frozen.downloaded_at,
                "droppedLines": frozen.dropped_lines,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        file=sys.stderr,
    )
    return 0


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--dataset-file",
        action="append",
        metavar="NAME",
        help=(
            "签认的评测输入 CSV，可重复（如 --dataset-file labels.csv）；"
            "只哈希列出的文件，相对路径按 --dataset-dir 解析"
        ),
    )
    parser.add_argument(
        "--dataset-dir",
        help="数据集基准目录（如 data/evaluation）；不再按目录扫全部 CSV",
    )
    parser.add_argument(
        "--commit",
        help="代码提交号；云端没有 .git，需要显式给出，缺省记 null",
    )
    parser.add_argument(
        "--no-config",
        action="store_true",
        help="不把当前进程的非敏感配置计入哈希（默认计入）",
    )
    parser.add_argument(
        "--collected-at",
        help="覆盖清单的采集时间（ISO 8601），便于复现同一份清单",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.evaluation",
        description="评测输入快照的导出与核对（EVALUATION_FREEZE §5.2）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    inputs = sub.add_parser("inputs", help="从数据库导出输入清单")
    inputs.add_argument("--out", default="-", help="输出文件，默认写到标准输出")
    _common(inputs)
    inputs.set_defaults(handler=_cmd_inputs)

    verify = sub.add_parser("verify", help="拿已签认的清单核对当前库")
    verify.add_argument("manifest", help="已签认的清单 JSON")
    _common(verify)
    verify.set_defaults(handler=_cmd_verify)

    freeze = sub.add_parser(
        "freeze-report",
        help="把下载的 API-13 报告裁成不含有效性段的冻结产物（§5.2 第 4 项）",
    )
    freeze.add_argument("report", help="从 API-13 下载得到的 report.md")
    freeze.add_argument("--out", default="-", help="冻结产物的输出文件，默认写到标准输出")
    freeze.set_defaults(handler=_cmd_freeze_report)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
