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
from uuid import uuid4

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

    original = first_out.read_text(encoding="utf-8")

    second_out = tmp_path / "second.txt"
    second = run_script("--out", str(second_out), DATABASE_URL=database_url_schema)

    assert second.returncode == 0, second.stderr
    assert "本轮没有新口令" in second.stdout
    # 没有任何新口令就不该生成（更没有权利覆盖）一份只有标题的凭证文件
    assert not second_out.exists()
    assert first_out.read_text(encoding="utf-8") == original
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


def _seed_user(dsn: str, username: str, *, role: str, status: str = "active", enterprise=None):
    """造一个同名但身份不符的账号，模拟「这台环境上已经有人占了这个名字」。"""

    user_id = uuid4()
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "INSERT INTO app_user (id, username, password_hash, role, status) "
            "VALUES (%s, %s, %s, %s, %s)",
            (user_id, username, "x" * 16, role, status),
        )
        if enterprise is not None:
            conn.execute(
                "INSERT INTO enterprise (id, name_synthetic, period_start, period_end, owner_user_id) "
                "VALUES (%s, %s, '2026-01-01', '2026-12-31', %s)",
                (uuid4(), enterprise, user_id),
            )
        conn.commit()
    return user_id


@pytest.mark.db
@pytest.mark.usefixtures("clean_tables")
def test_force_without_new_credentials_leaves_the_only_file_untouched(
    database_url_schema, tmp_path: Path
) -> None:
    """全部跳过时 `--force` 也不能把唯一一份还在用的口令文件清空。"""

    out = tmp_path / "creds.txt"
    assert run_script("--out", str(out), DATABASE_URL=database_url_schema).returncode == 0
    original = out.read_text(encoding="utf-8")
    assert len(credential_lines(out)) == 3

    result = run_script("--out", str(out), "--force", DATABASE_URL=database_url_schema)

    assert result.returncode == 0, result.stderr
    assert out.read_text(encoding="utf-8") == original


@pytest.mark.db
@pytest.mark.usefixtures("clean_tables")
def test_a_same_name_account_with_another_role_stops_the_whole_run(
    database_url_schema, tmp_path: Path
) -> None:
    """同名但角色不同：整批停下（退出码 5），其余账号一个也不建。"""

    _seed_user(database_url_schema, "reviewer.demo", role="enterprise")
    out = tmp_path / "creds.txt"

    result = run_script("--out", str(out), DATABASE_URL=database_url_schema)

    assert result.returncode == 5
    assert "reviewer.demo" in result.stderr
    assert not out.exists()
    with psycopg.connect(database_url_schema) as conn:
        count = conn.execute("SELECT count(*) FROM app_user").fetchone()[0]
    assert count == 1


@pytest.mark.db
@pytest.mark.usefixtures("clean_tables")
def test_a_disabled_account_stops_the_run(database_url_schema, tmp_path: Path) -> None:
    _seed_user(database_url_schema, "acme.alpha", role="enterprise", status="disabled")

    result = run_script(
        "--out", str(tmp_path / "creds.txt"), DATABASE_URL=database_url_schema
    )

    assert result.returncode == 5
    assert "acme.alpha" in result.stderr
    assert "disabled" in result.stderr


@pytest.mark.db
@pytest.mark.usefixtures("clean_tables")
def test_an_enterprise_with_another_name_stops_the_run(database_url_schema, tmp_path: Path) -> None:
    """同名同角色但企业归属不对：可能是真实客户账号，重置口令会踢掉它。"""

    _seed_user(
        database_url_schema, "acme.beta", role="enterprise", enterprise="某个真实企业"
    )

    result = run_script(
        "--out", str(tmp_path / "creds.txt"), DATABASE_URL=database_url_schema
    )

    assert result.returncode == 5
    assert "acme.beta" in result.stderr


@pytest.mark.db
@pytest.mark.usefixtures("clean_tables")
def test_a_same_name_reviewer_carrying_an_enterprise_stops_the_run(
    database_url_schema, tmp_path: Path
) -> None:
    _seed_user(
        database_url_schema, "reviewer.demo", role="reviewer", enterprise="不该有的企业"
    )

    result = run_script(
        "--out", str(tmp_path / "creds.txt"), DATABASE_URL=database_url_schema
    )

    assert result.returncode == 5
    assert "reviewer.demo" in result.stderr


@pytest.mark.skipif(os.name != "posix", reason="符号链接语义只在 POSIX 上可测")
def test_a_symlinked_out_path_is_refused(tmp_path: Path) -> None:
    real = tmp_path / "real.txt"
    real.write_text("keep", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(real)

    result = run_script("--out", str(link), "--force", DATABASE_URL=UNREACHABLE)

    assert result.returncode == 4
    assert "符号链接" in result.stderr
    assert real.read_text(encoding="utf-8") == "keep"


def _load_script():
    import importlib.util

    spec = importlib.util.spec_from_file_location("provision_test_accounts", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("provision_test_accounts", module)
    spec.loader.exec_module(module)
    return module


provision = _load_script()


class FakeAccounts:
    """只够跑 ``_provision`` 的替身：能模拟提交失败与并发撞名。"""

    def __init__(self, *, failure=None, taken=False) -> None:
        self.failure = failure
        self.taken = taken
        self.created: list[str] = []

    def find_user_by_username(self, username):
        return {"id": "00000000-0000-0000-0000-000000000000", "username": username,
                "role": "enterprise", "status": "active", "password_hash": "x"}

    def enterprise_name_for_user(self, user_id):
        return "合成企业A"

    def register(self, *, username, password_hash, role, profile):
        if self.taken:
            from app.errors import ApiProblem

            raise ApiProblem(409, "USERNAME_TAKEN", "该用户名已被占用")
        if self.failure is not None:
            raise self.failure
        self.created.append(username)

    def update_password(self, *, user_id, password_hash):
        if self.failure is not None:
            raise self.failure


def test_the_password_is_on_disk_before_the_database_commit(tmp_path: Path) -> None:
    """写前日志：提交失败时，生成了但没提交的口令仍在文件里（标「待提交」）。"""

    path = tmp_path / "creds.txt"
    entries: dict = {}
    items = [provision.PlanItem(provision.SPECS[1], "create")]

    with pytest.raises(RuntimeError):
        provision._provision(
            FakeAccounts(failure=RuntimeError("db down")), items, entries, [], path, create_only=True
        )

    text = path.read_text(encoding="utf-8")
    assert "username=acme.alpha" in text
    assert provision.STATUS_PENDING in text


def test_a_concurrent_username_take_stops_instead_of_reporting_success(tmp_path: Path) -> None:
    """并发撞名：拿不到对方的口令，就不能在文件里留下一条「成功」记录。"""

    path = tmp_path / "creds.txt"
    entries: dict = {}
    items = [provision.PlanItem(provision.SPECS[1], "create")]

    with pytest.raises(provision.ConcurrentConflict):
        provision._provision(
            FakeAccounts(taken=True), items, entries, [], path, create_only=True
        )

    text = path.read_text(encoding="utf-8") if path.exists() else ""
    assert "username=acme.alpha" not in text


def test_reconcile_drops_rows_whose_password_no_longer_matches(tmp_path: Path) -> None:
    """账号没建出来、或口令已被改过：那一行作废，不能当交付物。"""

    from app import auth as auth_tools

    good = auth_tools.hash_password("good-password-123")
    other = auth_tools.hash_password("other-password-123")

    class ReconcileFake(FakeAccounts):
        def find_user_by_username(self, username):
            if username == "reviewer.demo":
                return {"id": "1", "username": username, "role": "reviewer",
                        "status": "active", "password_hash": good}
            if username == "acme.alpha":
                return {"id": "2", "username": username, "role": "enterprise",
                        "status": "active", "password_hash": other}
            return None

    entries = {
        "reviewer.demo": provision.CredentialEntry("reviewer.demo", "reviewer", "good-password-123", None, "待提交"),
        "acme.alpha": provision.CredentialEntry("acme.alpha", "enterprise", "stale-password-123", "合成企业A", "待提交"),
        "acme.beta": provision.CredentialEntry("acme.beta", "enterprise", "never-created-123", "合成企业B", "待提交"),
    }

    dropped = provision._reconcile(ReconcileFake(), entries)

    assert set(dropped) == {"acme.alpha", "acme.beta"}
    assert list(entries) == ["reviewer.demo"]
    assert entries["reviewer.demo"].status == "已确认"


@pytest.mark.db
@pytest.mark.usefixtures("clean_tables")
def test_a_partial_existing_file_keeps_earlier_passwords(
    database_url_schema, tmp_path: Path
) -> None:
    """文件里已有两个账号、第三个还没建：重跑只补第三个，前两个口令原样保留。"""

    out = tmp_path / "creds.txt"
    assert run_script("--out", str(out), DATABASE_URL=database_url_schema).returncode == 0
    before = {entry["username"]: entry["password"] for entry in credential_lines(out)}
    assert len(before) == 3

    with psycopg.connect(database_url_schema) as conn:
        beta_id = conn.execute(
            "SELECT id FROM app_user WHERE username = 'acme.beta'"
        ).fetchone()[0]
        # FK 是 ON DELETE RESTRICT：先删企业再删账号
        conn.execute("DELETE FROM enterprise WHERE owner_user_id = %s", (beta_id,))
        conn.execute("DELETE FROM app_user WHERE id = %s", (beta_id,))
        conn.commit()

    result = run_script("--out", str(out), "--force", DATABASE_URL=database_url_schema)

    assert result.returncode == 0, result.stderr
    after = {entry["username"]: entry["password"] for entry in credential_lines(out)}
    assert set(after) == {"reviewer.demo", "acme.alpha", "acme.beta"}
    assert after["reviewer.demo"] == before["reviewer.demo"]
    assert after["acme.alpha"] == before["acme.alpha"]
    assert after["acme.beta"] != before["acme.beta"]
