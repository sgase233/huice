"""评测输入快照的命令行入口（``docs/product/EVALUATION_FREEZE.md`` §5.2）。

    python -m app.evaluation inputs --out snapshot.json
    python -m app.evaluation verify snapshot.json

``inputs`` 从库里导出政策/条件与企业/事实的映射，写入一份 JSON；``verify`` 拿那份
JSON 对当前库重算一遍，逐项列出差异，非零退出即「与签认时不一致，停止评测」。

连接串只从环境变量 ``DATABASE_URL`` 读：命令行参数会留在 shell 历史与 ``ps`` 里，
错误信息也只说变量名，不打印值。部署机上用 ``systemd-run`` 带上服务同款环境变量：

    systemd-run --quiet --pipe --wait --collect \\
        -p EnvironmentFile=/etc/huice/api.env \\
        -p WorkingDirectory=/opt/huice/app/services/api \\
        /opt/huice/venv/bin/python -m app.evaluation inputs --out /tmp/eval-inputs.json

**本工具只覆盖 §5.2 的第 1、2、3（部分）、5 项。** 第 4 项「结果导出」不在这里：
条件级结果只存在于生成时的报告与轨迹摘要里，库里只落政策级结果，怎么算「结果导出」
要先和 M3 定口径（见 PR 说明）。
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


def _dataset_hashes(directory: str | None) -> dict[str, str] | None:
    if directory is None:
        return None
    path = Path(directory)
    if not path.is_dir():
        raise SystemExit(f"{PROGRAM}：数据集目录不存在：{directory}")
    files = sorted(item for item in path.glob("*.csv") if item.is_file())
    if not files:
        raise SystemExit(f"{PROGRAM}：数据集目录里没有 CSV：{directory}")
    # 整文件哈希：与冻结声明 §3 的 sha256sum 口径一致
    return {item.name: hashlib.sha256(item.read_bytes()).hexdigest() for item in files}


def _config_from_env(use_config: bool) -> dict[str, str] | None:
    return dict(os.environ) if use_config else None


def _build_manifest(args: argparse.Namespace) -> dict[str, Any]:
    collected_at = None
    if args.collected_at:
        try:
            collected_at = datetime.fromisoformat(args.collected_at)
        except ValueError:
            raise SystemExit(f"{PROGRAM}：--collected-at 需要 ISO 8601 时间") from None

    # 先把不依赖数据库的参数校验完再连库：参数错了不该先去碰生产库
    dataset = _dataset_hashes(args.dataset_dir)
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
    actual = _build_manifest(args)
    problems = diff_snapshots(expected, actual)
    if problems:
        print("与签认清单不一致：", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(
        "一致：{} 个政策版本、{} 个企业".format(
            len(actual.get("policies", [])), len(actual.get("enterprises", []))
        )
    )
    return 0


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--dataset-dir",
        help="包含评测 CSV 的目录（如 data/evaluation）；给了就记整文件 sha256",
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
