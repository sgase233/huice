"""``infra/deploy/add-published-policy-source.py``：给已发布版本补来源行（受控例外）。

锁住四件事：只动目标版本的来源集合、计划固定后来源重排/替换会被挡住、
其它已发布版本不受影响、操作后守卫仍然生效。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from app.schemas import PolicyImportMetadata
from db_support import query_all, query_one
from support import metadata_payload

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "infra" / "deploy" / "add-published-policy-source.py"

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("clean_tables")]

TARGET_URL = "https://www.sz.gov.cn/cn/xxgk/zfxxgj/tzgg/content/post_12433720.html"
OTHER_URL = "https://example.gov.cn/other.html"
DETAIL_URL = "https://www.sz.gov.cn/szzt2010/zdlyzl/jjshzc/content/post_12435148.html"
DETAIL_TEXT = "第二十四条 本实施细则自印发之日起施行，有效期5年。\n"


def _load():
    spec = importlib.util.spec_from_file_location("add_published_policy_source", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("add_published_policy_source", module)
    spec.loader.exec_module(module)
    return module


add_source = _load()


class FakeStore:
    def __init__(self) -> None:
        self.objects: list[tuple[str, bytes]] = []

    def put(self, data: bytes, suffix: str = "") -> str:
        key = f"fake/{len(self.objects)}{suffix}"
        self.objects.append((key, data))
        return key


def _version(repo, *, version: str = "v1.0", source_url: str = TARGET_URL):
    metadata = PolicyImportMetadata.model_validate(
        metadata_payload(version=version, sourceUrl=source_url)
    )
    return repo.create_policy_version(
        metadata=metadata, sources=[], storage_keys=[], trace_id=str(uuid4())
    )


def _publish(dsn: str, version: str) -> None:
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "UPDATE policy_version SET review_status = 'published', reviewed_at = now() "
            "WHERE version = %s",
            (version,),
        )
        conn.commit()


def _data(tmp_path: Path, *, version: str = "v1.0") -> Path:
    (tmp_path / "detail.txt").write_text(DETAIL_TEXT, encoding="utf-8")
    path = tmp_path / "source.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "entries": [
                    {
                        "targetSourceUrl": TARGET_URL,
                        "targetVersion": version,
                        "reason": "细则第二十四条是有效期依据",
                        "source": {
                            "kind": "attachment",
                            "url": DETAIL_URL,
                            "title": "实施细则（2025年修订版）",
                            "file": "detail.txt",
                            "originalName": "post_12435148.html",
                            "originalSha256": "0" * 64,
                            "originalByteSize": 49372,
                            "fetchedAt": "2026-11-15T00:00:00+08:00",
                        },
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def test_a_published_version_gets_one_more_source(database_url_schema, repo, tmp_path, monkeypatch) -> None:
    _version(repo)
    _publish(database_url_schema, "v1.0")
    _version(repo, version="v2.0", source_url=OTHER_URL)
    _publish(database_url_schema, "v2.0")

    path = _data(tmp_path)
    plan = tmp_path / "plan.json"
    monkeypatch.setenv("DATABASE_URL", database_url_schema)

    assert add_source.run(path, write_plan=plan) == 0
    assert add_source.run(path, apply=True, plan_path=plan, store=FakeStore()) == 0

    rows = query_all(
        database_url_schema,
        "SELECT kind, ordinal, file_name, content_hash, byte_size, mime_type "
        "FROM policy_source WHERE policy_version_id = "
        "(SELECT id FROM policy_version WHERE version = 'v1.0')",
    )
    assert len(rows) == 1
    kind, ordinal, file_name, content_hash, byte_size, mime_type = rows[0]
    assert (kind, ordinal) == ("attachment", 1)
    assert file_name == "post_12435148.html.txt"
    assert mime_type == "text/plain"
    assert content_hash == hashlib.sha256(DETAIL_TEXT.encode()).hexdigest()
    assert byte_size == len(DETAIL_TEXT.encode())


def test_the_guard_still_blocks_published_source_updates(database_url_schema, repo, tmp_path, monkeypatch) -> None:
    _version(repo)
    _publish(database_url_schema, "v1.0")

    path = _data(tmp_path)
    plan = tmp_path / "plan.json"
    monkeypatch.setenv("DATABASE_URL", database_url_schema)
    assert add_source.run(path, write_plan=plan) == 0
    assert add_source.run(path, apply=True, plan_path=plan, store=FakeStore()) == 0

    source_id = query_one(database_url_schema, "SELECT id FROM policy_source")[0]
    with psycopg.connect(database_url_schema) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "UPDATE policy_source SET title = title WHERE id = %s", (source_id,)
            )
        conn.rollback()


def test_a_draft_target_is_refused(database_url_schema, repo, tmp_path, monkeypatch) -> None:
    _version(repo)  # 只创建，不发布
    path = _data(tmp_path)
    monkeypatch.setenv("DATABASE_URL", database_url_schema)

    assert add_source.run(path, apply=True, plan_path=tmp_path / "missing.json") == 2
    # 没有计划时先拒绝；给一份计划也不应动草稿
    plan = tmp_path / "plan.json"
    assert add_source.run(path, write_plan=plan) == 4


def test_a_changed_source_set_after_the_plan_rolls_back(
    database_url_schema, repo, tmp_path, monkeypatch
) -> None:
    _version(repo)
    _publish(database_url_schema, "v1.0")

    path = _data(tmp_path)
    plan = tmp_path / "plan.json"
    monkeypatch.setenv("DATABASE_URL", database_url_schema)
    assert add_source.run(path, write_plan=plan) == 0

    version_id = query_one(
        database_url_schema, "SELECT id FROM policy_version WHERE version = 'v1.0'"
    )[0]
    with psycopg.connect(database_url_schema) as conn:
        conn.execute("ALTER TABLE policy_source DISABLE TRIGGER policy_source_guard_published")
        conn.execute(
            "INSERT INTO policy_source (id, policy_version_id, kind, ordinal, source_url, "
            "title, file_name, mime_type, fetched_at, content_hash, byte_size, text, storage_key) "
            "VALUES (%s, %s, 'attachment', 1, %s, 't', 't.txt', 'text/plain', now(), %s, 1, 't', 'k')",
            (uuid4(), version_id, OTHER_URL, "a" * 64),
        )
        conn.execute("ALTER TABLE policy_source ENABLE TRIGGER policy_source_guard_published")
        conn.commit()

    assert add_source.run(path, apply=True, plan_path=plan, store=FakeStore()) == 3
    rows = query_all(database_url_schema, "SELECT content_hash FROM policy_source")
    assert rows == [("a" * 64,)], "计划后的来源变化必须整批回滚，不能补进第二行"


def test_a_changed_published_fingerprint_rolls_back(
    database_url_schema, repo, tmp_path, monkeypatch
) -> None:
    _version(repo)
    _publish(database_url_schema, "v1.0")
    _version(repo, version="v2.0", source_url=OTHER_URL)
    _publish(database_url_schema, "v2.0")

    path = _data(tmp_path)
    plan = tmp_path / "plan.json"
    monkeypatch.setenv("DATABASE_URL", database_url_schema)
    assert add_source.run(path, write_plan=plan) == 0

    calls = {"n": 0}
    original = add_source._published_fingerprint

    def flaky(conn, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            return "changed"
        return original(conn, **kwargs)

    monkeypatch.setattr(add_source, "_published_fingerprint", flaky)

    assert add_source.run(path, apply=True, plan_path=plan, store=FakeStore()) == 3
    assert query_all(database_url_schema, "SELECT id FROM policy_source") == []
