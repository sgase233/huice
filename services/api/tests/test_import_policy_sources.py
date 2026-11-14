"""``infra/deploy/import-policy-sources.py``：补 SMART / GREEN 两个版本的导入器。

为什么需要它：这两个版本的申报窗口只写在附件里，`data/policies/sources/` 下没有可导入
的正文，云端政策库因此一直缺这两行，`011` 的回填干跑按设计整批停下。导入器只写版本与
来源——不写条款、不发布——所以这里的断言也止于「版本 + 来源行」，不检查状态跃迁。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import psycopg
import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "infra" / "deploy" / "import-policy-sources.py"
DATA = REPO_ROOT / "infra" / "deploy" / "policy-source-import.json"

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("clean_tables")]


def _load():
    spec = importlib.util.spec_from_file_location("import_policy_sources", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("import_policy_sources", module)
    spec.loader.exec_module(module)
    return module


importer = _load()


class FakeStore:
    """对象存储替身：记下写入的字节与返回的键。"""

    def __init__(self) -> None:
        self.puts: list[tuple[bytes, str]] = []

    def put(self, data: bytes, *, suffix: str = "") -> str:
        key = f"fake/{len(self.puts)}{suffix}"
        self.puts.append((data, key))
        return key


def _payload_with_absolute_files() -> dict:
    """把清单里的相对路径换成绝对路径：负向用例把它写到 tmp_path 下，正文仍在仓库里。"""

    payload = json.loads(DATA.read_text(encoding="utf-8"))
    for entry in payload["entries"]:
        for source in entry["sources"]:
            source["file"] = str((DATA.parent / source["file"]).resolve())
    return payload


def test_the_shipped_manifest_is_valid() -> None:
    entries = importer.load_entries(DATA)

    assert [entry.version for entry in entries] == ["深工信规〔2025〕4号", "深工信规〔2024〕12号"]
    for entry in entries:
        kinds = [source.kind for source in entry.sources]
        # 正文必须排第一：policy_source.ordinal 由导入顺序决定，正文占 0、附件从 1 起
        assert kinds[0] == "body"
        assert kinds.count("attachment") == 1


def test_a_missing_body_is_rejected(tmp_path: Path) -> None:
    payload = _payload_with_absolute_files()
    payload["entries"][0]["sources"] = [payload["entries"][0]["sources"][1]]
    path = tmp_path / "data.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(importer.DataError, match=r"entries\[0\]\.sources 必须含一个 kind=body"):
        importer.load_entries(path)


def test_a_missing_file_names_only_the_field(tmp_path: Path) -> None:
    payload = _payload_with_absolute_files()
    payload["entries"][0]["sources"][0]["file"] = "policy-sources/does-not-exist.txt"
    path = tmp_path / "data.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(importer.DataError, match=r"points|不存在"):
        importer.load_entries(path)


def test_dry_run_writes_nothing(database_url_schema: str, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url_schema)

    assert importer.run(DATA, apply=False, store=FakeStore()) == 0

    with psycopg.connect(database_url_schema) as conn:
        count = conn.execute("SELECT count(*) FROM policy_version").fetchone()[0]
    assert count == 0


def test_apply_creates_versions_with_attachment_ordinals(database_url_schema: str, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url_schema)

    store = FakeStore()
    assert importer.run(DATA, apply=True, store=store) == 0

    with psycopg.connect(database_url_schema) as conn:
        rows = conn.execute(
            "SELECT version, review_status FROM policy_version ORDER BY version"
        ).fetchall()
        sources = conn.execute(
            "SELECT pv.version, ps.kind, ps.ordinal, ps.file_name FROM policy_source ps "
            "JOIN policy_version pv ON pv.id = ps.policy_version_id "
            "ORDER BY pv.version, ps.ordinal"
        ).fetchall()

    assert [row[0] for row in rows] == ["深工信规〔2024〕12号", "深工信规〔2025〕4号"]
    # 只建版本，不发布——条款还没有，发布等于伪造审核通过
    assert {row[1] for row in rows} != {"published"}
    smart = [row for row in sources if row[0] == "深工信规〔2025〕4号"]
    assert [(row[1], row[2]) for row in smart] == [("body", 0), ("attachment", 1)]
    assert smart[1][3].endswith("附件1.docx.txt"), "存的是提取文本，扩展名不能冒充原附件"
    green = [row for row in sources if row[0] == "深工信规〔2024〕12号"]
    assert green[1][3] == "11845882.doc.txt"
    # 文本真的写进了对象存储：storage_key 指向 FakeStore 返回的键
    keys = {key for _data, key in store.puts}
    assert len(keys) == 4


def test_a_second_run_skips(database_url_schema: str, monkeypatch, capsys) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url_schema)
    assert importer.run(DATA, apply=True, store=FakeStore()) == 0

    with psycopg.connect(database_url_schema) as conn:
        before = conn.execute("SELECT count(*) FROM policy_source").fetchone()[0]

    capsys.readouterr()
    assert importer.run(DATA, apply=True, store=FakeStore()) == 0
    assert "跳过" in capsys.readouterr().out

    with psycopg.connect(database_url_schema) as conn:
        after = conn.execute("SELECT count(*) FROM policy_source").fetchone()[0]
    assert after == before
