"""部署文件指纹：给「这台机器上跑的是哪份代码」一个**可复算**的答案。

``codeCommit`` 证明不了任何事——云端没有 ``.git``，那个值只能手打，打错、写反、
两侧都 ``null`` 都不会有人发现（M2 在 #36 与 #39 都点到这一条）。这里换一条路：

* 对**部署机上实际存在的代码文件**算一遍规范化哈希，记进清单；
* 任何人把某个提交 checkout 出来、用同一个算法算，就能得到同一个值，从而把
  「提交 ↔ 指纹」绑死；
* ``verify`` 在**当前机器**上按同一算法重算，不同就是「部署的代码不同」。

三件事分开证明，不能互相冒充（这也是 M2 在 #39 里给的边界）：

1. **磁盘上的代码是什么** —— 由本模块的指纹回答；
2. **这份代码是不是某个提交** —— 由 checkout 后复算回答，本模块不碰；
3. **进程里跑的是不是这份代码** —— 指纹答不了。能做的只有交叉核对：把服务进程的
   启动时间与代码文件的最后写入时间比一比，若**有文件比进程还新**，那盘上是新的、
   进程里多半是旧的（``git pull`` 之后没重启）。:func:`stale_code_files` 就是这个
   检查，签认与核对时都会跑。

范围与排除项见 :data:`CODE_SCOPE` 与 :func:`collect_code_files`。**缺文件、空集合
一律报错**，不允许「扫到 0 个文件」算出一个「稳定」的指纹：那等于什么都没证明，
却长得像证明过。
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Sequence

from .snapshot import sha256_hex

#: 进指纹的范围：应用代码整棵树 + 契约 + 依赖锁文件。
#: 不含 ``infra/sql``——库结构已由清单里逐表导出的内容证明，混进来会让「改个注释」
#: 也触发核对失败（M2 在 #39 的方向：是否纳入按证据目的决定，本项只答代码身份）。
CODE_SCOPE: tuple[str, ...] = (
    "services/api/app",
    "contracts/openapi.yaml",
    "services/api/requirements.lock",
)

#: 目录名命中即整棵跳过；后缀命中即单文件跳过。字节码是构建产物，换台机器就不同。
EXCLUDED_DIR_NAMES = frozenset({"__pycache__"})
EXCLUDED_SUFFIXES = frozenset({".pyc"})

#: 目录类范围只收录这个后缀；显式点名的文件不受它限制。
DIRECTORY_SUFFIXES = frozenset({".py"})


class CodeFingerprintError(Exception):
    """指纹算不出来：范围内的文件缺失、或一个文件都没扫到。"""


def default_code_root() -> Path:
    """本工具**所在的那棵树**的根（``services/api/app/evaluation`` 往上四层）。

    刻意不用 ``__main__.PROJECT_ROOT``：那个常量描述的是「签认路径以谁为基准」，
    测试与换布局时会被替换成临时目录；指纹跟着它变，就等于「换个人在哪儿加壳跑，
    代码身份就变了」。算指纹的树由代码自己所在的位置决定，唯一例外是显式传
    ``fingerprint --root``——那是要点名复算另一棵树。
    """

    return Path(__file__).resolve().parents[4]


@dataclass(frozen=True, slots=True)
class CodeFile:
    """一个参与计算的文件。``path`` 是相对项目根的 POSIX 路径，跨平台可比。"""

    path: str
    sha256: str
    size: int
    mtime: datetime


@dataclass(frozen=True, slots=True)
class CodeInspection:
    """一次代码检验的结果；``manifest_fields()`` 给出写进清单的那几个字段。"""

    fingerprint: str
    files: tuple[CodeFile, ...]

    @property
    def newest_mtime(self) -> datetime | None:
        latest: datetime | None = None
        for item in self.files:
            if latest is None or item.mtime > latest:
                latest = item.mtime
        return latest

    def manifest_fields(self) -> dict[str, Any]:
        """写进清单的字段：指纹 + 文件数 + 范围。

        文件数单独记，是为了让人一眼看出「是不是只算到一个文件」——只给哈希的话，
        一个被截断的范围与完整迁移看起来一样。范围也记，因为「改了范围」必须能像
        「改了代码」一样被 ``verify`` 发现。
        """

        return {
            "codeFingerprint": self.fingerprint,
            "codeFiles": len(self.files),
            "codeScope": list(CODE_SCOPE),
        }


def _file_entry(root: Path, path: Path) -> CodeFile:
    stat = path.stat()
    return CodeFile(
        path=path.resolve().relative_to(root.resolve()).as_posix(),
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        size=stat.st_size,
        mtime=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
    )


def _walk(root: Path, entry: str) -> list[Path]:
    """展开一条范围项：目录按后缀递归收集，文件原样返回。"""

    target = root / entry
    if not target.exists():
        raise CodeFingerprintError(
            f"代码指纹的范围内缺少 {entry}——指纹要求 {', '.join(CODE_SCOPE)} 全部存在，"
            "缺一项就不算完整（少了任何一块，「磁盘上是不是同一份代码」都答不上来）"
        )
    if target.is_file():
        return [target]

    found: list[Path] = []
    for path in target.rglob("*"):
        if not path.is_file():
            continue
        if EXCLUDED_DIR_NAMES & set(PurePosixPath(path.relative_to(root).as_posix()).parts):
            continue
        if path.suffix in EXCLUDED_SUFFIXES or path.suffix not in DIRECTORY_SUFFIXES:
            continue
        found.append(path)
    if not found:
        raise CodeFingerprintError(f"代码指纹的范围内 {entry} 一个文件都没扫到——空集合不能算指纹")
    return found


def collect_code_files(root: Path, *, scope: Sequence[str] = CODE_SCOPE) -> tuple[CodeFile, ...]:
    """按 :data:`CODE_SCOPE` 收集文件并按相对路径排序。

    排序是算指纹的一部分：文件系统给的遍历顺序不稳定，不排序的话同一棵树能算出
    不同的值。排除 ``__pycache__`` 与 ``*.pyc``：它们随解释器版本与编译时机变化，
    与「这份代码是什么」无关。
    """

    files: dict[str, CodeFile] = {}
    for entry in scope:
        for path in _walk(root, entry):
            item = _file_entry(root, path)
            files[item.path] = item
    return tuple(files[key] for key in sorted(files))


def fingerprint_of(files: Sequence[CodeFile]) -> str:
    """把「路径 + 文件字节哈希」按路径序列化后再取一次 SHA-256。

    与清单里其他哈希同一个口径（``snapshot.sha256_hex``）：排序键、无多余空白。
    指纹里**不含**大小与时间——那两项会随 ``cp``、``git checkout`` 变化而内容不变。
    """

    if not files:
        raise CodeFingerprintError("没有任何文件参与，不能算指纹")
    return sha256_hex([{"path": item.path, "sha256": item.sha256} for item in files])


def inspect_code(root: Path, *, scope: Sequence[str] = CODE_SCOPE) -> CodeInspection:
    files = collect_code_files(root, scope=scope)
    return CodeInspection(fingerprint=fingerprint_of(files), files=files)


def stale_code_files(files: Sequence[CodeFile], started_at: datetime) -> tuple[CodeFile, ...]:
    """写于「进程启动之后」的代码文件。

    这些文件说明：盘上的代码比正在跑的进程新，进程里很可能还是旧代码。判断用
    最后写入时间，只能作为交叉证据——``touch``、解包、``cp -p`` 都会改变它，所以
    **不因为时间新就断言进程是旧的**，只报出「哪几个文件、什么时候写的」，由人决定
    要不要重启。
    """

    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    return tuple(item for item in files if item.mtime > started_at)


def process_started_at(pid: int) -> datetime:
    """从 ``/proc`` 读进程启动时间（只在 Linux 上可用）。

    取 ``/proc/<pid>/stat`` 第 22 字段（自系统启动以来的时钟滴答）再加上
    ``/proc/stat`` 的 ``btime``。不用 ``ps`` 的文本输出：它的时间格式随发行版与语言
    环境变化，解析出来的东西没法当证据。
    """

    if not hasattr(os, "sysconf"):
        raise CodeFingerprintError("读进程启动时间只在 Linux 上可用（当前系统没有 /proc）")
    try:
        ticks = os.sysconf("SC_CLK_TCK")
        stat_text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        boot_text = Path("/proc/stat").read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        raise CodeFingerprintError(f"读不到进程 {pid} 的启动时间（{exc.__class__.__name__}）") from None

    # comm 字段可能含空格与括号，从最后一个 ')' 之后再切
    fields = stat_text.rsplit(")", 1)[1].split()
    start_ticks = int(fields[19])
    boot = next(
        (int(line.split()[1]) for line in boot_text.splitlines() if line.startswith("btime ")),
        None,
    )
    if boot is None:
        raise CodeFingerprintError("/proc/stat 里没有 btime，读不到系统启动时间")
    return datetime.fromtimestamp(boot + start_ticks / ticks, tz=timezone.utc)
