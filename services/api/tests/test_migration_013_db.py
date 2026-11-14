"""迁移 ``013_collection_gap.sql`` 的真实数据库用例（E3 / B3）。

这张表是「采集中断过吗」的证据：唯一键保证同一事件重复导入不新增，未闭合区间
（`gap_to IS NULL`）表示断电后还没恢复。
"""

from __future__ import annotations

import psycopg
import pytest

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("clean_tables")]


def _insert(conn, **overrides):
    values = {
        "source": "host-journal",
        "event_id": "evt-1",
        "host": "host-a",
        "kind": "stop",
        "scope": "all",
        "gap_from": "2026-11-20T10:00:00+08:00",
        "gap_to": None,
        "reason": None,
        "recorded_by": "record-collection-event.py",
        **overrides,
    }
    conn.execute(
        "INSERT INTO collection_gap (source, event_id, host, kind, scope, gap_from, gap_to, "
        "reason, recorded_by) VALUES (%(source)s, %(event_id)s, %(host)s, %(kind)s, %(scope)s, "
        "%(gap_from)s, %(gap_to)s, %(reason)s, %(recorded_by)s)",
        values,
    )


def test_the_table_exists(database_url_schema: str) -> None:
    with psycopg.connect(database_url_schema) as conn:
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'collection_gap' "
            "ORDER BY ordinal_position"
        ).fetchall()
    assert [row[0] for row in rows] == [
        "id", "source", "event_id", "host", "kind", "scope",
        "gap_from", "gap_to", "reason", "recorded_by", "created_at",
    ]


def test_the_event_id_is_unique(database_url_schema: str) -> None:
    with psycopg.connect(database_url_schema) as conn:
        _insert(conn)
        with pytest.raises(psycopg.errors.UniqueViolation):
            _insert(conn, host="host-b")
        conn.rollback()


def test_an_unclosed_gap_and_the_interval_check(database_url_schema: str) -> None:
    with psycopg.connect(database_url_schema) as conn:
        # 未闭合区间合法（断裂的 stop）
        _insert(conn, event_id="evt-open")
        # 结束早于开始 → 拒绝
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert(
                conn,
                event_id="evt-bad",
                gap_from="2026-11-20T12:00:00+08:00",
                gap_to="2026-11-20T10:00:00+08:00",
            )
        conn.rollback()

    with psycopg.connect(database_url_schema) as conn:
        open_rows = conn.execute(
            "SELECT count(*) FROM collection_gap WHERE gap_to IS NULL"
        ).fetchone()[0]
    assert open_rows == 0, "上面 rollback 过，表应为空"
