"""``infra/deploy/revoke-sessions.py``：会话迁移的守卫（PR #84 复审 P1）。

脚本默认只统计、``--apply`` 才写 ``revoked_at``，所以这里断言两件事：参数不合规时
不动数据库、只报变量名或规则；带库的用例确认 dry-run 不写、``--apply`` 只撤活跃会话。
"""

from __future__ import annotations

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


@pytest.mark.db
def test_dry_run_counts_without_writing_and_apply_revokes_live_sessions(database_url_schema):
    live_token = uuid.uuid4().hex + uuid.uuid4().hex
    spent_token = uuid.uuid4().hex + uuid.uuid4().hex
    username = f"revoke.{uuid.uuid4().hex[:8]}"

    with psycopg.connect(database_url_schema) as conn:
        user_id = conn.execute(
            "INSERT INTO app_user (id, username, password_hash, role) "
            "VALUES (gen_random_uuid(), %s, %s, 'enterprise') RETURNING id",
            (username, PASSWORD_HASH),
        ).fetchone()[0]
        conn.execute(
            # 过期那条要同时把 created_at 往前挪：user_session_expiry 要求 expires_at > created_at。
            "INSERT INTO user_session (id, user_id, token_hash, csrf_hash, created_at, expires_at) "
            "VALUES (gen_random_uuid(), %s, %s, %s, now(), now() + interval '1 hour'), "
            "       (gen_random_uuid(), %s, %s, %s, "
            "        now() - interval '2 hours', now() - interval '1 hour')",
            (
                user_id,
                live_token,
                uuid.uuid4().hex + uuid.uuid4().hex,
                user_id,
                spent_token,
                uuid.uuid4().hex + uuid.uuid4().hex,
            ),
        )

    dry = run_script("--username", username, DATABASE_URL=database_url_schema)

    assert dry.returncode == 0, dry.stderr
    assert "dry-run" in dry.stdout
    assert "1 条" in dry.stdout
    with psycopg.connect(database_url_schema) as conn:
        revoked = conn.execute(
            "SELECT count(*) FROM user_session WHERE revoked_at IS NOT NULL AND user_id = %s",
            (user_id,),
        ).fetchone()[0]
    assert revoked == 0

    applied = run_script("--username", username, "--apply", DATABASE_URL=database_url_schema)

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
    # 只撤活跃那条：过期会话本来就用不了，不动它（与 AUTH-05 登出只写 revoked_at 一致）。
    assert rows[live_token] is True
    assert rows[spent_token] is False
