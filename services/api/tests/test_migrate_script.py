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
    "007_idempotency_usage_semantics",
    "008_material_requirements",
)

#: 含空格与 ``$``：若脚本用 ``$(...)`` 直接拼命令行，这个值会被拆成多个参数或被展开。
TRICKY_URL = "postgresql://u:p$a ss@db.example:5432/huice"

_FAKE_SYSTEMCTL = """#!/bin/sh
# 按被问到的属性回答；没准备对应文件就返回空，效果与「unit 没配」一致。
case "$*" in
    *"-p EnvironmentFiles "*)
        if [ -f environment_files ]; then cat environment_files; fi
        exit 0
        ;;
    *"-p Environment "*)
        if [ -f environment_inline ]; then cat environment_inline; fi
        exit 0
        ;;
esac
# MainPID：有 mainpid 文件就用它，否则视为服务未运行。
if [ -f mainpid ]; then cat mainpid; else printf '0\\n'; fi
"""

#: 假的 ``systemd-run``。它不能只回放 ``env_value``：真 systemd 在把命令交给 ``ExecStart``
#: 之前会展开 ``${NAME}``，脚本里那个 ``$`` 有没有转义成 ``$$`` 直接决定取不取得到值。
#: 假件按同一条规则判：``$$`` 还原成字面 ``$`` 后交给 shell；出现未转义的 ``${...}`` 就
#: 当作被 systemd 展开掉，直接返回失败。
_FAKE_SYSTEMD_RUN = """#!/bin/sh
printf '%s\\n' "$*" >> systemd_run_log
if [ -f env_exit ]; then
    printf 'Failed to start transient service unit\\n' >&2
    exit "$(cat env_exit)"
fi
cmd=''
for arg in "$@"; do cmd=$arg; done
unescaped=$(printf '%s' "$cmd" | sed 's/\\$\\$//g')
case $unescaped in
    *'${'*)
        printf 'fake systemd-run: ExecStart 里出现未转义的 ${...}，systemd 会先把它展开\\n' >&2
        exit 97
        ;;
esac
cmd=$(printf '%s' "$cmd" | sed 's/\\$\\$/$/g')
exec env "DATABASE_URL=$(cat env_value 2>/dev/null)" /bin/sh -c "$cmd"
"""

_FAKE_PSQL = """#!/bin/sh
{
    for a in "$@"; do printf '[%s]\\n' "$a"; done
    printf '::END\\n'
} >> psql_log
case " $* " in
    *" to_regclass"*)
        if [ -f has_table ]; then cat has_table; else printf 'f\\n'; fi
        exit 0
        ;;
    *" FROM schema_migration"*)
        if [ -f schema_rows ]; then cat schema_rows; fi
        exit 0
        ;;
esac
if [ -f fail_on ] && printf '%s' "$*" | grep -q -- "$(cat fail_on)"; then exit 1; fi
# 跑通一份迁移就把它的 id 登记进 schema_migration——真库是这样，假库也得是这样，
# 否则「空库跑完 7 份、验收查询却查不到」会被当成脚本的 bug。
prev=''
id=''
for arg in "$@"; do
    if [ "$prev" = "-f" ]; then id=$(basename "$arg" .sql); fi
    prev=$arg
done
if [ -n "$id" ]; then
    # silent_ids：演「迁移跑完却没登记自己」的文件（漏了 INSERT INTO schema_migration）。
    # 它照样退出 0，但库里查不到——验收查询就是为这种文件准备的。
    if [ -f silent_ids ] && grep -qx -- "$id" silent_ids; then exit 0; fi
    { cat schema_rows 2>/dev/null; printf '%s\\n' "$id"; } > schema_rows.new
    mv schema_rows.new schema_rows
    printf 't\\n' > has_table
fi
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
        # 默认演「空库」：还没有 schema_migration 表，清单里的迁移一条都没登记。
        # 要演「已推进的库」自己写 has_table/schema_rows，见下面的用例。
        self.file("has_table", "f\n")
        self.file("schema_rows", "")
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
    """读 ``schema_migration`` 的那些调用：脚本开头读一次已登记、收尾再读一次做验收。

    不能按「参数里含 schema_migration」来筛：开头用 ``to_regclass`` 探表是否存在，
    那条也算 psql 调用，但它是探测不是读表。
    """

    return [
        call
        for call in sandbox.psql_calls()
        if any("FROM schema_migration" in arg for arg in call)
    ]


def _migration_calls(sandbox: Sandbox) -> list[list[str]]:
    """真正执行 ``.sql`` 的那些调用，按发生顺序。"""

    return [call for call in sandbox.psql_calls() if any(arg.endswith(".sql") for arg in call)]


def _applied_ids(*applied: str) -> dict[str, str]:
    """演一个「已推进到某些迁移」的库。"""

    return {"has_table": "t\n", "schema_rows": "\n".join(applied) + "\n" if applied else ""}


def _script_migrations() -> list[str]:
    """从脚本的 ``MIGRATIONS=(...)`` 里取出顺序清单（它才是实际执行的列表）。"""

    text = SCRIPT.read_text(encoding="utf-8")
    block = text.split("MIGRATIONS=(", 1)[1].split("\n)", 1)[0]
    return [line.strip() for line in block.splitlines() if line.strip()]


def test_every_shipped_sql_file_is_applied() -> None:
    applied = _script_migrations()

    # 这个常量是上面所有用例的期望值；它与脚本的清单必须逐项一致，否则测的就不是脚本。
    assert applied == list(MIGRATION_IDS)

    # 子集方向：脚本的清单可以比当前分支多（`007` 由 PR #34 新增，两支合并有先后），
    # 但 `infra/sql/` 里有的文件脚本必须都执行——漏一份就是「手册说跑了、实际没跑」，
    # 而这种漏项不会在服务启动时报错，只在后来写某张表时 500。
    shipped = {path.stem for path in (REPO_ROOT / "infra" / "sql").glob("*.sql")}
    assert shipped - set(applied) == set()


def test_a_missing_value_stops_before_any_psql(sandbox: Sandbox) -> None:
    sandbox.file("env_exit", "1")

    result = sandbox.run()

    assert result.returncode != 0
    assert "DATABASE_URL" in result.stderr
    # 只报一次：read_env_var 已经说清原因，外层再 die 一次会让同一句话重复
    assert result.stderr.count("读不到 DATABASE_URL") == 1
    assert sandbox.psql_calls() == []


def test_an_empty_value_stops_before_any_psql(sandbox: Sandbox) -> None:
    # systemd-run 成功返回、但值就是空的：空连接串会让 psql 退回默认参数
    sandbox.file("env_value", "")

    result = sandbox.run()

    assert result.returncode != 0
    assert "为空" in result.stderr
    assert sandbox.psql_calls() == []


def test_a_failed_systemd_run_shows_its_exit_code_and_stderr(sandbox: Sandbox) -> None:
    """服务未运行时唯一取值路径失败：要说清退出码与 stderr，而不是只报「读不到」。"""

    sandbox.file("env_exit", "3")

    result = sandbox.run()

    assert result.returncode != 0
    assert "3" in result.stderr
    assert "Failed to start transient service unit" in result.stderr
    # 诊断说一遍就够：内层已说明原因，外层不得再复述同一句话
    assert result.stderr.count("读不到 DATABASE_URL") == 1
    assert sandbox.psql_calls() == []


def test_an_empty_value_points_at_the_units_environment_file(sandbox: Sandbox) -> None:
    """unit 读的是别的文件，却按手册里的路径去取：空值不是「没配」，而是读错了地方。"""

    sandbox.file("env_value", "")
    sandbox.file("environment_files", "/etc/huice/other.env (ignore_errors=no)\n")

    result = sandbox.run(HUICE_ENV_FILE="/etc/huice/api.env")

    assert result.returncode != 0
    assert "/etc/huice/other.env" in result.stderr
    assert "/etc/huice/api.env" in result.stderr
    assert "为空" in result.stderr
    assert sandbox.psql_calls() == []


def test_an_empty_value_with_the_right_file_asks_about_the_file_format(sandbox: Sandbox) -> None:
    # 路径对得上，值仍为空：最可能是 systemd 不认的写法（export、等号两侧空格、CRLF）
    sandbox.file("env_value", "")
    sandbox.file("environment_files", "-/etc/huice/api.env (ignore_errors=yes)\n")

    result = sandbox.run(HUICE_ENV_FILE="/etc/huice/api.env")

    assert result.returncode != 0
    assert "路径没错" in result.stderr
    assert "KEY=value" in result.stderr
    assert sandbox.psql_calls() == []


def test_an_empty_value_when_the_unit_uses_inline_environment(sandbox: Sandbox) -> None:
    sandbox.file("env_value", "")
    sandbox.file("environment_inline", "DATABASE_URL=postgresql://never-used\n")

    result = sandbox.run()

    assert result.returncode != 0
    assert "Environment=" in result.stderr
    # 只报「用了 Environment=」，绝不把变量值打出来
    assert "never-used" not in result.stderr
    assert sandbox.psql_calls() == []


def test_every_migration_runs_then_the_verification_query(sandbox: Sandbox) -> None:
    # 空库：没有 schema_migration 表，所以清单里的迁移要按顺序全部执行
    sandbox.file("env_value", TRICKY_URL)

    result = sandbox.run()

    assert result.returncode == 0, result.stderr
    calls = _migration_calls(sandbox)
    assert len(calls) == len(MIGRATION_IDS)
    for call, expected in zip(calls, MIGRATION_IDS):
        # 值一律作为一个完整参数交给 psql，不被空格或 $ 拆开
        assert call[0] == TRICKY_URL
        assert "-v" in call and "ON_ERROR_STOP=1" in call
        assert f"sql/{expected}.sql" in call
    applied = _applied_migrations(sandbox)
    assert len(applied) == 1 and "schema_migration" in " ".join(applied[0])
    # 顺序也重要：验收查询必须在最后一次迁移之后
    assert calls[-1][-1].endswith(f"{MIGRATION_IDS[-1]}.sql")


def test_an_advanced_database_is_not_replayed_from_001(sandbox: Sandbox) -> None:
    """库已到最新：一条都不重放，只跑验收查询。

    这是 2026-11-06 云端那次失败的回归：库在 008 时从 001 重放，005 会把事实字典约束
    收回 16 键、被已有行顶掉而报错，于是「有新序号就跑脚本」推不动任何东西。
    """

    sandbox.file("env_value", TRICKY_URL)
    for name, content in _applied_ids(*MIGRATION_IDS).items():
        sandbox.file(name, content)

    result = sandbox.run()

    assert result.returncode == 0, result.stderr
    assert _migration_calls(sandbox) == []
    # 开头读一次已登记（用来跳过）、收尾读一次做验收；没有别的读表
    assert len(_applied_migrations(sandbox)) == 2
    assert "已登记，跳过 001_init" in result.stderr


def test_a_partially_applied_database_runs_only_the_rest(sandbox: Sandbox) -> None:
    sandbox.file("env_value", TRICKY_URL)
    applied = MIGRATION_IDS[:3]
    for name, content in _applied_ids(*applied).items():
        sandbox.file(name, content)

    result = sandbox.run()

    assert result.returncode == 0, result.stderr
    calls = _migration_calls(sandbox)
    assert [call[-1].rsplit("/", 1)[-1] for call in calls] == [
        f"{name}.sql" for name in MIGRATION_IDS[3:]
    ]


def test_a_database_ahead_of_the_code_is_reported_but_does_not_stop(sandbox: Sandbox) -> None:
    # 库里有本脚本清单之外的迁移（云端 008 早于代码就是这种情况）：点名即可，不算失败
    sandbox.file("env_value", TRICKY_URL)
    ahead = (*MIGRATION_IDS, "008_material_requirements")
    for name, content in _applied_ids(*ahead).items():
        sandbox.file(name, content)

    result = sandbox.run()

    assert result.returncode == 0, result.stderr
    assert "008_material_requirements" in result.stderr
    assert _migration_calls(sandbox) == []


def test_the_value_is_read_by_the_shell_not_by_systemd(sandbox: Sandbox) -> None:
    """`${DATABASE_URL-}` 要先用 `$$` 转义再交给 systemd。

    systemd 在把命令交给 `ExecStart` 之前会展开 `${NAME}`；`DATABASE_URL-` 带减号、
    不是合法变量名，会被当成未知变量换成空串，于是 `/bin/sh` 收到的是
    `printf '%s' ""`——退出码 0、输出为空，看着像「变量没配」。2026-11-06 云端实测：
    未转义 0 字节，写成 `$$` 后 54 字节。
    """

    sandbox.file("env_value", TRICKY_URL)

    result = sandbox.run()

    assert result.returncode == 0, result.stderr
    logged = " ".join(sandbox.lines("systemd_run_log"))
    assert "$${DATABASE_URL-}" in logged


def test_a_failed_migration_stops_the_rest_and_the_verification(sandbox: Sandbox) -> None:
    sandbox.file("env_value", TRICKY_URL)
    sandbox.file("fail_on", "003_trace_step_details")

    result = sandbox.run()

    assert result.returncode != 0
    assert "003_trace_step_details" in result.stderr
    calls = _migration_calls(sandbox)
    assert len(calls) == 3  # 001、002 与失败的那次；004 起一次都没跑
    assert _applied_migrations(sandbox) == []


@pytest.mark.parametrize("missing", MIGRATION_IDS)
def test_the_verification_fails_when_a_migration_is_not_recorded(
    sandbox: Sandbox, missing: str
) -> None:
    # 那一条的文件漏了 INSERT INTO schema_migration：psql 退出 0，库里却没有它。
    # 脚本会去跑它（因为开头读不到），跑完仍查不到——验收查询必须自己发现。
    # 对每个 id 都演一遍，避免只盯住最后一条、新增迁移后验收却没人测。
    sandbox.file("env_value", TRICKY_URL)
    sandbox.file("has_table", "t\n")
    sandbox.file(
        "schema_rows", "\n".join(mid for mid in MIGRATION_IDS if mid != missing) + "\n"
    )
    sandbox.file("silent_ids", f"{missing}\n")

    result = sandbox.run()

    assert result.returncode != 0
    assert missing in result.stderr
    assert _applied_migrations(sandbox)  # 迁移都跑完了，拦下的是验收查询


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
        calls = _migration_calls(sandbox)
        assert len(calls) == len(MIGRATION_IDS)
        assert calls[0][0] == TRICKY_URL
    finally:
        child.terminate()
        child.wait(timeout=10)
