"""部署手册里的 ``systemd-run`` 示例必须能在真实 systemd 上跑通。

``systemd-run`` 创建的临时单元**不继承调用 shell 的工作目录**（没有 ``WorkingDirectory=``
时从 ``/`` 启动）。手册曾先 ``cd /opt/huice/app`` 再用相对路径
``infra/deploy/issue-session.py`` 调用签发脚本——照着抄会得到「找不到文件」，而且临时单元
没有任何输出，看起来像卡住（M2 在 PR #35 的 P1）。

这里不执行命令，只对 ``infra/deploy/README.md`` 的代码块做结构检查：临时单元里调用项目
脚本时用绝对路径、显式给工作目录、不靠 ``cd`` 兜底。命令本身能否跑通仍要在真实 systemd
上验，本用例只挡住「照抄一定失败」的那类写法。

§6.2 同理：回滚的命令放在 ``infra/deploy/rollback.sh``，行为由
``test_rollback_script.py`` 真跑 shell 覆盖；这里只检查手册没有又手写一套命令。
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DEPLOY_README = REPO_ROOT / "infra" / "deploy" / "README.md"

_FENCE = re.compile(r"```(?:bash|sh)?\n(.*?)```", re.S)
#: 临时单元里直接跑项目脚本的写法：解释器 + 脚本路径。
_PROJECT_PYTHON = re.compile(r"(?:/opt/huice/venv/bin/python[0-9.]*)\s+(\S+)")


def _systemd_run_blocks() -> list[str]:
    text = DEPLOY_README.read_text(encoding="utf-8")
    return [block for block in _FENCE.findall(text) if "systemd-run" in block]


def test_the_readme_actually_has_systemd_run_examples():
    """前提断言：手册里还有临时单元示例，否则下面的用例会空转全绿。"""

    blocks = _systemd_run_blocks()
    assert blocks, "infra/deploy/README.md 里找不到 systemd-run 示例"


def test_every_project_script_in_a_transient_unit_is_an_absolute_path():
    for block in _systemd_run_blocks():
        for script in _PROJECT_PYTHON.findall(block):
            assert script.startswith("/"), (
                f"临时单元里用了相对脚本路径 {script!r}：systemd-run 不继承 shell 的 cwd，"
                "照抄会找不到文件。\n"
                f"{block}"
            )


def test_a_transient_unit_running_project_code_pins_the_working_directory():
    """跑项目脚本时要显式 ``WorkingDirectory=/opt/huice/app``，与应用单元一致。"""

    for block in _systemd_run_blocks():
        if not _PROJECT_PYTHON.search(block):
            continue
        assert "WorkingDirectory=/opt/huice/app" in block, (
            "临时单元没有钉住工作目录：相对路径（如 LOCAL_OBJECT_DIR 的默认值 "
            "`.local-objects`）会落到 `/`。\n"
            f"{block}"
        )


#: 取值失败时手册必须给出的诊断线索：unit 实际读哪个文件、systemd 认了哪些变量、文件写法是否合法。
#: 这几项都只涉及路径与变量名，不含任何值。
_EMPTY_VALUE_MARKERS = (
    "systemctl show -p EnvironmentFiles huice-api",
    "退出码",
    "stderr",
    "KEY=value",
    "export",
    "CRLF",
)


def test_the_manual_says_what_to_check_when_the_value_comes_back_empty():
    """「读不到」不能停在猜测上：手册要给出可执行的下一步，而不是再试一次。"""

    text = DEPLOY_README.read_text(encoding="utf-8")
    section = text.split("### 2.1 ", 1)[1].split("\n## ", 1)[0]
    missing = [marker for marker in _EMPTY_VALUE_MARKERS if marker not in section]
    assert not missing, f"§2.1 缺少取值失败时的诊断线索：{missing}"


def test_no_example_relies_on_cd_before_systemd_run():
    """``cd`` 只改调用 shell 的 cwd，对临时单元无效——留着它会给人错误的安全感。"""

    for block in _systemd_run_blocks():
        assert not re.search(r"^\s*cd\s", block, re.M), (
            "systemd-run 示例里不要靠 cd 兜底：临时单元从 `/` 启动，cd 对它不起作用。\n"
            f"{block}"
        )

# ------------------------------------------------------------- §6.2 回滚契约


def _section(text: str, title: str, next_title: str) -> str:
    assert title in text, f"手册里找不到 {title}"
    return text.split(title, 1)[1].split(next_title, 1)[0]


def _rollback_section() -> str:
    return _section(DEPLOY_README.read_text(encoding="utf-8"), "### 6.2 ", "\n## ")


def test_every_command_in_the_rollback_section_is_the_tested_script():
    """回滚不再手写「comm 有输出就停下」这类命令：判据得由命令自己返回非零。

    ``comm`` 有差异时退出码仍是 0、``cp -an`` 遇到同名文件会静默跳过——这两条都不是
    写在文档里就会生效的（M2 在 #44 的复审 P1）。命令改由 ``infra/deploy/rollback.sh``
    执行，行为由 ``test_rollback_script.py`` 在临时目录里真跑 shell 覆盖。
    """

    blocks = _FENCE.findall(_rollback_section())
    assert blocks, "§6.2 里没有可执行的示例"
    for block in blocks:
        assert "rollback.sh" in block, f"§6.2 里还有手写命令：\n{block}"


def test_the_rollback_section_states_the_exit_codes():
    section = _rollback_section()
    assert "退出码" in section
    for code in ("**0**", "**2**", "**3**", "**4**", "**5**"):
        assert code in section, f"§6.2 没写退出码 {code} 的含义"


def test_the_update_section_points_back_to_the_rollback():
    update = _section(DEPLOY_README.read_text(encoding="utf-8"), "### 6.1 ", "### 6.2 ")
    assert "§6.2" in update, "§6.1 没有告诉人回滚按哪一节做"
    assert "app.old-$X" in update, "§6.1 的旧目录名与 §6.2 的对不上"
