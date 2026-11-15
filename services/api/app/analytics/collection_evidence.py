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


def _base_event_id(value: str) -> str:
    """导入器在原始 ``event_id`` 后加 ``:open``/``:pair``/``:silent``，比较时去掉后缀。"""

    return value.split(":", 1)[0]


def inspect_journal(
    directory: Path | None,
    *,
    imported_event_ids: Iterable[str],
    imported_gap_times: Iterable[tuple[datetime, datetime | None]] = (),
    now: datetime,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    scopes: Iterable[str] = ("all",),
) -> JournalEvidence | None:
    """核验日志；``None`` 表示未配置目录，不参与覆盖判定。

    心跳空档按**查询窗口**逐对检查：相邻心跳超过阈值时，必须能在
    ``collection_gap`` 里找到同区间的缺口（``:pair``/``:open`` 覆盖），或找到
    该心跳事件对应的 ``:silent`` 行；否则视为「断电后恢复但缺口未导入」。
    """

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

    allowed_scopes = {str(item) for item in scopes}
    raw_imported = {str(item) for item in imported_event_ids}
    imported_bases = {_base_event_id(item) for item in raw_imported}
    times = list(imported_gap_times)
    opening_ids: set[str] = set()
    beats: list[tuple[datetime, str]] = []
    for path in files:
        entries = _read_entries(path)
        if entries is None:
            return JournalEvidence("JOURNAL_CHAIN_BROKEN")
        if _chain_broken(entries):
            return JournalEvidence("JOURNAL_CHAIN_BROKEN")
        for _raw, item in entries:
            kind = item.get("kind")
            event_id = str(item.get("event_id") or "")
            event_scope = str(item.get("scope") or "all")
            if (
                kind in OPENING_KINDS
                and event_id
                and event_scope in allowed_scopes
            ):
                opening_ids.add(event_id)
            if kind in ("beat", "start", "stop") and item.get("at"):
                try:
                    at = datetime.fromisoformat(str(item["at"]))
                except ValueError:
                    return JournalEvidence("JOURNAL_CHAIN_BROKEN")
                if kind == "beat":
                    beats.append((at, event_id))

    if opening_ids - imported_bases:
        return JournalEvidence("JOURNAL_NOT_IMPORTED")

    if not beats:
        return JournalEvidence("NO_HEARTBEAT")
    threshold = timedelta(seconds=HEARTBEAT_SECONDS * GAP_FACTOR)

    # 日志必须覆盖查询窗口两端：只检查日志内部的空档，会把「日志从窗口中间才开始」
    # 或「日志早于窗口结束就停了」误判为完整覆盖。
    earliest = min(at for at, _event_id in beats)
    latest = max(at for at, _event_id in beats)
    if window_start is not None and earliest > window_start:
        return JournalEvidence("WINDOW_START_UNCOVERED")
    recent_window = window_end is None or window_end >= now - threshold
    if (
        not recent_window
        and window_end is not None
        and latest < window_end
    ):
        return JournalEvidence("WINDOW_END_UNCOVERED")

    # 断电恢复：没有 stop 行，只能靠心跳空档发现；空档必须已经由导入器落成缺口。
    for (earlier, earlier_id), (later, _later_id) in zip(beats, beats[1:]):
        if window_start is not None and later <= window_start:
            continue
        if window_end is not None and earlier >= window_end:
            continue
        if later - earlier <= threshold:
            continue
        covered = any(
            gap_from <= earlier and (gap_to is None or gap_to >= later)
            for gap_from, gap_to in times
        )
        if covered or f"{earlier_id}:silent" in raw_imported:
            continue
        return JournalEvidence("HEARTBEAT_GAP_NOT_IMPORTED")

    # 只对「窗口延伸到当前」的查询判心跳过期；回看历史日期时日志本来就可以停在
    # 查询范围之后，不能拿今天没有心跳去否定那一天的覆盖。
    if recent_window and now - latest > threshold:
        return JournalEvidence("JOURNAL_STALE")
    return JournalEvidence(None)
