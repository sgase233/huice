"""材料要求导入的命令行入口（在 ``services/api`` 下用服务的虚拟环境解释器执行）。

    python -m app.materials check     # 只校验 CSV，不连库
    python -m app.materials import    # 校验并通过后写入数据库

CSV 与政策登记表默认取仓库里的 ``data/policies/``，可用 ``--csv`` / ``--source-register``
指定。``DATABASE_URL`` 从环境变量读（也可用 ``--database-url``），**不打印**——连接串里
有口令，输出只报数量、材料代号与政策代号。

两条命令都不改 CSV：它是 M3 的产物，导入只读。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from ..config import REPO_ROOT
from ..db import Database
from ..domain.rules import FACT_UNITS
from ..integrations.storage import PolicyRepository
from .catalog import MaterialError, parse, read_policy_codes, read_rows

DEFAULT_DIR = REPO_ROOT / "data" / "policies"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.materials",
        description="校验并导入已仲裁的材料要求（只读 CSV，不改 M3 的数据）",
    )
    parser.add_argument("command", choices=("check", "import"), help="check 只校验，import 写入数据库")
    parser.add_argument("--csv", type=Path, default=DEFAULT_DIR / "material_requirements.csv")
    parser.add_argument(
        "--source-register", type=Path, default=DEFAULT_DIR / "source_register.csv"
    )
    parser.add_argument("--database-url", default=None, help="缺省读环境变量 DATABASE_URL")
    return parser


def _describe(catalog) -> list[str]:
    lines = [f"可导入 {len(catalog.importable)} 行：" + "、".join(row.material_id for row in catalog.importable)]
    if catalog.skipped:
        lines.append(
            "跳过 {} 行（未到「已仲裁」）：{}".format(
                len(catalog.skipped),
                "、".join(f"{material_id}（{status}）" for material_id, status in catalog.skipped),
            )
        )
    return lines


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        catalog = parse(read_rows(args.csv), fact_keys=tuple(FACT_UNITS))
    except MaterialError as exc:
        print(f"材料要求不合口径：{exc}", file=sys.stderr)
        return 2

    for line in _describe(catalog):
        print(line)
    if not catalog.importable:
        print("没有已仲裁的行，未做任何写入。", file=sys.stderr)
        return 2
    if args.command == "check":
        return 0

    database_url = args.database_url or os.environ.get("DATABASE_URL", "")
    if not database_url:
        print("缺少 DATABASE_URL，未做任何写入（只列出变量名）。", file=sys.stderr)
        return 2
    try:
        document_numbers = read_policy_codes(args.source_register)
    except MaterialError as exc:
        print(f"政策登记表不合口径：{exc}", file=sys.stderr)
        return 2

    database = Database(database_url)
    try:
        repository = PolicyRepository(database, cursor_secret="")
        summary = repository.import_material_requirements(
            catalog.importable, document_numbers=document_numbers
        )
    except MaterialError as exc:
        print(f"导入失败，库未改动：{exc}", file=sys.stderr)
        return 2
    finally:
        database.close()

    print(f"已导入 {len(summary['imported'])} 行：" + "、".join(summary["imported"]))
    if summary["removed"]:
        print("已移除库里不再属于本份清单的行：" + "、".join(summary["removed"]))
    if summary["policies"]:
        print("已解析的政策代号：" + "、".join(sorted(summary["policies"])))
    return 0


if __name__ == "__main__":  # pragma: no cover - 命令行入口
    raise SystemExit(main())
