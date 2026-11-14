"""``infra/deploy/provision-test-accounts.py``：隔离环境合成账号的守卫（#59、PR #72）。

M2 的联调要「两个合成企业账号 + 一个审核员账号」，而口令只在生成时出现一次。这里断言
的是行为：缺变量只报变量名、凭证文件先于建账号检查、口令不进 stdout、重复跑不踢掉
已有账号、``--reset-passwords`` 才改口令。
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

from app import auth as auth_tools

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "infra" / "deploy" / "provision-test-accounts.py"

#: 指向关着的端口：用来证明「凭证文件检查在建库之前」。
UNREACHABLE = "postgresql://huice:huice@127.0.0.1:1/none"
PASSWORD_PATTERN = re.compile(r"password=(\S+)")


def run_script(*args: str, **env_overrides) -> subprocess.CompletedProcess:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"DATABASE_URL"}
    }
    env.update({key: value for key, value in env_overrides.items() if value is not None})
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )


def credential_lines(path: Path) -> list[dict[str, str]]:
    lines = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    parsed = []
    for line in lines:
        fields: dict[str, str] = {}
        for token in line.split():
            if "=" in token:
                key, _, value = token.partition("=")
                fields[key] = value
        parsed.append(fields)
    return parsed


def test_missing_database_url_names_the_variable_only(tmp_path: Path) -> None:
    result = run_script("--out", str(tmp_path / "creds.txt"), DATABASE_URL=None)

    assert result.returncode == 2
    assert "DATABASE_URL" in result.stderr
    assert "postgresql://" not in result.stderr


def test_a_non_postgres_url_is_rejected(tmp_path: Path) -> None:
    result = run_script("--out", str(tmp_path / "creds.txt"), DATABASE_URL="sqlite:///tmp/x.db")

    assert result.returncode == 2
    assert "DATABASE_URL" in result.stderr


def test_out_is_required() -> None:
    result = run_script(DATABASE_URL=None)

    assert result.returncode == 2
    assert "--out" in result.stderr


def test_an_existing_credential_file_is_refused_before_touching_the_database(
    tmp_path: Path,
) -> None:
    """先查文件再建账号：反过来会留下「账号建了、口令没落盘」的账号。"""
    out = tmp_path / "creds.txt"
    out.write_text("old\n", encoding="utf-8")

    result = run_script("--out", str(out), DATABASE_URL=UNREACHABLE)

    assert result.returncode == 4
    assert "已存在" in result.stderr
    # 没有去连库：连库失败会打印「建账号失败（ConnectionTimeout）」之类
    assert "建账号失败" not in result.stderr
    assert out.read_text(encoding="utf-8") == "old\n"


@pytest.mark.db
@pytest.mark.usefixtures("clean_tables")
def test_it_creates_one_reviewer_and_two_enterprises(database_url_schema, tmp_path: Path) -> None:
    out = tmp_path / "creds.txt"
    result = run_script("--out", str(out), DATABASE_URL=database_url_schema)

    assert result.returncode == 0, result.stderr
    entries = credential_lines(out)
    assert {entry["username"] for entry in entries} == {"reviewer.demo", "acme.alpha", "acme.beta"}
    assert {entry["role"] for entry in entries} == {"reviewer", "enterprise"}

    # 口令只进文件：stdout / stderr 里一个都不许出现
    passwords = [entry["password"] for entry in entries]
    assert len(set(passwords)) == 3
    for password in passwords:
        assert password not in result.stdout + result.stderr

    # 凭证文件必须是 0600（Windows 上 chmod 只影响只读位，权限断言放到 POSIX）
    if os.name == "posix":
        assert stat.S_IMODE(out.stat().st_mode) == 0o600

    with psycopg.connect(database_url_schema) as conn:
        users = conn.execute(
            "SELECT username, role, password_hash FROM app_user ORDER BY username"
        ).fetchall()
        owned = conn.execute(
            "SELECT u.username, e.name_synthetic FROM enterprise e "
            "JOIN app_user u ON u.id = e.owner_user_id ORDER BY u.username"
        ).fetchall()

    assert [(row[0], row[1]) for row in users] == [
        ("acme.alpha", "enterprise"),
        ("acme.beta", "enterprise"),
        ("reviewer.demo", "reviewer"),
    ]
    # 两个企业各归各的账号：越权用例要的就是这个形状
    assert owned == [("acme.alpha", "合成企业A"), ("acme.beta", "合成企业B")]
    # 文件里的口令确实能验过库里的哈希，不是随手编的
    by_username = {entry["username"]: entry["password"] for entry in entries}
    for username, _role, password_hash in users:
        assert auth_tools.verify_password(by_username[username], password_hash)


@pytest.mark.db
@pytest.mark.usefixtures("clean_tables")
def test_a_second_run_keeps_existing_passwords(database_url_schema, tmp_path: Path) -> None:
    """重跑不能把 M2 正在用的账号踢掉：已存在的账号跳过，口令不变。"""
    first_out = tmp_path / "first.txt"
    assert run_script("--out", str(first_out), DATABASE_URL=database_url_schema).returncode == 0

    with psycopg.connect(database_url_schema) as conn:
        before = dict(
            conn.execute("SELECT username, password_hash FROM app_user").fetchall()
        )

    second_out = tmp_path / "second.txt"
    second = run_script("--out", str(second_out), DATABASE_URL=database_url_schema)

    assert second.returncode == 0, second.stderr
    assert "已存在跳过 3 个" in second.stdout
    assert credential_lines(second_out) == []
    with psycopg.connect(database_url_schema) as conn:
        after = dict(
            conn.execute("SELECT username, password_hash FROM app_user").fetchall()
        )
    assert after == before


@pytest.mark.db
@pytest.mark.usefixtures("clean_tables")
def test_reset_passwords_rewrites_the_credentials(database_url_schema, tmp_path: Path) -> None:
    """口令丢了才用 ``--reset-passwords``：重发口令、重写文件，哈希随之改变。"""
    first_out = tmp_path / "first.txt"
    assert run_script("--out", str(first_out), DATABASE_URL=database_url_schema).returncode == 0

    with psycopg.connect(database_url_schema) as conn:
        before = dict(
            conn.execute("SELECT username, password_hash FROM app_user").fetchall()
        )

    reset_out = tmp_path / "reset.txt"
    reset = run_script(
        "--out", str(reset_out), "--reset-passwords", "--force", DATABASE_URL=database_url_schema
    )

    assert reset.returncode == 0, reset.stderr
    entries = credential_lines(reset_out)
    assert {entry["username"] for entry in entries} == {"reviewer.demo", "acme.alpha", "acme.beta"}
    assert all("口令已重置" in line for line in reset_out.read_text(encoding="utf-8").splitlines() if "username=" in line)

    with psycopg.connect(database_url_schema) as conn:
        after = dict(
            conn.execute("SELECT username, password_hash FROM app_user").fetchall()
        )
    assert set(after) == set(before)
    for username in after:
        assert after[username] != before[username]
        stored = after[username]
        assert auth_tools.verify_password(
            {entry["username"]: entry["password"] for entry in entries}[username], stored
        )
