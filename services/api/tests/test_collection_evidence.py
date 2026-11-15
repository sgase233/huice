"""采集证据核验（``app/analytics/collection_evidence.py``）。

M2 在 #79 P1 点名：只看 `collection_gap` 不够——日志缺失、断链、没导入、主机换掉，
都会让「缺口表为空」看起来像完整覆盖。这里逐种证据缺陷锁一个降级原因。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.analytics.collection_evidence import host_id, inspect_journal
from app.domain.versioning import SHANGHAI

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=SHANGHAI)


def _line(
    seq: int,
    prev: str | None,
    *,
    kind: str,
    at: datetime,
    event_id: str,
    scope: str = "all",
) -> str:
    return json.dumps(
        {
            "seq": seq,
            "prev": prev,
            "host": host_id(),
            "kind": kind,
            "scope": scope,
            "at": at.isoformat(),
            "event_id": event_id,
            "note": None,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _write(path: Path, entries: list[dict]) -> list[dict]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    prev: str | None = None
    for index, item in enumerate(entries, start=1):
        raw = _line(index, prev, **item)
        lines.append(raw)
        prev = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return entries


def _journal(tmp_path: Path, entries: list[dict]) -> Path:
    directory = tmp_path / "huice"
    _write(directory / f"collection-lifecycle-{host_id()}.jsonl", entries)
    return directory


def _evidence(directory: Path | None, *, imported=(), times=(), window=None, scopes=("all",)):
    return inspect_journal(
        directory,
        imported_event_ids=imported,
        imported_gap_times=times,
        now=NOW,
        window_start=window[0] if window else None,
        window_end=window[1] if window else None,
        scopes=scopes,
    )


def test_not_configured_means_no_check() -> None:
    assert _evidence(None) is None


def test_missing_directory_or_no_file_is_no_journal(tmp_path: Path) -> None:
    assert _evidence(tmp_path / "missing").reason == "NO_JOURNAL"
    empty = tmp_path / "empty"
    empty.mkdir()
    assert _evidence(empty).reason == "NO_JOURNAL"


def test_a_healthy_recent_beat_passes(tmp_path: Path) -> None:
    directory = _journal(
        tmp_path,
        [{"kind": "beat", "at": NOW - timedelta(minutes=1), "event_id": "b1"}],
    )

    assert _evidence(directory).ok


def test_a_changed_host_is_flagged(tmp_path: Path) -> None:
    directory = tmp_path / "huice"
    _write(
        directory / "collection-lifecycle-some-other-host.jsonl",
        [{"kind": "beat", "at": NOW - timedelta(minutes=1), "event_id": "b1"}],
    )

    assert _evidence(directory).reason == "HOST_CHANGED"


def test_a_broken_chain_is_flagged(tmp_path: Path) -> None:
    directory = _journal(
        tmp_path,
        [{"kind": "beat", "at": NOW - timedelta(minutes=1), "event_id": "b1"}],
    )
    path = next(directory.glob("*.jsonl"))
    path.write_text(path.read_text(encoding="utf-8").replace('"prev":null', '"prev":"deadbeef"'), encoding="utf-8")

    assert _evidence(directory).reason == "JOURNAL_CHAIN_BROKEN"


def test_an_unimported_opening_event_is_flagged(tmp_path: Path) -> None:
    directory = _journal(
        tmp_path,
        [
            {"kind": "stop", "at": NOW - timedelta(hours=2), "event_id": "stop-1"},
            {"kind": "beat", "at": NOW - timedelta(minutes=1), "event_id": "b1"},
        ],
    )

    assert _evidence(directory).reason == "JOURNAL_NOT_IMPORTED"


def test_an_imported_opening_event_passes(tmp_path: Path) -> None:
    directory = _journal(
        tmp_path,
        [
            {"kind": "stop", "at": NOW - timedelta(hours=2), "event_id": "stop-1"},
            {"kind": "beat", "at": NOW - timedelta(minutes=1), "event_id": "b1"},
        ],
    )

    assert _evidence(directory, imported={"stop-1:pair"}).ok


def test_no_heartbeat_is_flagged(tmp_path: Path) -> None:
    directory = _journal(
        tmp_path,
        [{"kind": "migrate", "at": NOW - timedelta(minutes=1), "event_id": "m1"}],
    )

    assert _evidence(directory).reason == "NO_HEARTBEAT"


@pytest.mark.parametrize("age", [16, 120])
def test_a_stale_heartbeat_is_flagged(tmp_path: Path, age: int) -> None:
    directory = _journal(
        tmp_path,
        [{"kind": "beat", "at": NOW - timedelta(minutes=age), "event_id": "b1"}],
    )

    assert _evidence(directory).reason == "JOURNAL_STALE"


def test_the_importer_event_id_suffix_is_understood(tmp_path: Path) -> None:
    directory = _journal(
        tmp_path,
        [
            {"kind": "stop", "at": NOW - timedelta(hours=2), "event_id": "stop-1"},
            {"kind": "start", "at": NOW - timedelta(hours=1), "event_id": "start-1"},
            {"kind": "beat", "at": NOW - timedelta(minutes=1), "event_id": "b1"},
        ],
    )

    assert _evidence(directory, imported={"stop-1:open", "stop-1:pair"}).ok


def test_a_heartbeat_gap_must_be_imported(tmp_path: Path) -> None:
    directory = _journal(
        tmp_path,
        [
            {"kind": "beat", "at": NOW - timedelta(hours=2), "event_id": "b1"},
            {"kind": "beat", "at": NOW - timedelta(minutes=1), "event_id": "b2"},
        ],
    )

    assert _evidence(directory).reason == "HEARTBEAT_GAP_NOT_IMPORTED"
    assert _evidence(directory, imported={"b1:silent"}).ok
    assert _evidence(
        directory,
        times=[(NOW - timedelta(hours=2), NOW - timedelta(minutes=1))],
    ).ok


def test_a_gap_outside_the_query_window_is_ignored(tmp_path: Path) -> None:
    directory = _journal(
        tmp_path,
        [
            {"kind": "beat", "at": NOW - timedelta(hours=2), "event_id": "b1"},
            {"kind": "beat", "at": NOW - timedelta(minutes=1), "event_id": "b2"},
        ],
    )

    assert _evidence(directory, window=(NOW, NOW + timedelta(hours=1))).ok


def test_stale_heartbeat_is_ignored_for_a_historical_window(tmp_path: Path) -> None:
    directory = _journal(
        tmp_path,
        [
            {"kind": "beat", "at": NOW - timedelta(days=3), "event_id": "b1"},
            {"kind": "beat", "at": NOW - timedelta(days=2), "event_id": "b2"},
        ],
    )

    assert _evidence(
        directory,
        imported={"b1:silent"},
        window=(NOW - timedelta(days=3), NOW - timedelta(days=2)),
    ).ok


def test_journal_starting_after_the_window_start_is_flagged(tmp_path: Path) -> None:
    directory = _journal(
        tmp_path,
        [{"kind": "beat", "at": NOW - timedelta(hours=1), "event_id": "b1"}],
    )

    assert _evidence(
        directory, window=(NOW - timedelta(hours=2), NOW + timedelta(hours=1))
    ).reason == "WINDOW_START_UNCOVERED"


def test_journal_ending_before_a_historical_window_end_is_flagged(tmp_path: Path) -> None:
    directory = _journal(
        tmp_path,
        [
            {"kind": "beat", "at": NOW - timedelta(hours=5), "event_id": "b0"},
            {"kind": "beat", "at": NOW - timedelta(hours=3), "event_id": "b1"},
        ],
    )

    assert _evidence(
        directory, window=(NOW - timedelta(hours=4), NOW - timedelta(hours=1))
    ).reason == "WINDOW_END_UNCOVERED"


def test_opening_events_follow_the_metric_scope(tmp_path: Path) -> None:
    directory = _journal(
        tmp_path,
        [
            {"kind": "stop", "at": NOW - timedelta(hours=2), "event_id": "stop-1", "scope": "run"},
            {"kind": "beat", "at": NOW - timedelta(minutes=1), "event_id": "b1"},
        ],
    )

    # 只影响运行的停采事件，不应拖累审核指标；运行指标仍要求它已导入。
    assert _evidence(directory, scopes=("review", "all")).ok
    assert _evidence(directory, scopes=("run", "all")).reason == "JOURNAL_NOT_IMPORTED"
    assert _evidence(
        directory, imported={"stop-1:pair"}, scopes=("run", "all")
    ).ok


def test_review_scoped_opening_does_not_block_run_metrics(tmp_path: Path) -> None:
    directory = _journal(
        tmp_path,
        [
            {
                "kind": "maintenance",
                "at": NOW - timedelta(hours=2),
                "event_id": "maint-1",
                "scope": "review",
            },
            {"kind": "beat", "at": NOW - timedelta(minutes=1), "event_id": "b1"},
        ],
    )

    assert _evidence(directory, scopes=("run", "all")).ok
    assert _evidence(directory, scopes=("review", "all")).reason == "JOURNAL_NOT_IMPORTED"


def test_all_scoped_opening_blocks_both_metrics(tmp_path: Path) -> None:
    directory = _journal(
        tmp_path,
        [
            {"kind": "stop", "at": NOW - timedelta(hours=2), "event_id": "stop-1", "scope": "all"},
            {"kind": "beat", "at": NOW - timedelta(minutes=1), "event_id": "b1"},
        ],
    )

    assert _evidence(directory, scopes=("review", "all")).reason == "JOURNAL_NOT_IMPORTED"
    assert _evidence(directory, scopes=("run", "all")).reason == "JOURNAL_NOT_IMPORTED"
