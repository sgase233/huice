"""``infra/deploy/import-collection-journal.py``：日志 → ``collection_gap``。

两类缺口都要能从日志推出来：配对缺口（stop → start）与心跳缺口（没有 stop 也发现）。
链断了拒绝导入——不能把不完整的日志当成完整证据。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import psycopg
import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "infra" / "deploy" / "import-collection-journal.py"


def _load():
    spec = importlib.util.spec_from_file_location("import_collection_journal", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("import_collection_journal", module)
    spec.loader.exec_module(module)
    return module


importer = _load()
recorder = importer.recorder


def _write_log(path: Path, items: list[dict]) -> None:
    import hashlib

    prev = None
    lines = []
    for seq, item in enumerate(items, start=1):
        item = {**item, "seq": seq, "prev": prev, "host": "host-a", "event_id": f"e{seq}"}
        raw = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
        lines.append(raw)
        prev = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _at(minutes: int) -> str:
    base = datetime.fromisoformat("2026-11-20T10:00:00+08:00")
    return (base + timedelta(minutes=minutes)).isoformat()


def test_a_stop_start_pair_becomes_a_closed_gap(tmp_path: Path) -> None:
    path = tmp_path / "collection-lifecycle-host-a.jsonl"
    _write_log(path, [
        {"kind": "start", "scope": "all", "at": _at(0), "note": None},
        {"kind": "stop", "scope": "run", "at": _at(10), "note": "deploy"},
        {"kind": "start", "scope": "all", "at": _at(20), "note": None},
    ])

    gaps, broken = importer.build_gaps(path)

    assert broken is None
    assert [(gap["gap_from"], gap["gap_to"], gap["scope"]) for gap in gaps] == [
        (_at(10), _at(20), "run")
    ]


def test_a_stop_without_start_is_an_open_gap(tmp_path: Path) -> None:
    path = tmp_path / "collection-lifecycle-host-a.jsonl"
    _write_log(path, [
        {"kind": "start", "scope": "all", "at": _at(0), "note": None},
        {"kind": "stop", "scope": "all", "at": _at(10), "note": "断电"},
    ])

    gaps, _broken = importer.build_gaps(path)

    assert len(gaps) == 1
    assert gaps[0]["gap_to"] is None
    assert gaps[0]["event_id"].endswith(":open")


def test_a_heartbeat_gap_is_a_gap_even_without_stop(tmp_path: Path) -> None:
    path = tmp_path / "collection-lifecycle-host-a.jsonl"
    _write_log(path, [
        {"kind": "start", "scope": "all", "at": _at(0), "note": None},
        {"kind": "beat", "scope": "all", "at": _at(5), "note": None},
        {"kind": "beat", "scope": "all", "at": _at(45), "note": None},
    ])

    gaps, _broken = importer.build_gaps(path)

    silent = [gap for gap in gaps if gap["reason"] == "HOST_SILENT"]
    assert len(silent) == 1
    assert (silent[0]["gap_from"], silent[0]["gap_to"]) == (_at(5), _at(45))


def test_a_broken_chain_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "collection-lifecycle-host-a.jsonl"
    _write_log(path, [
        {"kind": "start", "scope": "all", "at": _at(0), "note": None},
        {"kind": "stop", "scope": "all", "at": _at(10), "note": None},
    ])
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines[1:]) + "\n", encoding="utf-8")

    gaps, broken = importer.build_gaps(path)
    assert gaps == []
    assert broken is not None


@pytest.mark.db
@pytest.mark.usefixtures("clean_tables")
def test_import_is_idempotent(database_url_schema, tmp_path, monkeypatch, capsys) -> None:
    path = tmp_path / "collection-lifecycle-host-a.jsonl"
    _write_log(path, [
        {"kind": "start", "scope": "all", "at": _at(0), "note": None},
        {"kind": "stop", "scope": "all", "at": _at(10), "note": "deploy"},
        {"kind": "start", "scope": "all", "at": _at(20), "note": None},
    ])
    monkeypatch.setenv("DATABASE_URL", database_url_schema)

    assert importer.run(tmp_path, apply=True) == 0
    assert "已导入 1 条" in capsys.readouterr().out

    assert importer.run(tmp_path, apply=True) == 0
    assert "已导入 0 条" in capsys.readouterr().out

    with psycopg.connect(database_url_schema) as conn:
        count = conn.execute("SELECT count(*) FROM collection_gap").fetchone()[0]
    assert count == 1


def test_a_trailing_truncation_is_refused(tmp_path: Path) -> None:
    """写到一半被打断：最后一行没有换行，链上看不出来，单独判截断。"""

    path = tmp_path / "collection-lifecycle-host-a.jsonl"
    _write_log(path, [
        {"kind": "start", "scope": "all", "at": _at(0), "note": None},
    ])
    path.write_text(path.read_text(encoding="utf-8").rstrip("\n"), encoding="utf-8")

    gaps, broken = importer.build_gaps(path)
    assert gaps == []
    assert "尾部截断" in broken


@pytest.mark.db
@pytest.mark.usefixtures("clean_tables")
def test_an_open_gap_is_closed_when_the_host_comes_back(
    database_url_schema, tmp_path, monkeypatch
) -> None:
    """先只有 stop（未闭合），后来出现 start：:open 行被 :pair 行取代，不留两份。"""

    path = tmp_path / "collection-lifecycle-host-a.jsonl"
    _write_log(path, [
        {"kind": "start", "scope": "all", "at": _at(0), "note": None},
        {"kind": "stop", "scope": "all", "at": _at(10), "note": "断电"},
    ])
    monkeypatch.setenv("DATABASE_URL", database_url_schema)
    assert importer.run(tmp_path, apply=True) == 0

    # 主机恢复：整份日志重写成「start / stop / start」（保持 host-a 与链一致）
    _write_log(path, [
        {"kind": "start", "scope": "all", "at": _at(0), "note": None},
        {"kind": "stop", "scope": "all", "at": _at(10), "note": "断电"},
        {"kind": "start", "scope": "all", "at": _at(30), "note": None},
    ])
    assert importer.run(tmp_path, apply=True) == 0

    with psycopg.connect(database_url_schema) as conn:
        rows = conn.execute("SELECT event_id, gap_to FROM collection_gap ORDER BY event_id").fetchall()
    assert len(rows) == 1
    assert rows[0][0].endswith(":pair")
    assert rows[0][1] is not None


def test_a_migrate_event_does_not_open_a_gap(tmp_path: Path) -> None:
    """迁移不停服务：migrate 只是信息标记，不能留下「持续到当前」的假缺口。"""

    path = tmp_path / "collection-lifecycle-host-a.jsonl"
    _write_log(path, [
        {"kind": "start", "scope": "all", "at": _at(0), "note": None},
        {"kind": "migrate", "scope": "all", "at": _at(2), "note": None},
        {"kind": "beat", "scope": "all", "at": _at(5), "note": None},
    ])

    gaps, broken = importer.build_gaps(path)

    assert broken is None
    assert gaps == []
