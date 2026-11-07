"""部署文件指纹（``EVALUATION_FREEZE`` §5.2「代码身份」）。

只管纯函数：算得稳、算得实、扫不到就报错。「运行进程与磁盘版本」的交叉核对用
:func:`stale_code_files` 就地验，不依赖 ``/proc``；读进程启动时间的
:func:`process_started_at` 只在 Linux 上有意义，其他平台跳过。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.evaluation import __main__ as cli
from app.evaluation.code_fingerprint import (
    CODE_SCOPE,
    CodeFingerprintError,
    collect_code_files,
    default_code_root,
    fingerprint_of,
    inspect_code,
    process_started_at,
    stale_code_files,
)
from app.evaluation.snapshot import diff_snapshots

REPO_ROOT = default_code_root()


def _tree(root: Path, files: dict[str, str]) -> Path:
    """在 ``root`` 下按 ``{相对路径: 内容}`` 造一棵树。"""

    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _minimal_tree(root: Path, **extra: str) -> Path:
    """一份刚好满足范围的树：应用目录、契约、锁文件。"""

    files = {
        "services/api/app/__init__.py": "",
        "services/api/app/agents/extraction.py": "PROMPT = 'v1'\n",
        "contracts/openapi.yaml": "openapi: 3.1.0\n",
        "services/api/requirements.lock": "fastapi==0.0.0\n",
    }
    files.update(extra)
    return _tree(root, files)


# ---------------------------------------------------------------- 算得稳


def test_the_same_tree_hashes_the_same_way_twice(tmp_path: Path) -> None:
    root = _minimal_tree(tmp_path / "a")

    assert inspect_code(root).fingerprint == inspect_code(root).fingerprint


def test_creation_order_does_not_change_the_fingerprint(tmp_path: Path) -> None:
    """文件系统遍历顺序不稳定，同一批文件换个写入顺序必须得到同一个值。"""

    forward = _minimal_tree(
        tmp_path / "forward",
        **{
            "services/api/app/a.py": "A\n",
            "services/api/app/b.py": "B\n",
        },
    )
    backward = _minimal_tree(
        tmp_path / "backward",
        **{
            "services/api/app/b.py": "B\n",
            "services/api/app/a.py": "A\n",
        },
    )

    assert inspect_code(forward).fingerprint == inspect_code(backward).fingerprint


def test_one_changed_byte_changes_the_fingerprint(tmp_path: Path) -> None:
    """单点变异：改一个字符就必须不同，否则「改没改代码」答不上来。"""

    root = _minimal_tree(tmp_path / "a")
    before = inspect_code(root).fingerprint

    (root / "services/api/app/agents/extraction.py").write_text("PROMPT = 'v2'\n", encoding="utf-8")

    assert inspect_code(root).fingerprint != before


def test_bytecode_and_caches_are_left_out(tmp_path: Path) -> None:
    """``__pycache__`` 与 ``*.pyc`` 随解释器版本变化，与「这份代码是什么」无关。"""

    root = _minimal_tree(
        tmp_path / "a",
        **{
            "services/api/app/__pycache__/x.cpython-312.pyc": "字节码",
            "services/api/app/mod.py": "M = 1\n",
        },
    )
    clean = _minimal_tree(tmp_path / "b", **{"services/api/app/mod.py": "M = 1\n"})

    paths = {item.path for item in collect_code_files(root)}
    assert not any("__pycache__" in path or path.endswith(".pyc") for path in paths)
    assert inspect_code(root).fingerprint == inspect_code(clean).fingerprint


def test_the_scope_is_part_of_the_manifest_fields() -> None:
    """范围要记进清单：改了范围必须像改了代码一样能被发现。"""

    fields = inspect_code(REPO_ROOT).manifest_fields()

    assert fields["codeScope"] == list(CODE_SCOPE)
    assert fields["codeFiles"] == len(collect_code_files(REPO_ROOT))
    assert len(fields["codeFingerprint"]) == 64


# ---------------------------------------------------------------- 扫不到就报错


def test_a_missing_scope_entry_is_named(tmp_path: Path) -> None:
    """缺 ``requirements.lock`` 就不能算：少一块就答不上「磁盘上是不是同一份代码」。"""

    root = _tree(
        tmp_path / "a",
        {
            "services/api/app/__init__.py": "",
            "contracts/openapi.yaml": "openapi: 3.1.0\n",
        },
    )

    with pytest.raises(CodeFingerprintError) as error:
        collect_code_files(root)

    assert "services/api/requirements.lock" in str(error.value)


def test_an_empty_directory_is_an_error_not_a_fingerprint(tmp_path: Path) -> None:
    """扫到 0 个文件还照样出哈希，等于「什么都没证明」被记成「证明过了」。"""

    root = _tree(
        tmp_path / "a",
        {
            "services/api/app/README.md": "只有说明，没有代码\n",
            "contracts/openapi.yaml": "openapi: 3.1.0\n",
            "services/api/requirements.lock": "",
        },
    )
    (root / "services/api/app").mkdir(parents=True, exist_ok=True)

    with pytest.raises(CodeFingerprintError) as error:
        collect_code_files(root)

    assert "一个文件都没扫到" in str(error.value)


def test_hashing_no_files_at_all_is_refused() -> None:
    with pytest.raises(CodeFingerprintError):
        fingerprint_of([])


# ---------------------------------------------------------------- 进程 ↔ 磁盘


def test_a_file_written_after_the_process_started_is_reported(tmp_path: Path) -> None:
    """盘上比进程新：进程里很可能还是旧代码，必须能列出来。"""

    root = _minimal_tree(tmp_path / "a")
    files = collect_code_files(root)
    started = datetime.now(timezone.utc) - timedelta(days=1)

    stale = stale_code_files(files, started)

    assert {item.path for item in stale} == {item.path for item in files}


def test_nothing_is_stale_when_the_process_started_later(tmp_path: Path) -> None:
    root = _minimal_tree(tmp_path / "a")
    started = datetime.now(timezone.utc) + timedelta(days=1)

    assert stale_code_files(collect_code_files(root), started) == ()


def test_a_naive_process_start_is_read_as_utc(tmp_path: Path) -> None:
    """手工传 naive 时间不能抛异常：那会让整条核对因为一个时间戳而失败。"""

    root = _minimal_tree(tmp_path / "a")
    started = datetime.now() + timedelta(days=1)

    assert stale_code_files(collect_code_files(root), started) == ()


@pytest.mark.skipif(not Path("/proc/self/stat").exists(), reason="读进程启动时间只在 Linux 上有意义")
def test_the_current_process_start_is_in_the_past() -> None:
    started = process_started_at(os.getpid())

    assert started.tzinfo is not None
    assert started < datetime.now(timezone.utc)


@pytest.mark.skipif(Path("/proc/self/stat").exists(), reason="Linux 上这项检查本来就该成功")
def test_process_start_is_refused_on_other_platforms() -> None:
    with pytest.raises(CodeFingerprintError):
        process_started_at(os.getpid())


# ---------------------------------------------------------------- 命令行


def test_the_fingerprint_command_prints_only_the_hash(capsys) -> None:
    assert cli.main(["fingerprint"]) == 0
    captured = capsys.readouterr()

    assert captured.out.count("\n") == 1
    assert len(captured.out.strip()) == 64
    assert "范围" in captured.err


def test_the_fingerprint_command_can_list_every_file(tmp_path: Path, capsys) -> None:
    """逐文件明细：两棵树差在哪，比对这份就行，不必先相信顶层哈希。"""

    root = _minimal_tree(tmp_path / "a")

    assert cli.main(["fingerprint", "--root", str(root), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["fileCount"] == 4
    assert {item["path"] for item in payload["files"]} == {
        "contracts/openapi.yaml",
        "services/api/app/__init__.py",
        "services/api/app/agents/extraction.py",
        "services/api/requirements.lock",
    }
    assert payload["scope"] == list(CODE_SCOPE)
    assert payload["fingerprint"] == inspect_code(root).fingerprint


def test_the_fingerprint_command_names_a_missing_scope(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as error:
        cli.main(["fingerprint", "--root", str(tmp_path / "nowhere")])

    assert "services/api/app" in str(error.value.code)


# ---------------------------------------------------------------- 比对口径


def _signed(**overrides):
    manifest = {
        "manifestVersion": "3",
        "appVersion": "1.0.0-rc.7",
        "ruleVersion": "rule:criteria@1",
        "codeCommit": None,
        "codeFingerprint": "1" * 64,
        "codeFiles": 41,
        "codeScope": list(CODE_SCOPE),
        "processStartedAt": "2026-11-05T09:00:00+00:00",
        "dataset": None,
        "datasetMapping": None,
        "configKeys": [],
        "configFingerprint": None,
        "policies": [],
        "enterprises": [],
    }
    manifest.update(overrides)
    return manifest


def test_a_changed_deployment_is_reported() -> None:
    problems = diff_snapshots(_signed(), _signed(codeFingerprint="2" * 64))

    assert any("部署代码指纹不同" in item for item in problems)


def test_a_narrowed_scope_is_reported() -> None:
    """只算了几个文件也算「代码不同」——否则截断的范围能冒充完整签认。"""

    problems = diff_snapshots(
        _signed(), _signed(codeFiles=3, codeScope=["services/api/app"])
    )

    assert any("参与指纹的代码文件数不同" in item for item in problems)
    assert any("代码指纹范围不同" in item for item in problems)


def test_a_restart_alone_is_not_a_difference() -> None:
    """签认之后重启过服务不算不一致：启动时间只是交叉证据。"""

    restarted = _signed(processStartedAt="2026-11-06T10:00:00+00:00")

    assert diff_snapshots(_signed(), restarted) == []


def test_a_manifest_without_a_fingerprint_is_not_called_different() -> None:
    """旧清单没有指纹字段：报「不同」会让人以为库变了，实际是清单没记。"""

    legacy = _signed()
    for field in ("codeFingerprint", "codeFiles", "codeScope", "processStartedAt"):
        legacy.pop(field)

    assert diff_snapshots(legacy, _signed()) == []
