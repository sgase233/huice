"""部署手册里的 ``systemd-run`` 示例必须能在真实 systemd 上跑通。

``systemd-run`` 创建的临时单元**不继承调用 shell 的工作目录**（没有 ``WorkingDirectory=``
时从 ``/`` 启动）。手册曾先 ``cd /opt/huice/app`` 再用相对路径
``infra/deploy/issue-session.py`` 调用签发脚本——照着抄会得到「找不到文件」，而且临时单元
没有任何输出，看起来像卡住（M2 在 PR #35 的 P1）。

这里不执行命令，只对 ``infra/deploy/README.md`` 的代码块做结构检查：临时单元里调用项目
脚本时用绝对路径、显式给工作目录、不靠 ``cd`` 兜底。命令本身能否跑通仍要在真实 systemd
上验，本用例只挡住「照抄一定失败」的那类写法。

§6.2 的回滚用例同理：只检查「对象目录是否无条件随回滚走、有没有校验、命名是否与 §6.1
一致」，不执行 && / cp 这些命令。
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

# ------------------------------------------------------------- §6.1/§6.2 回滚契约


def _section(text: str, title: str, next_title: str) -> str:
    assert title in text, f"手册里找不到 {title}"
    return text.split(title, 1)[1].split(next_title, 1)[0]


def _rollback_section() -> str:
    text = DEPLOY_README.read_text(encoding="utf-8")
    return _section(text, "### 6.2 ", "\n## ")


def test_the_rollback_merges_objects_unconditionally():
    """§6.1 会把 ``.local-objects`` 搬进新目录，旧树里通常**没有**这个子目录。

    「两边都在才合并」在这种状态下永远为假，更新窗内的对象会全部留在 ``app.bad-*``
    里，回滚后的服务读不到（M2 在 #44 的 P1）。所以：目标目录必须无条件建出来，
    合并源必须是被替换掉的那棵树。
    """

    section = _rollback_section()
    assert "[ -d /opt/huice/app/.local-objects ] && [ -d" not in section, (
        "§6.2 仍在用「两边都在才合并」的条件：§6.1 已经把对象搬走，这个条件在真实"
        "回滚路径上永远是假。"
    )
    commands = [line.strip() for line in section.splitlines()]
    merge = next(
        (
            index
            for index, line in enumerate(commands)
            if line.startswith('cp -an "/opt/huice/app.bad-$X/.local-objects/.')
        ),
        None,
    )
    assert merge is not None, "§6.2 没有把 app.bad-$X 里的对象并回旧树"
    assert any(
        line.startswith("install -d") and "app.old-$X/.local-objects" in line
        for line in commands[:merge]
    ), "合并不前先建目标目录：旧树里通常已经没有 .local-objects，cp 会直接失败"


def test_the_rollback_verifies_every_object_arrived():
    """合并后要能证明「源里的每个文件都在目标里」，而不是 merger 完就当成功。"""

    section = _rollback_section()
    assert "comm -23" in section, "§6.2 没有校验对象是否全部搬过去"
    before_check = section.split("comm -23", 1)[0]
    assert "app.bad-$X/.local-objects" in before_check, "校验的源清单应取自 app.bad-$X"


def test_the_rollback_uses_the_same_directory_names_as_the_update():
    """更新以目标提交命名 ``app.old-$X``，回滚照着用即可——换个名字容易找错目录。"""

    section = _rollback_section()
    assert "<上一个提交>" not in section, (
        "§6.2 仍写「app.old-<上一个提交>」：§6.1 实际建的是 app.old-$X（X 是本次部署"
        "的提交），两节名字对不上。"
    )
    assert "app.old-$X" in section


def test_the_update_refuses_to_overwrite_an_existing_old_directory():
    """``app.old-$X`` 已存在时 ``mv`` 会把它塞到里面，事后从目录名上看不出来。"""

    text = DEPLOY_README.read_text(encoding="utf-8")
    update = _section(text, "### 6.1 ", "### 6.2 ")
    assert '[ ! -e "/opt/huice/app.old-$X" ]' in update, "§6.1 缺 app.old-$X 已存在时的守卫"


def test_the_backup_restore_keeps_objects_created_during_the_update():
    """备份包是**更新前**打的，直接解包会把更新窗内的新对象覆盖掉：先另存、再并回。"""

    section = _rollback_section()
    extract = section.index("tar -xzf")
    assert section.index("objects-after-update-$X") < extract, (
        "备份包路线要先另存当前对象再解包，否则更新窗内的对象随目录一起没了"
    )
    assert section.index('cp -an "/root/objects-after-update-$X/."') > extract, (
        "解包之后要把另存的对象并回去（不覆盖）"
    )
    assert "comm -23" in section.split("tar -xzf", 1)[1], "备份包路线同样要校验对象齐全"

