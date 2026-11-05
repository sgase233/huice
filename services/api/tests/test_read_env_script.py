"""``infra/deploy/read-env.py`` 的取值规则。

部署手册 §2.1 / §3.1 用它从 ``/etc/huice/api.env`` 里取单个变量，代替
``set -a; . /etc/huice/api.env``。两种读法的差别正是这些用例要盯住的地方：shell 会
展开 ``$VAR``、会执行反引号、会在值里有未配对引号时把后文解析歪；systemd 的
``EnvironmentFile`` 这些都不做。脚本取到的值与 systemd 实际传给服务进程的值不一致时，
迁移就会连到错的库——这种错误在小配置里看不出来，只表现为「连不上」。

脚本先复制到临时目录再执行：仓库路径里有中文，Windows 上给原生命令传路径时会被按
代码页重编码（与 ``test_enable_tls_script.py`` 同一原因）。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
DEPLOY_DIR = REPO_ROOT / "infra" / "deploy"


@pytest.fixture()
def script(tmp_path: Path) -> Path:
    target = tmp_path / "read-env.py"
    shutil.copyfile(DEPLOY_DIR / "read-env.py", target)
    return target


def _run(script: Path, env_text: str, name: str) -> subprocess.CompletedProcess:
    env_file = script.parent / "api.env"
    env_file.write_text(env_text, encoding="utf-8", newline="\n")
    return subprocess.run(
        [sys.executable, str(script), str(env_file), name],
        capture_output=True,
        # 脚本的报错是中文：Windows 上 subprocess 默认按 GBK 解码会炸，显式指定 UTF-8。
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )


def test_plain_value_is_trimmed_and_written_without_newline(script):
    result = _run(script, "DATABASE_URL=postgresql://u:p@h:5432/db   \n", "DATABASE_URL")

    assert result.returncode == 0, result.stderr
    # 结尾不能有换行：调用方用 $(...) 取值，多一个换行就进到 psql 的参数里。
    assert result.stdout == "postgresql://u:p@h:5432/db"


def test_value_may_contain_equals_signs_and_slashes(script):
    text = "DATABASE_URL=postgresql://u:p@h:5432/db?sslmode=require&x=1\n"

    result = _run(script, text, "DATABASE_URL")

    assert result.stdout == "postgresql://u:p@h:5432/db?sslmode=require&x=1"


def test_single_quotes_are_taken_literally(script):
    """``$VAR`` / 反引号在单引号里是普通字符——shell 的 source 会把它们展开或执行。"""

    text = "DEMO_SESSION_SECRET='a$HOME`id`b\\c'\n"

    result = _run(script, text, "DEMO_SESSION_SECRET")

    assert result.stdout == "a$HOME`id`b\\c"


def test_double_quotes_only_unescape_the_five_systemd_escapes(script):
    text = 'DEMO_SESSION_SECRET="a\\$b\\\\c\\"d\\qe"\n'

    result = _run(script, text, "DEMO_SESSION_SECRET")

    # \$ \\ \" 按 systemd 规则还原；\q 不是已知转义，原样保留，不悄悄吃掉反斜杠。
    assert result.stdout == 'a$b\\c"d\\qe'


def test_dollar_signs_are_never_expanded(script):
    text = 'DEMO_SESSION_SECRET="pre$HOME/post$(id)/`id`"\n'

    result = _run(script, text, "DEMO_SESSION_SECRET")

    assert result.stdout == "pre$HOME/post$(id)/`id`"


def test_comments_and_blank_lines_are_skipped(script):
    text = "\n# 整行注释\n; 另一种注释\nDATABASE_URL=postgresql://h/db\n"

    result = _run(script, text, "DATABASE_URL")

    assert result.stdout == "postgresql://h/db"


def test_leading_whitespace_before_the_name_is_ignored(script):
    result = _run(script, "   DATABASE_URL=postgresql://h/db\n", "DATABASE_URL")

    assert result.stdout == "postgresql://h/db"


def test_last_occurrence_wins_like_systemd(script):
    text = "DATABASE_URL=first\nDATABASE_URL=second\n"

    result = _run(script, text, "DATABASE_URL")

    assert result.stdout == "second"


def test_missing_variable_exits_two_without_printing_any_value(script):
    """找不到变量时只报变量名与路径，不能把整个文件的其它值漏到 stdout/stderr。"""

    text = "ADMIN_TOKEN=should-not-leak\n"

    result = _run(script, text, "DATABASE_URL")

    assert result.returncode == 2
    assert result.stdout == ""
    assert "DATABASE_URL" in result.stderr
    assert "should-not-leak" not in result.stdout
    assert "should-not-leak" not in result.stderr


def test_empty_value_is_treated_as_missing(script):
    """空连接串会让 psql 退回默认值、可能连到别的库，所以按「取不到」处理而不是返回空串。"""

    result = _run(script, "DATABASE_URL=\n", "DATABASE_URL")

    assert result.returncode == 2
    assert result.stdout == ""
    assert "DATABASE_URL" in result.stderr


def test_missing_file_exits_two(script):
    result = subprocess.run(
        [sys.executable, str(script), str(script.parent / "nope.env"), "DATABASE_URL"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )

    assert result.returncode == 2
    assert result.stdout == ""


def test_wrong_usage_exits_two(script):
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )

    assert result.returncode == 2
    assert "read-env.py" in result.stderr
