"""``infra/deploy/rollback.sh`` 的行为：对象目录跟着回滚走，出问题就**不换目录、不起服务**。

写进手册的「有差异就停下」不会自己发生：``comm`` 在有差异时退出码仍是 0，``cp -an``
遇到同名文件也只是静默跳过。所以这里不用文档字符串检查，而是在临时目录里造两棵真的
代码树、用一个会记账的假 ``systemctl`` 跑真实 shell，断言 **退出码、目录换没换、服务有
没有被重新拉起**（M2 在 #44 的复审 P1）。

停服之后的每一步都单独注入失败（``mv``、``chown``、``find``、``systemctl``），因为
「脚本写了恢复」和「真的恢复到了」是两件事：注入点用 PATH 上的垫片实现，垫片按环境变量
决定失败哪一次，其余情况原样转给真命令。
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
APP = "./app"
BAD = f"./app.bad-{X}"
STAGE = f"./app.stage-{X}"
OLD = f"./app.old-{X}"

BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="执行部署脚本需要 bash")

_FAKE_SYSTEMCTL = """#!/bin/sh
printf '%s\\n' "$*" >> systemctl.log
if [ -f systemctl.fail ] && [ "$(cat systemctl.fail)" = "$1" ]; then
    printf 'fake systemctl: %s 按测试要求失败\\n' "$1" >&2
    exit 1
fi
exit 0
"""

#: 垫片模板：真命令路径在写文件时填进 ``{real}``。失败条件由环境变量控制，
#: 没设变量时完全等同于真命令——正常用例照旧跑真的 ``mv``/``find``。
_SHIM = """#!/bin/sh
{guard}
exec "{real}" "$@"
"""

_MV_GUARD = """if [ -n "${HUICE_TEST_MV_FAIL_SRC:-}" ]; then
    src=
    dst=
    for a in "$@"; do
        case "$a" in
            --) ;;
            *) src=$dst; dst=$a ;;
        esac
    done
    case "$src" in
        *"$HUICE_TEST_MV_FAIL_SRC"*)
            if [ -z "${HUICE_TEST_MV_FAIL_DST:-}" ] || [ "$dst" = "$HUICE_TEST_MV_FAIL_DST" ]; then
                printf 'fake mv: 按测试要求拒绝 %s -> %s\\n' "$src" "$dst" >&2
                exit 1
            fi
            ;;
    esac
fi"""

_CHOWN_GUARD = """if [ "${HUICE_TEST_CHOWN_FAIL:-}" = "1" ]; then
    printf 'fake chown: 按测试要求失败\\n' >&2
    exit 1
fi"""

_FIND_GUARD = """if [ "${HUICE_TEST_FIND_FAIL:-}" = "1" ]; then
    printf 'fake find: 按测试要求读不全\\n' >&2
    exit 1
fi"""

_SHIMS = {"mv": _MV_GUARD, "chown": _CHOWN_GUARD, "find": _FIND_GUARD}


class Sandbox:
    """一套假的部署根：``app``、``app.old-$X``、假的 ``systemctl`` 与失败注入垫片。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.bin = root / "bin"
        self.bin.mkdir()
        # write_bytes：不让 Windows 把 \n 转成 \r\n（CRLF 的 shebang 在 bash 里跑不起来）
        self._write(self.bin / "systemctl", _FAKE_SYSTEMCTL)
        for name, guard in _SHIMS.items():
            real = self._which(name)
            if real is None:
                continue
            self._write(self.bin / name, _SHIM.format(guard=guard, real=real))
        subprocess.run(
            [BASH, "-c", 'chmod +x bin/*'],
            cwd=root,
            check=True,
        )

    def _which(self, name: str) -> str | None:
        """在**未**插入垫片目录的 PATH 里找真命令（bash 的 POSIX 形式，便于写进 shebang）。"""

        out = subprocess.run(
            [BASH, "-c", f"command -v {name}"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
        found = out.stdout.strip()
        return found or None

    @staticmethod
    def _write(path: Path, text: str) -> None:
        path.write_bytes(text.encode())

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

    def fail_service(self, action: str) -> None:
        """让假 ``systemctl`` 在 ``action``（start/stop）这一步失败。"""

        self._write(self.root / "systemctl.fail", action)

    def path(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)

    def marker(self, *parts: str) -> str:
        return self.path(*parts).read_bytes().decode().strip()

    def run(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        environment = dict(os.environ)
        environment["HUICE_SYSTEMCTL"] = "bin/systemctl"
        # 垫片目录放在最前：真命令由垫片转调，正常用例行为不变
        environment["PATH"] = self.bin.as_posix() + os.pathsep + environment.get("PATH", "")
        environment.update(env or {})
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


def _skip_without_shim(sandbox: Sandbox, name: str) -> None:
    if not sandbox.path("bin", name).exists():
        pytest.skip(f"这个环境里没有可转调的 {name}")


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

    result = sandbox.run(X, "--base", ".", "--object-store", "obs")

    assert result.returncode == 0, result.stderr
    assert "不涉及本地对象目录" in result.stderr
    assert not sandbox.path("app", ".local-objects").exists()
    assert sandbox.marker("app", "services", "api", "app.py") == "# before"


# --------------------------------------------------------- 对象存储模式判定


def test_local_mode_without_the_object_dir_stops_before_stopping_the_service(
    sandbox: Sandbox,
) -> None:
    """目录没了既可能是 obs，也可能是部署事故——默认 local 时不能当成 obs 放过去。"""

    sandbox.tree(f"app.old-{X}", marker="before")
    sandbox.tree("app", marker="after")

    result = sandbox.run(X, "--base", ".")

    assert result.returncode == 2
    assert "local 模式下" in result.stderr
    assert sandbox.service_calls() == []  # 连服务都没停
    assert sandbox.marker("app", "services", "api", "app.py") == "# after"
    assert sandbox.path(f"app.old-{X}").is_dir()


def test_declared_obs_but_the_object_dir_exists_stops_before_stopping_the_service(
    sandbox: Sandbox,
) -> None:
    """操作员说 obs、磁盘上却还有本地对象目录：状态说不清，不能替人猜。"""

    sandbox.tree(f"app.old-{X}", marker="before")
    sandbox.tree("app", marker="after", objects={"a.bin": b"a"})

    result = sandbox.run(X, "--base", ".", "--object-store", "obs")

    assert result.returncode == 2
    assert "声明了 --object-store obs" in result.stderr
    assert sandbox.service_calls() == []
    assert sandbox.marker("app", "services", "api", "app.py") == "# after"


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


def test_a_find_that_cannot_read_the_objects_stops_instead_of_counting_zero(
    sandbox: Sandbox,
) -> None:
    """进程替换吞掉 ``find`` 的退出码时，读不全的对象会被当成「0 个对象」照样通过。"""

    _skip_without_shim(sandbox, "find")
    sandbox.tree(f"app.old-{X}", marker="before")
    sandbox.tree("app", marker="after", objects={"a.bin": b"a"})

    result = sandbox.run(X, "--base", ".", env={"HUICE_TEST_FIND_FAIL": "1"})

    assert result.returncode == 3
    assert "读不完" in result.stderr
    assert sandbox.marker("app", "services", "api", "app.py") == "# after"
    assert sandbox.path(f"app.old-{X}").is_dir()
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


# ------------------------------------------------------------- 切换失败注入


def test_when_the_first_mv_to_bad_fails_the_old_tree_and_the_service_are_put_back(
    sandbox: Sandbox,
) -> None:
    _skip_without_shim(sandbox, "mv")
    sandbox.tree(f"app.old-{X}", marker="before")
    sandbox.tree("app", marker="after", objects={"a.bin": b"a"})

    result = sandbox.run(
        X,
        "--base",
        ".",
        env={"HUICE_TEST_MV_FAIL_SRC": APP, "HUICE_TEST_MV_FAIL_DST": BAD},
    )

    assert result.returncode == 2
    assert "什么都没换" in result.stderr
    assert sandbox.marker("app", "services", "api", "app.py") == "# after"
    assert sandbox.path(f"app.old-{X}").is_dir()
    assert not sandbox.path(f"app.stage-{X}").exists()
    assert not sandbox.path(f"app.bad-{X}").exists()
    assert sandbox.service_calls() == ["stop huice-api", "start huice-api"]


def test_when_the_second_mv_into_app_fails_the_app_and_the_service_are_put_back(
    sandbox: Sandbox,
) -> None:
    """第二次 mv 失败时 app 已经不在原位：必须把 app 和旧树都放回去，再起服务。"""

    _skip_without_shim(sandbox, "mv")
    sandbox.tree(f"app.old-{X}", marker="before")
    sandbox.tree("app", marker="after", objects={"a.bin": b"a"})

    result = sandbox.run(
        X,
        "--base",
        ".",
        env={"HUICE_TEST_MV_FAIL_SRC": "app.stage-", "HUICE_TEST_MV_FAIL_DST": APP},
    )

    assert result.returncode == 2
    assert "第二步切换失败" in result.stderr
    assert "已把 app 与旧树放回原位" in result.stderr
    # app 回到原来的新代码，旧树也回到 app.old-$X，没有 stage/bad 残留
    assert sandbox.marker("app", "services", "api", "app.py") == "# after"
    assert sandbox.marker(f"app.old-{X}", "services", "api", "app.py") == "# before"
    assert not sandbox.path(f"app.stage-{X}").exists()
    assert not sandbox.path(f"app.bad-{X}").exists()
    assert sandbox.service_calls() == ["stop huice-api", "start huice-api"]


def test_when_chown_fails_the_old_tree_and_the_service_are_put_back(sandbox: Sandbox) -> None:
    _skip_without_shim(sandbox, "chown")
    sandbox.tree(f"app.old-{X}", marker="before")
    sandbox.tree("app", marker="after", objects={"a.bin": b"a"})

    result = sandbox.run(
        X,
        "--base",
        ".",
        env={"HUICE_FORCE_ROOT": "1", "HUICE_TEST_CHOWN_FAIL": "1"},
    )

    assert result.returncode == 2
    assert "改属主失败" in result.stderr
    assert sandbox.marker("app", "services", "api", "app.py") == "# after"
    assert sandbox.path(f"app.old-{X}").is_dir()
    assert not sandbox.path(f"app.stage-{X}").exists()
    assert not sandbox.path(f"app.bad-{X}").exists()
    assert sandbox.service_calls() == ["stop huice-api", "start huice-api"]


def test_when_the_service_does_not_start_the_swap_is_kept_and_reported(sandbox: Sandbox) -> None:
    """已经换完才起不来：这时不能假装回滚成功，退出码 5 并指出现场在哪。"""

    sandbox.tree(f"app.old-{X}", marker="before")
    sandbox.tree("app", marker="after", objects={"a.bin": b"a"})
    sandbox.fail_service("start")

    result = sandbox.run(X, "--base", ".")

    assert result.returncode == 5
    assert "没起来" in result.stderr
    assert sandbox.marker("app", "services", "api", "app.py") == "# before"
    assert sandbox.marker(f"app.bad-{X}", "services", "api", "app.py") == "# after"
    assert sandbox.service_calls() == ["stop huice-api", "start huice-api"]


def test_when_the_service_cannot_be_stopped_nothing_is_touched(sandbox: Sandbox) -> None:
    sandbox.tree(f"app.old-{X}", marker="before")
    sandbox.tree("app", marker="after", objects={"a.bin": b"a"})
    sandbox.fail_service("stop")

    result = sandbox.run(X, "--base", ".")

    assert result.returncode == 2
    assert "停不下" in result.stderr
    assert sandbox.marker("app", "services", "api", "app.py") == "# after"
    assert sandbox.path(f"app.old-{X}").is_dir()
    assert sandbox.service_calls() == ["stop huice-api"]  # 没停成，也就不该有 start


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
    assert not sandbox.path(f"app.stage-{X}").exists()
    assert sandbox.service_calls() == ["stop huice-api", "start huice-api"]
