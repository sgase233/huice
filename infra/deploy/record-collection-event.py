#!/usr/bin/env python3
"""把采集生命周期事件写进主机侧日志（E3 / B3，指标覆盖证据）。

**为什么不用数据库**：库不可用时正好是最需要记录中断的时候；首次迁移前表也不存在。
所以事件先写主机文件，恢复后再由 `import-collection-journal.py` 幂等导入
`collection_gap`。

日志格式：一行一个 JSON，追加写并 `fsync`：

```json
{"seq":128,"prev":"9f3c…","host":"i-0a1b…","kind":"beat",
 "at":"2026-11-20T10:05:00+08:00","event_id":"…","scope":"all","note":null,"prev":null}
```

- `seq` / `prev`：哈希链。`prev` 是**上一行原始文本**的 sha256；读侧发现链断即视为
  日志被截断或篡改，覆盖判为 `partial` + `LOG_CHAIN_BROKEN`；
- `host`：`/etc/machine-id`（读不到时退回主机名），一台主机一个文件；
- `kind=beat`：心跳，用于在没有 `stop` 的情况下发现断电——两次心跳之间的空档即缺口。

用法（ECS，root；也可被 unit 钩子调用）::

    /opt/huice/venv/bin/python record-collection-event.py --kind start --scope all
    /opt/huice/venv/bin/python record-collection-event.py --kind beat
    /opt/huice/venv/bin/python record-collection-event.py --verify

`--verify` 只校验链与心跳空档，不写日志，退出码：0 完整；4 链断；5 心跳空档超阈。

退出码：0 成功；2 参数或环境不合法；3 写盘失败。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

SHANGHAI = timezone(timedelta(hours=8))
DEFAULT_LOG_DIR = Path("/var/lib/huice")
#: 心跳间隔（秒）。读侧判断「空档」时按 3 倍计。
HEARTBEAT_SECONDS = 300
GAP_FACTOR = 3
KINDS = ("stop", "start", "migrate", "rollback", "maintenance", "beat")
SCOPES = ("run", "review", "all")


class LogError(RuntimeError):
    """日志链或心跳不连续。"""


def host_id() -> str:
    try:
        value = Path("/etc/machine-id").read_text(encoding="utf-8").strip()
    except OSError:
        value = ""
    return value or socket.gethostname()


def log_path(log_dir: Path, host: str) -> Path:
    return log_dir / f"collection-lifecycle-{host}.jsonl"


def _chain_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def read_entries(path: Path) -> list[tuple[str, dict]]:
    """读出 (原始行, 解析后的对象)；坏行直接报错，不猜。"""

    if not path.exists():
        return []
    entries: list[tuple[str, dict]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entries.append((line, json.loads(line)))
        except json.JSONDecodeError as exc:
            raise LogError(f"{path.name} 第 {exc.lineno} 行不是合法 JSON") from None
    return entries


def verify_chain(entries: list[tuple[str, dict]]) -> str | None:
    """返回 None 表示链完整，否则返回断点描述。"""

    prev: str | None = None
    expected_seq = 1
    for raw, item in entries:
        if item.get("seq") != expected_seq:
            return f"seq 断档：期望 {expected_seq}，实际 {item.get('seq')}"
        if item.get("prev") != prev:
            return f"prev 不匹配（seq={item.get('seq')}）"
        prev = _chain_hash(raw)
        expected_seq += 1
    return None


def heartbeat_gaps(entries: list[tuple[str, dict]], *, factor: int = GAP_FACTOR) -> list[tuple[str, str]]:
    """找出心跳空档超过阈值的区间（只报，不写库）。"""

    threshold = timedelta(seconds=HEARTBEAT_SECONDS * factor)
    beats = [item for _raw, item in entries if item.get("kind") in ("beat", "start", "stop")]
    gaps: list[tuple[str, str]] = []
    for earlier, later in zip(beats, beats[1:]):
        start = datetime.fromisoformat(earlier["at"])
        end = datetime.fromisoformat(later["at"])
        if end - start > threshold:
            gaps.append((earlier["at"], later["at"]))
    return gaps


def append_event(log_dir: Path, *, kind: str, scope: str, note: str | None) -> dict:
    host = host_id()
    path = log_path(log_dir, host)
    entries = read_entries(path)
    broken = verify_chain(entries)
    if broken is not None:
        # 链已经断了：继续追加只会把坏链写长。交给人工先处理。
        raise LogError(f"{path.name} 链不完整（{broken}），拒绝追加")

    seq = len(entries) + 1
    prev = _chain_hash(entries[-1][0]) if entries else None
    event = {
        "seq": seq,
        "prev": prev,
        "host": host,
        "kind": kind,
        "scope": scope,
        "at": datetime.now(SHANGHAI).isoformat(),
        "event_id": str(uuid.uuid4()),
        "note": note,
    }
    line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    log_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        with os.fdopen(fd, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise LogError(f"写日志失败（{type(exc).__name__}）") from exc
    return event


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="写采集生命周期事件到主机日志")
    parser.add_argument("--kind", choices=KINDS, help="事件类型；--verify 时可省略")
    parser.add_argument("--scope", choices=SCOPES, default="all", help="影响范围")
    parser.add_argument("--note", default=None, help="备注（不写业务数据、密钥或连接串）")
    parser.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    parser.add_argument("--verify", action="store_true", help="只校验链与心跳空档")
    args = parser.parse_args(argv)

    path = log_path(args.log_dir, host_id())
    try:
        entries = read_entries(path)
        broken = verify_chain(entries)
        gaps = heartbeat_gaps(entries)
        if args.verify:
            if broken is not None:
                print(f"链不完整：{broken}", file=sys.stderr)
                return 4
            if gaps:
                print(f"心跳空档 {len(gaps)} 处：{gaps[0][0]} → {gaps[0][1]}", file=sys.stderr)
                return 5
            print(f"日志完整：{len(entries)} 行，心跳无超阈空档。")
            return 0

        if args.kind is None:
            print("必须给 --kind（或改 --verify）。", file=sys.stderr)
            return 2
        event = append_event(args.log_dir, kind=args.kind, scope=args.scope, note=args.note)
    except LogError as exc:
        print(f"record-collection-event: {exc}", file=sys.stderr)
        return 3

    print(f"已记录 {event['kind']}（seq={event['seq']}，scope={event['scope']}）到 {path}。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
