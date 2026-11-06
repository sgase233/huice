"""``infra/deploy/rollback.sh`` 的行为：对象目录跟着回滚走，出问题就**不换目录、不起服务**。

写进手册的「有差异就停下」不会自己发生：``comm`` 在有差异时退出码仍是 0，``cp -an``
遇到同名文件也只是静默跳过。所以这里不用文档字符串检查，而是在临时目录里造两棵真的
代码树、用一个会记账的假 ``systemctl`` 跑真实 shell，断言 **退出码、目录换没换、服务有
没有被重新拉起**（M2 在 #44 的复审 P1）。
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "infra" / "deploy" / "rollback.sh"

#: §6.1 部署的那个提交号：``app.old-$X`` 里是它之前的代码树。
X = "abc1234"

BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="执行部署脚本需要 bash")

_FAKE_SYSTEMCTL = """#!/bin/sh
printf '%s\\n' "$*" >> systemctl.log
"""


class Sandbox:
    """一套假的部署根：``app``、``app.old-$X``、假的 ``systemctl``，状态用文件记录。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "bin").mkdir()
        # write_bytes：不让 Windows 把 \n 转成 \r\n（CRLF 的 shebang 在 bash 里跑不起来）
        (root / "bin" / "systemctl").write_bytes(_FAKE_SYSTEMCTL.encode())
        subprocess.run(
            [BASH, "-c", "chmod +x bin/systemctl"],
            cwd=root,
            check=True,
        )

    def tree(self, name: str, *, marker: str, objects: dict[str, bytes] | None = None) -> Path:
        base = self.root / name
        (base / "services" / "api").mkdir(parents=True, exist_ok=True)
        (base / "services" / "api" / "app.py").write_bytes(f"# {marker}\n".encode())
        for rel, content in (objects or {}).items():
            path = base / ".local-objects" / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        return base

    def backup(self, *, marker: str, objects: dict[str, bytes] | None = None) -> Path:
        """造一个「更新前」的备份包：包里是 ``app/...``，与手册里的 tar 结构一致。"""

        path = self.root / "app-before.tar.gz"
        with tarfile.open(path, "w:gz") as tar:
            self._add(tar, "app/services/api/app.py", f"# {marker}\n".encode())
            for rel, content in (objects or {}).items():
                self._add(tar, f"app/.local-objects/{rel}", content)
        return path

    @staticmethod
    def _add(tar: tarfile.TarFile, name: str, content: bytes) -> None:
        info = tarfile.TarInfo(name)
        info.size = len(content)
        info.mode = 0o644
        tar.addfile(info, io.BytesIO(content))

    def path(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)

    def marker(self, *parts: str) -> str:
        return self.path(*parts).read_bytes().decode().strip()

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        environment = dict(os.environ)
        environment["HUICE_SYSTEMCTL"] = "bin/systemctl"
        return subprocess.run(
            [BASH, str(SCRIPT), *args],
            cwd=self.root,
            env=environment,
            capture_output=True,
            # bash 输出是 UTF-8；不指定会按 Windows 的 GBK 解码，中文提示直接变成解码异常
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )

    def service_calls(self) -> list[str]:
        path = self.root / "systemctl.log"
        if not path.exists():
            return []
        return path.read_bytes().decode().splitlines()


@pytest.fixture()
def sandbox(tmp_path: Path) -> Sandbox:
    return Sandbox(tmp_path)


# --------------------------------------------------------------- 正常回滚


def test_the_objects_moved_into_the_new_tree_come_back_with_the_rollback(sandbox: Sandbox) -> None:
    """§6.1 把对象**搬**进了新目录，旧树里已经没有这个子目录——回滚仍要把它们带回去。"""

    sandbox.tree(f"app.old-{X}", marker="before")
    sandbox.tree("app", marker="after", objects={"pre/a.bin": b"pre", "during.bin": b"during"})

    result = sandbox.run(X, "--base", ".")

    assert result.returncode == 0, result.stderr
    assert sandbox.marker("app", "services", "api", "app.py") == "# before"
    assert sandbox.path("app", ".local-objects", "pre", "a.bin").read_bytes() == b"pre"
    assert sandbox.path("app", ".local-objects", "during.bin").read_bytes() == b"during"
    assert sandbox.marker(f"app.bad-{X}", "services", "api", "app.py") == "# after"
    assert sandbox.service_calls() == ["stop huice-api", "start huice-api"]


def test_an_object_that_exists_on_both_sides_with_the_same_content_is_kept_once(
    sandbox: Sandbox,
) -> None:
    sandbox.tree(f"app.old-{X}", marker="before", objects={"same.bin": b"same"})
    sandbox.tree("app", marker="after", objects={"same.bin": b"same"})

    result = sandbox.run(X, "--base", ".")

    assert result.returncode == 0, result.stderr
    assert sandbox.path("app", ".local-objects", "same.bin").read_bytes() == b"same"
    assert sandbox.marker("app", "services", "api", "app.py") == "# before"


def test_obs_mode_without_a_local_object_dir_still_rolls_back(sandbox: Sandbox) -> None:
    """obs 模式下 app 里本来就没有 ``.local-objects``：不能因此中断，也不能凭空建一个。"""

    sandbox.tree(f"app.old-{X}", marker="before")
    sandbox.tree("app", marker="after")

    result = sandbox.run(X, "--base", ".")

    assert result.returncode == 0, result.stderr
    assert "obs 模式" in result.stderr
    assert not sandbox.path("app", ".local-objects").exists()
    assert sandbox.marker("app", "services", "api", "app.py") == "# before"


# ------------------------------------------------------------------- 停手


def test_a_same_name_object_with_different_content_stops_before_switching(
    sandbox: Sandbox,
) -> None:
    """``cp -an`` 会静默留下旧内容，而库里指向的是当前对象：必须停下交给人。"""

    sandbox.tree(f"app.old-{X}", marker="before", objects={"same.bin": b"old"})
    sandbox.tree("app", marker="after", objects={"same.bin": b"new"})

    result = sandbox.run(X, "--base", ".")

    assert result.returncode == 3
    assert "同名对象但内容不同" in result.stderr
    # 什么都没换：app 仍是新代码，旧树原样退回，没有 stage/bad 残留
    assert sandbox.marker("app", "services", "api", "app.py") == "# after"
    assert sandbox.path("app", ".local-objects", "same.bin").read_bytes() == b"new"
    assert sandbox.path(f"app.old-{X}").is_dir()
    assert not sandbox.path(f"app.bad-{X}").exists()
    assert not sandbox.path(f"app.stage-{X}").exists()
    # 服务不能停在「已停止」：原代码要重新起来
    assert sandbox.service_calls() == ["stop huice-api", "start huice-api"]


def test_an_object_that_cannot_be_placed_stops_before_switching(sandbox: Sandbox) -> None:
    """旧树里同名路径是个文件、当前是个目录：放不进去就要停，而不是带着缺口启动。"""

    sandbox.tree(f"app.old-{X}", marker="before")
    sandbox.path(f"app.old-{X}", ".local-objects").mkdir(parents=True)
    sandbox.path(f"app.old-{X}", ".local-objects", "blocker").write_bytes(b"i am a file")
    sandbox.tree("app", marker="after", objects={"blocker/x.bin": b"x"})

    result = sandbox.run(X, "--base", ".")

    assert result.returncode == 3
    assert "复制失败" in result.stderr or "形状不一致" in result.stderr
    assert sandbox.marker("app", "services", "api", "app.py") == "# after"
    assert not sandbox.path(f"app.bad-{X}").exists()
    assert sandbox.service_calls() == ["stop huice-api", "start huice-api"]


def test_without_an_old_tree_or_a_backup_it_stops_before_touching_anything(
    sandbox: Sandbox,
) -> None:
    sandbox.tree("app", marker="after", objects={"a.bin": b"a"})

    result = sandbox.run(X, "--base", ".")

    assert result.returncode == 2
    assert "改用 --from-backup" in result.stderr
    assert sandbox.service_calls() == []  # 连服务都没停
    assert sandbox.marker("app", "services", "api", "app.py") == "# after"


# ----------------------------------------------------------------- 备份包


def test_the_backup_route_keeps_objects_created_during_the_update(sandbox: Sandbox) -> None:
    """备份包是**更新前**打的，里面没有更新窗内新导入的对象：要另存再并回。"""

    backup = sandbox.backup(marker="before", objects={"pre.bin": b"pre"})
    sandbox.tree("app", marker="after", objects={"pre.bin": b"pre", "during.bin": b"during"})

    result = sandbox.run(X, "--base", ".", "--from-backup", backup.name)

    assert result.returncode == 0, result.stderr
    assert sandbox.marker("app", "services", "api", "app.py") == "# before"
    assert sandbox.path("app", ".local-objects", "pre.bin").read_bytes() == b"pre"
    assert sandbox.path("app", ".local-objects", "during.bin").read_bytes() == b"during"
    assert sandbox.service_calls() == ["stop huice-api", "start huice-api"]


def test_the_backup_route_stops_on_a_same_name_object_with_different_content(
    sandbox: Sandbox,
) -> None:
    backup = sandbox.backup(marker="before", objects={"same.bin": b"old"})
    sandbox.tree("app", marker="after", objects={"same.bin": b"new"})

    result = sandbox.run(X, "--base", ".", "--from-backup", backup.name)

    assert result.returncode == 3
    assert "同名对象但内容不同" in result.stderr
    assert sandbox.marker("app", "services", "api", "app.py") == "# after"
    assert not sandbox.path(f"app.bad-{X}").exists()
    assert sandbox.service_calls() == ["stop huice-api", "start huice-api"]
