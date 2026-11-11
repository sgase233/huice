"""``infra/deploy/create-admin.py``：保底审核员初始化的守卫（#61 review 第 5 条）。

脚本只从环境读口令，所以这里断言的是**行为**：缺变量 / 取值不合法时不动数据库、
只报变量名；用户名撞车时不覆盖既有口令。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "infra" / "deploy" / "create-admin.py"

PASSWORD = "correct-horse-battery-9"
DSN = "postgresql://huice:huice@127.0.0.1:5432/huice_test"


def run_script(**env_overrides) -> subprocess.CompletedProcess:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"DATABASE_URL", "ADMIN_INIT_USERNAME", "ADMIN_INIT_PASSWORD"}
    }
    env.update({key: value for key, value in env_overrides.items() if value is not None})
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


def test_missing_variables_are_named_but_never_printed():
    result = run_script(
        DATABASE_URL=None,
        ADMIN_INIT_USERNAME=None,
        ADMIN_INIT_PASSWORD=None,
    )

    assert result.returncode == 2
    assert "DATABASE_URL" in result.stderr
    assert "ADMIN_INIT_USERNAME" in result.stderr
    assert "ADMIN_INIT_PASSWORD" in result.stderr
    # 提示里只有变量名：不出现任何取值（这里连赋值号都不该有）。
    assert "ADMIN_INIT_PASSWORD=" not in result.stderr


def test_an_invalid_username_is_rejected_before_touching_the_database():
    result = run_script(
        DATABASE_URL=DSN,
        ADMIN_INIT_USERNAME="A",
        ADMIN_INIT_PASSWORD=PASSWORD,
    )

    assert result.returncode == 2
    assert "ADMIN_INIT_USERNAME" in result.stderr


def test_a_short_password_is_rejected():
    result = run_script(
        DATABASE_URL=DSN,
        ADMIN_INIT_USERNAME="admin.one",
        ADMIN_INIT_PASSWORD="short",
    )

    assert result.returncode == 2
    assert "ADMIN_INIT_PASSWORD" in result.stderr


def test_a_non_postgres_url_is_rejected():
    result = run_script(
        DATABASE_URL="sqlite:///tmp/x.db",
        ADMIN_INIT_USERNAME="admin.one",
        ADMIN_INIT_PASSWORD=PASSWORD,
    )

    assert result.returncode == 2
    assert "DATABASE_URL" in result.stderr


@pytest.mark.db
def test_it_creates_a_reviewer_and_refuses_to_overwrite(database_url_schema):
    first = run_script(
        DATABASE_URL=database_url_schema,
        ADMIN_INIT_USERNAME="  Admin.Two ",
        ADMIN_INIT_PASSWORD=PASSWORD,
    )

    assert first.returncode == 0, first.stderr
    # 只回显用户名，不回显口令或哈希。
    assert "admin.two" in first.stdout
    assert PASSWORD not in first.stdout + first.stderr

    with psycopg.connect(database_url_schema) as conn:
        row = conn.execute(
            "SELECT username, role, status FROM app_user WHERE username = 'admin.two'"
        ).fetchone()
    assert row == ("admin.two", "reviewer", "active")

    again = run_script(
        DATABASE_URL=database_url_schema,
        ADMIN_INIT_USERNAME="admin.two",
        ADMIN_INIT_PASSWORD="another-password-9",
    )

    assert again.returncode == 4
    assert "已存在" in again.stderr
    with psycopg.connect(database_url_schema) as conn:
        stored = conn.execute(
            "SELECT password_hash FROM app_user WHERE username = 'admin.two'"
        ).fetchone()[0]
    assert stored != "another-password-9"
