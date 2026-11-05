"""部署手册里的 ``systemd-run`` 示例必须能在真实 systemd 上跑通。

``systemd-run`` 创建的临时单元**不继承调用 shell 的工作目录**（没有 ``WorkingDirectory=``
时从 ``/`` 启动）。手册曾先 ``cd /opt/huice/app`` 再用相对路径
``infra/deploy/issue-session.py`` 调用签发脚本——照着抄会得到「找不到文件」，而且临时单元
没有任何输出，看起来像卡住（M2 在 PR #35 的 P1）。

这里不执行命令，只对 ``infra/deploy/README.md`` 的代码块做结构检查：临时单元里调用项目
脚本时用绝对路径、显式给工作目录、不靠 ``cd`` 兜底。命令本身能否跑通仍要在真实 systemd
上验，本用例只挡住「照抄一定失败」的那类写法。
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


def test_no_example_relies_on_cd_before_systemd_run():
    """``cd`` 只改调用 shell 的 cwd，对临时单元无效——留着它会给人错误的安全感。"""

    for block in _systemd_run_blocks():
        assert not re.search(r"^\s*cd\s", block, re.M), (
            "systemd-run 示例里不要靠 cd 兜底：临时单元从 `/` 启动，cd 对它不起作用。\n"
            f"{block}"
        )
