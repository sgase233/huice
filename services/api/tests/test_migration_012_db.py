"""迁移 ``012_e3_metrics.sql`` 的真实数据库用例（E3 / D5，B2）。

三条必须成立：结构就位、已发布版本不被碰、运行行只放开该放开的那三列。最后一条是这次
改造的安全边界——``error_category`` 要能写，而 ``lock_owner_pid`` / ``lock_owner_started_at``
描述的是「T1 时是谁占的锁」，被改写就失去判断中断的依据。
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import psycopg
import pytest

from db_support import create, query_all, query_one

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("clean_tables")]

RULE_VERSION = "rule:criteria@1"
FACT_VERSION = "f" * 64


def _enterprise(conn) -> object:
    enterprise_id = uuid4()
    conn.execute(
        "INSERT INTO enterprise (id, name_synthetic, period_start, period_end) "
        "VALUES (%s, %s, %s, %s)",
        (enterprise_id, "合成演示企业", "2026-01-01", "2026-12-31"),
    )
    return enterprise_id


def _run(conn, enterprise_id, *, status: str = "running", **extra) -> object:
    run_id = uuid4()
    columns = {
        "id": run_id,
        "enterprise_id": enterprise_id,
        "fact_version": FACT_VERSION,
        "period_start": "2026-01-01",
        "period_end": "2026-12-31",
        "lock_owner_pid": 4242,
        "lock_owner_started_at": "2026-11-14T00:00:00+08:00",
        **extra,
    }
    conn.execute(
        "INSERT INTO assessment_run "
        "(id, enterprise_id, as_of, status, fact_version, model_or_rule_version, "
        " period_start, period_end, lock_owner_pid, lock_owner_started_at) "
        "VALUES (%(id)s, %(enterprise_id)s, now(), %(status)s, %(fact_version)s, "
        " %(rule)s, %(period_start)s, %(period_end)s, "
        " %(lock_owner_pid)s, %(lock_owner_started_at)s)",
        {**columns, "status": status, "rule": RULE_VERSION},
    )
    return run_id


def test_event_and_baseline_tables_exist(database_url_schema: str) -> None:
    rows = query_all(
        database_url_schema,
        "SELECT table_name, column_name, data_type, is_nullable "
        "FROM information_schema.columns "
        "WHERE table_schema = current_schema() "
        "AND table_name IN ('policy_review_event', 'collection_baseline') "
        "ORDER BY table_name, ordinal_position",
    )
    assert [(row[0], row[1]) for row in rows] == [
        ("collection_baseline", "id"),
        ("collection_baseline", "review_events_from"),
        ("collection_baseline", "review_source"),
        ("collection_baseline", "run_lifecycle_from"),
        ("collection_baseline", "note"),
        ("collection_baseline", "created_at"),
        ("policy_review_event", "id"),
        ("policy_review_event", "policy_version_id"),
        ("policy_review_event", "policy_id"),
        ("policy_review_event", "review_revision"),
        ("policy_review_event", "actor_kind"),
        ("policy_review_event", "actor_user_id"),
        ("policy_review_event", "occurred_at"),
        ("policy_review_event", "legacy_source"),
        ("policy_review_event", "created_at"),
    ]


def test_migration_is_registered(database_url_schema: str) -> None:
    row = query_one(
        database_url_schema,
        "SELECT description FROM schema_migration WHERE id = '012_e3_metrics'",
    )
    assert row is not None
    assert "审核事件" in row[0]


def test_run_status_accepts_interrupted_and_rejects_unknown(database_url_schema: str) -> None:
    with psycopg.connect(database_url_schema) as conn:
        enterprise_id = _enterprise(conn)
        run_id = _run(conn, enterprise_id)
        conn.execute("UPDATE assessment_run SET status = 'interrupted' WHERE id = %s", (run_id,))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("UPDATE assessment_run SET status = 'cancelled' WHERE id = %s", (run_id,))
        conn.rollback()


def test_error_category_domain(database_url_schema: str) -> None:
    with psycopg.connect(database_url_schema) as conn:
        enterprise_id = _enterprise(conn)
        run_id = _run(conn, enterprise_id)
        conn.execute(
            "UPDATE assessment_run SET status = 'failed', finished_at = now(), "
            "error_category = 'model_unavailable' WHERE id = %s",
            (run_id,),
        )
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "UPDATE assessment_run SET error_category = 'boom' WHERE id = %s", (run_id,)
            )
        conn.rollback()


def test_lock_owner_and_identity_stay_frozen(database_url_schema: str) -> None:
    """锁两列与身份列写进去就冻结：改了就没法判断「原连接是否还在」。"""
    with psycopg.connect(database_url_schema) as conn:
        enterprise_id = _enterprise(conn)
        run_id = _run(conn, enterprise_id)
        conn.commit()  # 先落行：下面的失败更新会中止事务，行必须已经提交
        for column, value in (
            ("lock_owner_pid", 999),
            ("lock_owner_started_at", None),
            ("user_id", uuid4()),
            ("created_at", datetime(2020, 1, 1, tzinfo=timezone.utc)),
        ):
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    f"UPDATE assessment_run SET {column} = %s WHERE id = %s", (value, run_id)
                )
            conn.rollback()


def test_review_revision_defaults_to_one_without_touching_rows(
    database_url_schema: str, repo
) -> None:
    """``ADD COLUMN ... DEFAULT`` 不写行：已有版本 xmin 不变，重跑迁移同样不动。"""
    version = create(repo, version="深工信规〔2025〕4号")
    before = query_one(
        database_url_schema,
        "SELECT id, xmin::text, review_revision FROM policy_version WHERE policy_id = %s",
        (version.id,),
    )
    assert before[2] == 1

    with psycopg.connect(database_url_schema) as conn:
        # 重放迁移里动 policy_version 的那条语句：IF NOT EXISTS 之下必须是纯 no-op
        conn.execute(
            "ALTER TABLE policy_version ADD COLUMN IF NOT EXISTS "
            "review_revision integer NOT NULL DEFAULT 1"
        )
        conn.commit()

    after = query_one(
        database_url_schema,
        "SELECT id, xmin::text, review_revision FROM policy_version WHERE policy_id = %s",
        (version.id,),
    )
    assert after == before


def test_review_event_unique_and_actor_rules(database_url_schema: str, repo) -> None:
    version = create(repo, version="深工信规〔2025〕4号")
    version_id, policy_id = query_one(
        database_url_schema,
        "SELECT id, policy_id FROM policy_version WHERE policy_id = %s",
        (version.id,),
    )

    with psycopg.connect(database_url_schema) as conn:
        user_id = uuid4()
        conn.execute(
            "INSERT INTO app_user (id, username, password_hash, role) VALUES (%s, %s, %s, 'reviewer')",
            (user_id, "reviewer.one", "x" * 16),
        )
        insert = (
            "INSERT INTO policy_review_event "
            "(policy_version_id, policy_id, review_revision, actor_kind, actor_user_id, "
            " occurred_at, legacy_source) VALUES (%s, %s, %s, %s, %s, now(), %s)"
        )
        conn.execute(
            insert, (version_id, policy_id, 1, "user", user_id, None)
        )
        # 同一版本同一轮次只能有一条：重放或并发都会撞这个唯一键
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(insert, (version_id, policy_id, 1, "user", user_id, None))
        conn.rollback()

        # 用户审核事件必须带账号；基线事件必须带 legacy_source
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(insert, (version_id, policy_id, 1, "user", None, None))
        conn.rollback()
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(insert, (version_id, policy_id, 0, "baseline", None, None))
        conn.rollback()

        # 基线事件可以带来源标记
        conn.execute(insert, (version_id, policy_id, 0, "baseline", None, "reviewed_at"))
        conn.commit()

    count = query_one(
        database_url_schema,
        "SELECT count(*) FROM policy_review_event WHERE policy_version_id = %s",
        (version_id,),
    )
    assert count[0] == 1


def test_baseline_start_pairing(database_url_schema: str) -> None:
    """两个起点各自独立：任一为 NULL 都合法，但 review_source 必须与起点成对出现。"""
    with psycopg.connect(database_url_schema) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO collection_baseline (id, review_source) VALUES ('default', 'unknown')"
            )
        conn.rollback()

        conn.execute(
            "INSERT INTO collection_baseline (id, review_events_from, review_source) "
            "VALUES ('default', now(), 'reviewed_at')"
        )
        conn.commit()
        # B2 后到：只补自己那列，不碰审核侧的值
        conn.execute(
            "UPDATE collection_baseline SET run_lifecycle_from = now() WHERE id = 'default'"
        )
        conn.commit()

    row = query_one(
        database_url_schema,
        "SELECT review_source, run_lifecycle_from IS NOT NULL FROM collection_baseline "
        "WHERE id = 'default'",
    )
    assert row == ("reviewed_at", True)
