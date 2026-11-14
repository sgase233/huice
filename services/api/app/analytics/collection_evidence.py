"""采集证据核验：主机事件日志能否证明「这段时间确实在采集」。

指标读侧不能只看 `collection_gap`：缺口表为空也可能是日志没写、没导入或主机换了。
本模块直接读 ``/var/lib/huice/collection-lifecycle-<host>.jsonl``（与 #77 的写入器同一格式），
返回一个可解释的降级原因；读侧把它套在 ``precise`` 上，变成 ``partial``。

核验顺序（返回第一个问题）：

1. 目录里一个日志文件都没有 → ``NO_JOURNAL``；
2. 日志的 host 与当前主机不一致（或同时存在多台主机）→ ``HOST_CHANGED``；
3. 任一文件哈希链断裂 / 行不合法 → ``JOURNAL_CHAIN_BROKEN``；
4. 日志里有 ``stop``/``maintenance`` 事件但 `collection_gap` 里没有对应
   ``event_id`` → ``JOURNAL_NOT_IMPORTED``（导入器没跑）；
5. 没有任何心跳 → ``NO_HEARTBEAT``；最后一次心跳超过 3 个周期 → ``JOURNAL_STALE``；
6. 全部通过 → 不降级。

``None`` 表示调用方没有配置日志目录（本地开发/未部署采集），此时不做核验。
"""

from __future__ import annotations

import hashlib
import json
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

HEARTBEAT_SECONDS = 300
GAP_FACTOR = 3
OPENING_KINDS = ("stop", "maintenance")


@dataclass(frozen=True)
class JournalEvidence:
    reason: str | None

    @property
    def ok(self) -> bool:
        return self.reason is None


def host_id() -> str:
    try:
        value = Path("/etc/machine-id").read_text(encoding="utf-8").strip()
    except OSError:
        value = ""
    return value or socket.gethostname()


def _chain_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _read_entries(path: Path) -> list[tuple[str, dict]] | None:
    entries: list[tuple[str, dict]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(item, dict):
            return None
        entries.append((line, item))
    return entries


def _chain_broken(entries: Iterable[tuple[str, dict]]) -> bool:
    prev: str | None = None
    expected_seq = 1
    for raw, item in entries:
        if item.get("seq") != expected_seq or item.get("prev") != prev:
            return True
        prev = _chain_hash(raw)
        expected_seq += 1
    return False


def inspect_journal(
    directory: Path | None,
    *,
    imported_event_ids: Iterable[str],
    now: datetime,
) -> JournalEvidence | None:
    """核验日志；``None`` 表示未配置目录，不参与覆盖判定。"""

    if directory is None:
        return None
    try:
        files = sorted(directory.glob("collection-lifecycle-*.jsonl"))
    except OSError:
        return JournalEvidence("NO_JOURNAL")
    if not files:
        return JournalEvidence("NO_JOURNAL")

    current = host_id()
    hosts = {
        path.name.removeprefix("collection-lifecycle-").removesuffix(".jsonl")
        for path in files
    }
    if len(hosts) > 1 or current not in hosts:
        return JournalEvidence("HOST_CHANGED")

    imported = {str(item) for item in imported_event_ids}
    opening_ids: set[str] = set()
    beats: list[datetime] = []
    for path in files:
        entries = _read_entries(path)
        if entries is None:
            return JournalEvidence("JOURNAL_CHAIN_BROKEN")
        if _chain_broken(entries):
            return JournalEvidence("JOURNAL_CHAIN_BROKEN")
        for _raw, item in entries:
            if item.get("kind") in OPENING_KINDS and item.get("event_id"):
                opening_ids.add(str(item["event_id"]))
            if item.get("kind") == "beat" and item.get("at"):
                try:
                    beats.append(datetime.fromisoformat(str(item["at"])))
                except ValueError:
                    return JournalEvidence("JOURNAL_CHAIN_BROKEN")

    if opening_ids - imported:
        return JournalEvidence("JOURNAL_NOT_IMPORTED")

    if not beats:
        return JournalEvidence("NO_HEARTBEAT")
    latest = max(beats)
    if now - latest > timedelta(seconds=HEARTBEAT_SECONDS * GAP_FACTOR):
        return JournalEvidence("JOURNAL_STALE")
    return JournalEvidence(None)
