"""``infra/deploy/revoke-sessions.py``：会话迁移的守卫（PR #84 复审 P1）。

脚本默认只统计、``--apply`` 才写 ``revoked_at``，所以这里断言两件事：参数不合规时
不动数据库、只报变量名或规则；带库的用例确认 dry-run 不写、``--apply`` 只撤活跃会话。
"""

from __future__ import annotations

import datetime
import importlib.util
import os
import subprocess
import sys
import uuid
from pathlib import Path

import psycopg
import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "infra" / "deploy" / "revoke-sessions.py"

DSN = "postgresql://huice:huice@127.0.0.1:5432/huice_test"
PASSWORD_HASH = "$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$hash-placeholder"


def run_script(*args: str, **env_overrides) -> subprocess.CompletedProcess:
    env = {key: value for key, value in os.environ.items() if key != "DATABASE_URL"}
    env.update({key: value for key, value in env_overrides.items() if value is not None})
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


def load_script():
    """按路径加载脚本模块（文件名带连字符，不能直接 import）。"""

    spec = importlib.util.spec_from_file_location("revoke_sessions_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_update_statement_does_not_join_the_update_target_twice():
    """`UPDATE user_session s … FROM user_session s` 会让别名重复声明而报错。

    带库用例在 CI 上才发现过一次；这条不连库，本地就能挡住同类回归。
    """

    module = load_script()
    where = "s.revoked_at IS NULL"

    counted = module.count_sql(where)
    revoked = module.revoke_sql(where)

    assert counted.count("user_session") == 1
    assert revoked.startswith("UPDATE user_session s SET revoked_at = now()")
    assert revoked.count("user_session") == 1
    assert "JOIN app_user" in counted
    assert "FROM app_user u WHERE u.id = s.user_id" in revoked


def test_boundary_format_keeps_microseconds_and_offset():
    """边界要带微秒与时区：秒精度会漏掉同一秒内签发的旧会话。"""

    module = load_script()
    value = datetime.datetime(
        2026, 11, 25, 8, 0, 0, 123456, tzinfo=datetime.timezone(datetime.timedelta(hours=8))
    )

    assert module.format_boundary(value) == "2026-11-25T08:00:00.123456+08:00"


def test_print_boundary_cannot_be_combined_with_apply():
    result = run_script("--print-boundary", "--apply", DATABASE_URL=DSN)

    assert result.returncode == 2
    assert "--print-boundary" in result.stderr


def test_missing_database_url_is_named_but_never_printed():
    result = run_script(DATABASE_URL=None)

    assert result.returncode == 2
    assert "DATABASE_URL" in result.stderr
    assert "postgresql://" not in result.stdout + result.stderr


def test_a_non_postgres_url_is_rejected():
    result = run_script(DATABASE_URL="sqlite:///tmp/x.db")

    assert result.returncode == 2
    assert "DATABASE_URL" in result.stderr
    assert "sqlite" not in result.stderr


def test_an_invalid_username_is_rejected_before_touching_the_database():
    result = run_script("--username", "Not Allowed!", DATABASE_URL=DSN)

    assert result.returncode == 2
    assert "--username" in result.stderr


@pytest.mark.parametrize("value", ["2026-11-24T16:00:00", "不是时间"])
def test_before_requires_an_iso_timestamp_with_offset(value):
    """不带时区就拒绝：猜错时区会把还没过期的旧会话算成新会话，判据失真。"""

    result = run_script("--before", value, DATABASE_URL=DSN)

    assert result.returncode == 2
    assert "--before" in result.stderr


@pytest.mark.db
def test_dry_run_counts_without_writing_and_apply_revokes_live_sessions(database_url_schema):
    old_live = uuid.uuid4().hex + uuid.uuid4().hex
    old_expired = uuid.uuid4().hex + uuid.uuid4().hex
    new_live = uuid.uuid4().hex + uuid.uuid4().hex
    username = f"revoke.{uuid.uuid4().hex[:8]}"
    # 切换时刻取在「旧会话创建之后、新会话创建之前」：--before 之后应只剩新会话不计入。
    cut = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)

    with psycopg.connect(database_url_schema) as conn:
        user_id = conn.execute(
            "INSERT INTO app_user (id, username, password_hash, role) "
            "VALUES (gen_random_uuid(), %s, %s, 'enterprise') RETURNING id",
            (username, PASSWORD_HASH),
        ).fetchone()[0]
        conn.execute(
            # 过期那条要同时把 created_at 往前挪：user_session_expiry 要求 expires_at > created_at。
            "INSERT INTO user_session (id, user_id, token_hash, csrf_hash, created_at, expires_at) "
            "VALUES (gen_random_uuid(), %s, %s, %s, "
            "        now() - interval '3 hours', now() + interval '1 hour'), "
            "       (gen_random_uuid(), %s, %s, %s, "
            "        now() - interval '26 hours', now() - interval '2 hours'), "
            "       (gen_random_uuid(), %s, %s, %s, now(), now() + interval '1 hour')",
            (
                user_id,
                old_live,
                uuid.uuid4().hex + uuid.uuid4().hex,
                user_id,
                old_expired,
                uuid.uuid4().hex + uuid.uuid4().hex,
                user_id,
                new_live,
                uuid.uuid4().hex + uuid.uuid4().hex,
            ),
        )

    dry = run_script(
        "--username", username, "--before", cut.isoformat(), DATABASE_URL=database_url_schema
    )

    assert dry.returncode == 0, dry.stderr
    assert "dry-run" in dry.stdout
    assert "1 条" in dry.stdout
    with psycopg.connect(database_url_schema) as conn:
        revoked = conn.execute(
            "SELECT count(*) FROM user_session WHERE revoked_at IS NOT NULL AND user_id = %s",
            (user_id,),
        ).fetchone()[0]
    assert revoked == 0

    applied = run_script(
        "--username",
        username,
        "--before",
        cut.isoformat(),
        "--apply",
        DATABASE_URL=database_url_schema,
    )

    assert applied.returncode == 0, applied.stderr
    assert "已撤销" in applied.stdout
    with psycopg.connect(database_url_schema) as conn:
        rows = dict(
            conn.execute(
                "SELECT token_hash, revoked_at IS NOT NULL FROM user_session "
                "WHERE user_id = %s",
                (user_id,),
            ).fetchall()
        )
    # 只撤「切换前创建且仍活跃」那条：过期会话本来就用不了；切换后新建的 Secure 会话不动
    # （否则用户重新登录后永远满足不了「旧会话清零」这个判据）。
    assert rows[old_live] is True
    assert rows[old_expired] is False
    assert rows[new_live] is False


@pytest.mark.db
def test_print_boundary_uses_the_database_clock_and_covers_same_second_sessions(
    database_url_schema,
):
    """停服后取的边界必须覆盖同一秒内创建的会话（`date -Iseconds` 会漏掉）。"""

    token = uuid.uuid4().hex + uuid.uuid4().hex
    username = f"boundary.{uuid.uuid4().hex[:8]}"

    with psycopg.connect(database_url_schema) as conn:
        user_id = conn.execute(
            "INSERT INTO app_user (id, username, password_hash, role) "
            "VALUES (gen_random_uuid(), %s, %s, 'enterprise') RETURNING id",
            (username, PASSWORD_HASH),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO user_session (id, user_id, token_hash, csrf_hash, created_at, expires_at) "
            "VALUES (gen_random_uuid(), %s, %s, %s, now(), now() + interval '1 hour')",
            (user_id, token, uuid.uuid4().hex + uuid.uuid4().hex),
        )

    printed = run_script("--print-boundary", DATABASE_URL=database_url_schema)

    assert printed.returncode == 0, printed.stderr
    boundary = printed.stdout.strip()
    parsed = datetime.datetime.fromisoformat(boundary)
    # 边界来自数据库时钟：带时区，且与 created_at 同一来源，不存在两台机器的偏差。
    assert parsed.tzinfo is not None

    counted = run_script(
        "--username", username, "--before", boundary, DATABASE_URL=database_url_schema
    )

    assert counted.returncode == 0, counted.stderr
    assert "1 条" in counted.stdout

    applied = run_script(
        "--username",
        username,
        "--before",
        boundary,
        "--apply",
        DATABASE_URL=database_url_schema,
    )

    assert applied.returncode == 0, applied.stderr
    with psycopg.connect(database_url_schema) as conn:
        revoked = conn.execute(
            "SELECT revoked_at IS NOT NULL FROM user_session WHERE token_hash = %s",
            (token,),
        ).fetchone()[0]
    assert revoked is True
