"""发现运行的 CLI 入口：``python -m app.discovery``。

systemd timer 每 15 分钟唤醒一次，命令行只负责「看一眼是否到点」；真正跑不跑由
``discovery_settings.next_run_at`` 决定（E0 D3.1）。运维也可以手工执行：

    python -m app.discovery                 # 到点才跑
    python -m app.discovery --force         # 立即跑一轮（不改配置、不改周期）
    python -m app.discovery --json          # 输出一行 JSON，供脚本读取

退出码：``0`` 成功或无需运行（未启用、未到点、已有运行在跑）；``1`` 本轮失败或部分
失败；``2`` 配置不合法（只报变量名，不打印取值）。输出里不含正文与连接串。
"""

from __future__ import annotations

import argparse
import json
import sys

from ..config import ConfigError, Settings, cursor_secret
from ..db import Database
from ..integrations.discovery import DiscoveryRepository
from ..integrations.llm import build_llm_client
from ..logging_setup import configure_logging
from .runner import DiscoveryOutcome, DiscoveryRunner


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.discovery", description="政策发现 CLI")
    parser.add_argument(
        "--trigger",
        choices=("timer", "cli"),
        default="cli",
        help="记进运行记录的触发方式（timer = systemd 定时；cli = 人工执行）",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="忽略 nextRunAt 立即运行一轮；不会改变已排定的周期",
    )
    parser.add_argument("--json", action="store_true", help="以一行 JSON 输出结果")
    args = parser.parse_args(argv)

    configure_logging()
    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        # ConfigError 只含变量名（config.py 的约定），可以原样给出。
        print(f"发现运行未启动：{exc}", file=sys.stderr)
        return 2

    database = Database(settings.database_url, max_size=1)
    try:
        repository = DiscoveryRepository(database, cursor_secret=cursor_secret(settings))
        runner = DiscoveryRunner(
            database=database,
            repository=repository,
            llm=build_llm_client(settings),
            model_version=settings.deepseek_model or None,
        )
        outcome = runner.run(args.trigger, force=args.force)
    finally:
        database.close()

    _report(outcome, as_json=args.json)
    return 0 if outcome.succeeded else 1


def _report(outcome: DiscoveryOutcome, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(outcome.as_dict(), ensure_ascii=False))
        return
    if not outcome.ran:
        print(f"本轮未运行：{outcome.reason}（{outcome.detail or '无补充说明'}）")
        return
    print(
        f"本轮结束：{outcome.reason}，新候选 {outcome.new_candidates} 条，"
        f"重复 {outcome.duplicates} 条，运行 {outcome.run_id}。"
    )
    if outcome.detail:
        print(f"说明：{outcome.detail}")


if __name__ == "__main__":  # pragma: no cover - 由 systemd/人工执行
    sys.exit(main())
