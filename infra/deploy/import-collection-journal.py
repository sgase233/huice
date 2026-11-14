#!/usr/bin/env python3
"""把主机侧采集事件日志导入 ``collection_gap``（E3 / B3）。

`record-collection-event.py` 写日志；本脚本按主机逐台读取，推出**中断区间**再幂等写入
`collection_gap`。两类缺口：

1. **配对缺口**：同一主机上一次 `stop`（或 `migrate`/`rollback`/`maintenance`）到下一次
   `start`；没有后续 `start` 的，`gap_to` 留空表示「未闭合，持续到当前」；
2. **心跳缺口**：相邻心跳间隔超过 `3 × 心跳间隔`，说明主机在没写 `stop` 的情况下消失
   （断电、被杀、实例重建），按 `kind='stop'`、`reason='HOST_SILENT'` 记一条。

链不完整（`seq`/`prev` 对不上）或日志不可读时**不导入**、非零退出：读侧看到证据缺失会
按 `partial` 处理，这里不能把不完整的日志当成完整证据。

用法（ECS，venv 解释器）::

    /opt/huice/venv/bin/python import-collection-journal.py            # 干跑，只打印
    /opt/huice/venv/bin/python import-collection-journal.py --apply    # 写库

退出码：0 成功（含「没有新事件」）；2 环境或日志不合法；3 写库失败；4 日志链不完整。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_LOG_DIR = Path("/var/lib/huice")

#: 与记录脚本共用同一份「链校验 + 心跳间隔」实现，避免两边各写一套。
_recorder_spec = importlib.util.spec_from_file_location(
    "record_collection_event", SCRIPT_DIR / "record-collection-event.py"
)
assert _recorder_spec and _recorder_spec.loader
recorder = importlib.util.module_from_spec(_recorder_spec)
sys.modules.setdefault("record_collection_event", recorder)
_recorder_spec.loader.exec_module(recorder)

OPENING_KINDS = ("stop", "migrate", "rollback", "maintenance")


def journal_files(log_dir: Path) -> list[Path]:
    return sorted(log_dir.glob("collection-lifecycle-*.jsonl"))


def build_gaps(path: Path) -> tuple[list[dict[str, Any]], str | None]:
    """从一台主机的日志推出缺口；返回 ``(缺口列表, 链错误)``。"""

    entries = recorder.read_entries(path)
    broken = recorder.verify_chain(entries)
    if broken is not None:
        return [], broken

    gaps: list[dict[str, Any]] = []
    pending: tuple[str, dict] | None = None
    for raw, item in entries:
        if item.get("kind") in OPENING_KINDS:
            if pending is not None:  # 又一次中断而没有恢复：保持未闭合，不覆盖
                continue
            pending = (raw, item)
        elif item.get("kind") == "start":
            if pending is None:
                continue
            raw_open, opened = pending
            gaps.append(
                {
                    "source": "host-journal",
                    "event_id": f"{opened['event_id']}:pair",
                    "host": opened["host"],
                    "kind": opened["kind"],
                    "scope": opened.get("scope", "all"),
                    "gap_from": opened["at"],
                    "gap_to": item["at"],
                    "reason": opened.get("note"),
                    "recorded_by": "import-collection-journal.py",
                }
            )
            pending = None

    if pending is not None:
        raw_open, opened = pending
        last = entries[-1][1]
        gaps.append(
            {
                "source": "host-journal",
                "event_id": f"{opened['event_id']}:open",
                "host": opened["host"],
                "kind": opened["kind"],
                "scope": opened.get("scope", "all"),
                "gap_from": opened["at"],
                "gap_to": None,  # 未闭合
                "reason": (opened.get("note") or "") + "（未见恢复事件）",
                "recorded_by": "import-collection-journal.py",
            }
        )
        del last

    # 心跳空档：没有 stop 行也能发现主机消失
    threshold = timedelta(seconds=recorder.HEARTBEAT_SECONDS * recorder.GAP_FACTOR)
    beats = [item for _raw, item in entries if item.get("kind") in ("beat", "start", "stop")]
    for earlier, later in zip(beats, beats[1:]):
        start = datetime.fromisoformat(earlier["at"])
        end = datetime.fromisoformat(later["at"])
        if end - start > threshold:
            gaps.append(
                {
                    "source": "host-journal",
                    "event_id": f"{earlier['event_id']}:silent",
                    "host": earlier["host"],
                    "kind": "stop",
                    "scope": "all",
                    "gap_from": earlier["at"],
                    "gap_to": later["at"],
                    "reason": "HOST_SILENT",
                    "recorded_by": "import-collection-journal.py",
                }
            )
    return gaps, None


def collect(log_dir: Path) -> tuple[list[dict[str, Any]], list[str]]:
    gaps: list[dict[str, Any]] = []
    errors: list[str] = []
    for path in journal_files(log_dir):
        found, broken = build_gaps(path)
        if broken is not None:
            errors.append(f"{path.name}：链不完整（{broken}）")
            continue
        gaps.extend(found)
    return gaps, errors


def run(log_dir: Path, apply: bool) -> int:
    gaps, errors = collect(log_dir)
    if errors:
        for message in errors:
            print(f"import-collection-journal: {message}，拒绝导入。", file=sys.stderr)
        return 4
    if not gaps:
        print("没有新的缺口事件。")
        return 0

    changed = 0
    if not apply:
        print(f"干跑：将导入 {len(gaps)} 条缺口（未闭合 "
              f"{sum(1 for gap in gaps if gap['gap_to'] is None)} 条）。")
        for gap in gaps:
            print(f"  {gap['host']} {gap['gap_from']} → {gap['gap_to'] or '未闭合'}"
                  f"（{gap['kind']}/{gap['scope']}，{gap['reason'] or '—'}）")
        return 0

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("缺少环境变量 DATABASE_URL（不打印取值）。", file=sys.stderr)
        return 2
    try:
        import psycopg
    except ModuleNotFoundError as exc:
        print(f"缺少依赖 {exc.name}：请用项目虚拟环境的解释器运行。", file=sys.stderr)
        return 2

    logging.getLogger("psycopg.pool").setLevel(logging.CRITICAL)
    try:
        with psycopg.connect(dsn, autocommit=False) as conn:
            for gap in gaps:
                cursor = conn.execute(
                    "INSERT INTO collection_gap (source, event_id, host, kind, scope, "
                    "gap_from, gap_to, reason, recorded_by) VALUES (%(source)s, %(event_id)s, "
                    "%(host)s, %(kind)s, %(scope)s, %(gap_from)s, %(gap_to)s, %(reason)s, "
                    "%(recorded_by)s) ON CONFLICT (event_id) DO NOTHING",
                    gap,
                )
                changed += cursor.rowcount
            conn.commit()
    except Exception as exc:  # 不回显连接串
        print(f"写库失败（{type(exc).__name__}）。", file=sys.stderr)
        return 3

    print(f"已导入 {changed} 条（其余 {len(gaps) - changed} 条此前已存在，跳过）。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把主机事件日志导入 collection_gap")
    parser.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    parser.add_argument("--apply", action="store_true", help="真正写库；不加只打印")
    args = parser.parse_args(argv)
    return run(args.log_dir, args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
