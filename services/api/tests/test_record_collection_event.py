"""``infra/deploy/record-collection-event.py``：主机侧采集事件日志。

这里锁住三件事：哈希链能发现截断、心跳空档能发现断电（没有 stop 行）、链断了拒绝
继续追加。都是不连库的用例——这个日志存在的意义就是库不可用时也能写。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "infra" / "deploy" / "record-collection-event.py"


def _load():
    spec = importlib.util.spec_from_file_location("record_collection_event", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("record_collection_event", module)
    spec.loader.exec_module(module)
    return module


recorder = _load()


def test_append_builds_a_verifiable_chain(tmp_path: Path) -> None:
    recorder.append_event(tmp_path, kind="start", scope="all", note=None)
    recorder.append_event(tmp_path, kind="beat", scope="all", note=None)
    recorder.append_event(tmp_path, kind="stop", scope="all", note=None)

    path = recorder.log_path(tmp_path, recorder.host_id())
    entries = recorder.read_entries(path)

    assert [item["kind"] for _raw, item in entries] == ["start", "beat", "stop"]
    assert recorder.verify_chain(entries) is None


def test_a_truncated_log_is_detected(tmp_path: Path) -> None:
    """删掉中间一行：链断，读侧必须能发现（LOG_CHAIN_BROKEN 的依据）。"""

    recorder.append_event(tmp_path, kind="start", scope="all", note=None)
    recorder.append_event(tmp_path, kind="beat", scope="all", note=None)
    recorder.append_event(tmp_path, kind="beat", scope="all", note=None)

    path = recorder.log_path(tmp_path, recorder.host_id())
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines[:1] + lines[2:]) + "\n", encoding="utf-8")

    assert recorder.verify_chain(recorder.read_entries(path)) is not None


def test_a_power_cut_show_up_as_a_heartbeat_gap(tmp_path: Path) -> None:
    """没有 stop 行（断电/被杀）：两次心跳之间的空档就是缺口。"""

    path = recorder.log_path(tmp_path, recorder.host_id())
    base = datetime.fromisoformat("2026-11-20T10:00:00+08:00")
    rows = []
    prev = None
    for seq, offset in enumerate([0, 5, 10, 40], start=1):
        item = {
            "seq": seq,
            "prev": prev,
            "host": recorder.host_id(),
            "kind": "beat",
            "scope": "all",
            "at": (base + timedelta(minutes=offset)).isoformat(),
            "event_id": f"e{seq}",
            "note": None,
        }
        raw = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
        rows.append(raw)
        import hashlib

        prev = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")

    entries = recorder.read_entries(path)
    assert recorder.verify_chain(entries) is None
    gaps = recorder.heartbeat_gaps(entries)
    assert gaps == [("2026-11-20T10:10:00+08:00", "2026-11-20T10:40:00+08:00")]


def test_append_refuses_a_broken_chain(tmp_path: Path) -> None:
    recorder.append_event(tmp_path, kind="start", scope="all", note=None)
    recorder.append_event(tmp_path, kind="beat", scope="all", note=None)

    path = recorder.log_path(tmp_path, recorder.host_id())
    lines = path.read_text(encoding="utf-8").splitlines()
    # 删掉第一行：后面的 seq 从 2 开始，链在这里断
    path.write_text("\n".join(lines[1:]) + "\n", encoding="utf-8")

    with pytest.raises(recorder.LogError, match="链不完整"):
        recorder.append_event(tmp_path, kind="beat", scope="all", note=None)


def test_verify_reports_broken_chain_and_gap(tmp_path: Path) -> None:
    assert recorder.main(["--kind", "start", "--log-dir", str(tmp_path)]) == 0
    assert recorder.main(["--verify", "--log-dir", str(tmp_path)]) == 0

    path = recorder.log_path(tmp_path, recorder.host_id())
    path.write_text("", encoding="utf-8")
    recorder.append_event(tmp_path, kind="beat", scope="all", note=None)
    path.write_text(path.read_text(encoding="utf-8").replace('"seq":1', '"seq":9'), encoding="utf-8")

    assert recorder.main(["--verify", "--log-dir", str(tmp_path)]) == 4


@pytest.mark.skipif(recorder.fcntl is None, reason="fcntl 只在 POSIX 上可用")
def test_concurrent_appends_keep_the_chain_valid(tmp_path: Path) -> None:
    """心跳、启停、人工命令同时写：加锁后不该出现重复 seq。"""

    import threading

    threads = [
        threading.Thread(
            target=recorder.append_event,
            args=(tmp_path,),
            kwargs={"kind": "beat", "scope": "all", "note": None},
        )
        for _ in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    path = recorder.log_path(tmp_path, recorder.host_id())
    entries = recorder.read_entries(path)
    assert len(entries) == 8
    assert recorder.verify_chain(entries) is None


@pytest.mark.skipif(recorder.os.name != "posix", reason="权限位只在 POSIX 上有意义")
def test_the_lock_and_log_files_are_group_writable(tmp_path: Path) -> None:
    """root 先用默认 umask 建了锁文件：服务账号也要能打开并追加。

    这里先造一个 0644 的锁文件（模拟 root 首次执行），再追加一次，断言锁与日志都被
    正回 0660。
    """

    host = recorder.host_id()
    lock_path = tmp_path / f".collection-lifecycle-{host}.lock"
    lock_path.write_text("", encoding="utf-8")
    lock_path.chmod(0o644)

    recorder.append_event(tmp_path, kind="beat", scope="all", note=None)

    log_path = recorder.log_path(tmp_path, host)
    assert (lock_path.stat().st_mode & 0o777) == 0o660
    assert (log_path.stat().st_mode & 0o777) == 0o660
