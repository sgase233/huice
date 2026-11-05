"""``infra/deploy/migrate.sh`` 的守卫：取不到值、取到空值、迁移失败时，一次 ``psql`` 也不该跑。

脚本不解释 ``EnvironmentFile``：服务在跑就读 ``/proc/<MainPID>/environ``，没在跑就让
``systemd-run`` 用 systemd 自己的解析读一次。这里用假的 ``systemctl`` / ``systemd-run`` /
``psql`` 覆盖两条取值路径与失败分支，断言的是**行为**（跑没跑 psql、跑了几次、参数是什么），
不是脚本内部的实现细节。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "infra" / "deploy" / "migrate.sh"

MIGRATION_IDS = (
    "001_init",
    "002_evaluation_and_idempotency",
    "003_trace_step_details",
    "004_invalidation_event_period",
    "005_fact_dictionary_extension",
    "006_fact_dictionary_extension_rc4",
)

#: 含空格与 ``$``：若脚本用 ``$(...)`` 直接拼命令行，这个值会被拆成多个参数或被展开。
TRICKY_URL = "postgresql://u:p$a ss@db.example:5432/huice"

_FAKE_SYSTEMCTL = """#!/bin/sh
# 只回答 MainPID：有 mainpid 文件就用它，否则视为服务未运行。
if [ -f mainpid ]; then cat mainpid; else printf '0\\n'; fi
"""

_FAKE_SYSTEMD_RUN = """#!/bin/sh
printf '%s\\n' "$*" >> systemd_run_log
if [ -f env_exit ]; then exit "$(cat env_exit)"; fi
if [ -f env_value ]; then printf '%s' "$(cat env_value)"; fi
exit 0
"""

_FAKE_PSQL = """#!/bin/sh
{
    for a in "$@"; do printf '[%s]\\n' "$a"; done
    printf '::END\\n'
} >> psql_log
case " $* " in
    *" SELECT "*)
        if [ -f schema_rows ]; then cat schema_rows; fi
        exit 0
        ;;
esac
if [ -f fail_on ] && printf '%s' "$*" | grep -q -- "$(cat fail_on)"; then exit 1; fi
exit 0
"""

BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="执行部署脚本需要 bash")


class Sandbox:
    """一套假的 ``systemctl`` / ``systemd-run`` / ``psql``，状态用文件喂。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        for name in ("bin", "sql"):
            (root / name).mkdir()
        self._write_tool("systemctl", _FAKE_SYSTEMCTL)
        self._write_tool("systemd-run", _FAKE_SYSTEMD_RUN)
        self._write_tool("psql", _FAKE_PSQL)
        # 假的 psql 不读文件内容，但路径要像真的
        for name in MIGRATION_IDS:
            (root / "sql" / f"{name}.sql").write_bytes(b"-- fixture\n")
        # 默认「库里六个 id 都在」；要演缺 id 的用例自己覆盖这个文件。
        # 这里必须给默认值：漏写的用例会让验收查询返回空、六项全报缺失。
        self.file("schema_rows", "\n".join(MIGRATION_IDS) + "\n")
        subprocess.run(
            [BASH, "-c", "chmod +x bin/systemctl bin/systemd-run bin/psql"],
            cwd=root,
            check=True,
        )

    def _write_tool(self, name: str, body: str) -> None:
        # write_bytes：不让 Windows 把 \n 转成 \r\n（CRLF 的 shebang 在 bash 里跑不起来）
        (self.root / "bin" / name).write_bytes(body.encode())

    def file(self, name: str, content: str) -> None:
        (self.root / name).write_bytes(content.encode())

    def lines(self, name: str) -> list[str]:
        path = self.root / name
        if not path.exists():
            return []
        return path.read_bytes().decode().splitlines()

    def psql_calls(self) -> list[list[str]]:
        """把 ``[arg]`` 记录还原成一次次调用。"""

        calls: list[list[str]] = []
        current: list[str] = []
        for line in self.lines("psql_log"):
            if line == "::END":
                calls.append(current)
                current = []
            elif line.startswith("[") and line.endswith("]"):
                current.append(line[1:-1])
        return calls

    def run(self, **env: str) -> subprocess.CompletedProcess[str]:
        environment = dict(os.environ)
        environment.pop("DATABASE_URL", None)
        environment.update(
            {
                "HUICE_SERVICE": "huice-api",
                "HUICE_ENV_FILE": "api.env",
                "HUICE_SQL_DIR": "sql",
                "HUICE_SYSTEMCTL": "bin/systemctl",
                "HUICE_SYSTEMD_RUN": "bin/systemd-run",
                "HUICE_PSQL": "bin/psql",
            }
        )
        environment.update(env)
        return subprocess.run(
            [BASH, str(SCRIPT)],
            cwd=self.root,
            env=environment,
            capture_output=True,
            # bash 输出是 UTF-8；不指定就会按 Windows 的 GBK 解码，中文报错直接变成解码异常
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )


@pytest.fixture()
def sandbox(tmp_path: Path) -> Sandbox:
    return Sandbox(tmp_path)


def _applied_migrations(sandbox: Sandbox) -> list[list[str]]:
    return [
        call
        for call in sandbox.psql_calls()
        if any("schema_migration" in arg for arg in call)
    ]


def test_a_missing_value_stops_before_any_psql(sandbox: Sandbox) -> None:
    sandbox.file("env_exit", "1")

    result = sandbox.run()

    assert result.returncode != 0
    assert "DATABASE_URL" in result.stderr
    assert sandbox.psql_calls() == []


def test_an_empty_value_stops_before_any_psql(sandbox: Sandbox) -> None:
    # systemd-run 成功返回、但值就是空的：空连接串会让 psql 退回默认参数
    sandbox.file("env_value", "")

    result = sandbox.run()

    assert result.returncode != 0
    assert "为空" in result.stderr
    assert sandbox.psql_calls() == []


def test_every_migration_runs_then_the_verification_query(sandbox: Sandbox) -> None:
    sandbox.file("env_value", TRICKY_URL)
    sandbox.file("schema_rows", "\n".join(MIGRATION_IDS) + "\n")

    result = sandbox.run()

    assert result.returncode == 0, result.stderr
    calls = sandbox.psql_calls()
    assert len(calls) == len(MIGRATION_IDS) + 1
    for call, expected in zip(calls, MIGRATION_IDS):
        # 值一律作为一个完整参数交给 psql，不被空格或 $ 拆开
        assert call[0] == TRICKY_URL
        assert "-v" in call and "ON_ERROR_STOP=1" in call
        assert f"sql/{expected}.sql" in call
    assert "schema_migration" in " ".join(calls[-1])
    # 顺序也重要：验收查询必须在最后一次迁移之后
    assert "sql/" in " ".join(calls[0])


def test_a_failed_migration_stops_the_rest_and_the_verification(sandbox: Sandbox) -> None:
    sandbox.file("env_value", TRICKY_URL)
    sandbox.file("fail_on", "003_trace_step_details")

    result = sandbox.run()

    assert result.returncode != 0
    assert "003_trace_step_details" in result.stderr
    calls = sandbox.psql_calls()
    assert len(calls) == 3  # 001、002 与失败的那次；004 起一次都没跑
    assert _applied_migrations(sandbox) == []


def test_the_verification_fails_when_a_migration_is_not_recorded(sandbox: Sandbox) -> None:
    # psql 全部成功，但库里少了 006（比如迁移脚本被跳过）：验收查询必须自己发现问题
    sandbox.file("env_value", TRICKY_URL)
    sandbox.file("schema_rows", "\n".join(MIGRATION_IDS[:-1]) + "\n")

    result = sandbox.run()

    assert result.returncode != 0
    assert "006_fact_dictionary_extension_rc4" in result.stderr


def test_a_running_service_is_read_from_its_own_environment(sandbox: Sandbox, tmp_path: Path) -> None:
    if not Path("/proc/self/environ").exists():
        pytest.skip("没有 /proc，无法模拟运行中服务的环境")

    # 一个真的在跑、环境里真有 DATABASE_URL 的进程
    child = subprocess.Popen(
        ["sleep", "30"],
        env={**os.environ, "DATABASE_URL": TRICKY_URL},
    )
    try:
        sandbox.file("mainpid", f"{child.pid}\n")
        sandbox.file("env_exit", "1")  # 走到 systemd-run 就失败：证明根本没用到它

        result = sandbox.run()

        assert result.returncode == 0, result.stderr
        assert sandbox.lines("systemd_run_log") == []
        calls = sandbox.psql_calls()
        assert len(calls) == len(MIGRATION_IDS) + 1
        assert calls[0][0] == TRICKY_URL
    finally:
        child.terminate()
        child.wait(timeout=10)
