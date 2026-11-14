"""``infra/deploy/backfill-review-baseline.py``：审核事件基线与两个覆盖起点。

基线只补已发布版本、只用于日终待办回放（不计 `reviewsCompleted`），两个起点按列
幂等——B2 先写 `run_lifecycle_from` 也不能挡住这里的 `review_events_from`。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from app.schemas import PolicyImportMetadata
from support import metadata_payload

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "infra" / "deploy" / "backfill-review-baseline.py"

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("clean_tables")]


def _load():
    spec = importlib.util.spec_from_file_location("backfill_review_baseline", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("backfill_review_baseline", module)
    spec.loader.exec_module(module)
    return module


baseline = _load()


def _version(repo, version: str, *, published: bool, reviewed_at: str | None = None):
    metadata = PolicyImportMetadata.model_validate(
        metadata_payload(version=version, sourceUrl="https://example.gov.cn/p.html")
    )
    repo.create_policy_version(
        metadata=metadata, sources=[], storage_keys=[], trace_id=str(uuid4())
    )
    with psycopg.connect(repo._db._dsn) as conn:
        conn.execute(
            "UPDATE policy_version SET review_status = %s, reviewed_at = %s WHERE version = %s",
            ("published" if published else "pending_parse", reviewed_at, version),
        )
        conn.commit()


def test_it_creates_baseline_events_for_published_versions(
    database_url_schema: str, repo, monkeypatch
) -> None:
    _version(repo, "v1.0", published=True, reviewed_at="2026-11-01T10:00:00+08:00")
    _version(repo, "v2.0", published=True, reviewed_at="2026-11-02T10:00:00+08:00")
    _version(repo, "v3.0", published=False)
    monkeypatch.setenv("DATABASE_URL", database_url_schema)

    assert baseline._run(True) == 0

    with psycopg.connect(database_url_schema) as conn:
        events = conn.execute(
            "SELECT pv.version, e.review_revision, e.actor_kind, e.legacy_source "
            "FROM policy_review_event e JOIN policy_version pv ON pv.id = e.policy_version_id "
            "ORDER BY pv.version"
        ).fetchall()
        start = conn.execute(
            "SELECT review_events_from, review_source FROM collection_baseline WHERE id = 'default'"
        ).fetchone()
    assert [(row[0], row[1], row[2], row[3]) for row in events] == [
        ("v1.0", 0, "baseline", "reviewed_at"),
        ("v2.0", 0, "baseline", "reviewed_at"),
    ]
    assert start[0] is not None and start[1] == "reviewed_at"


def test_a_second_run_is_a_no_op(database_url_schema: str, repo, monkeypatch, capsys) -> None:
    _version(repo, "v1.0", published=True, reviewed_at="2026-11-01T10:00:00+08:00")
    monkeypatch.setenv("DATABASE_URL", database_url_schema)
    assert baseline._run(True) == 0

    with psycopg.connect(database_url_schema) as conn:
        first = conn.execute(
            "SELECT review_events_from FROM collection_baseline WHERE id = 'default'"
        ).fetchone()[0]

    capsys.readouterr()
    assert baseline._run(True) == 0
    assert "已有审核事件基线" in capsys.readouterr().out

    with psycopg.connect(database_url_schema) as conn:
        second = conn.execute(
            "SELECT review_events_from FROM collection_baseline WHERE id = 'default'"
        ).fetchone()[0]
        count = conn.execute("SELECT count(*) FROM policy_review_event").fetchone()[0]
    assert second == first
    assert count == 1


def test_it_does_not_touch_the_run_lifecycle_start(
    database_url_schema: str, repo, monkeypatch
) -> None:
    """B2 先写了运行起点：基线只补自己那两列。"""

    _version(repo, "v1.0", published=True, reviewed_at="2026-11-01T10:00:00+08:00")
    with psycopg.connect(database_url_schema) as conn:
        conn.execute(
            "INSERT INTO collection_baseline (id, run_lifecycle_from) "
            "VALUES ('default', '2026-11-10T00:00:00+08:00')"
        )
        conn.commit()
    monkeypatch.setenv("DATABASE_URL", database_url_schema)

    assert baseline._run(True) == 0

    with psycopg.connect(database_url_schema) as conn:
        row = conn.execute(
            "SELECT review_events_from, run_lifecycle_from FROM collection_baseline "
            "WHERE id = 'default'"
        ).fetchone()
    assert row[0] is not None
    from datetime import datetime

    assert row[1] == datetime.fromisoformat("2026-11-10T00:00:00+08:00")
