"""``infra/deploy/retire-test-policy-version.py``：把已发布的测试版本降级为草稿。

M3 第三轮选 B：最小代价、保留审计痕迹。这里锁住三件事：只动目标行、其它已发布行
`xmin` 不变、操作后守卫仍然生效。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from app.schemas import PolicyImportMetadata
from db_support import query_one
from support import metadata_payload

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "infra" / "deploy" / "retire-test-policy-version.py"

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("clean_tables")]

TEST_URL = "https://www.sz.gov.cn/zwgk/zfxxgkml/zcfg/content/post_1.html"


def _load():
    spec = importlib.util.spec_from_file_location("retire_test_policy_version", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("retire_test_policy_version", module)
    spec.loader.exec_module(module)
    return module


retire = _load()


def _version(repo, *, version: str = "v1.0", source_url: str = TEST_URL):
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


def _data(tmp_path: Path, version: str = "v1.0", source_url: str = TEST_URL) -> Path:
    path = tmp_path / "retire.json"
    path.write_text(
        json.dumps(
            {"version": 1, "entries": [{"sourceUrl": source_url, "version": version, "reason": "测试数据"}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def test_a_published_test_version_is_downgraded(database_url_schema, repo, tmp_path, monkeypatch) -> None:
    _version(repo)
    _publish(database_url_schema, "v1.0")
    _version(repo, version="v2.0", source_url="https://example.gov.cn/other.html")
    _publish(database_url_schema, "v2.0")

    path = _data(tmp_path)
    monkeypatch.setenv("DATABASE_URL", database_url_schema)
    assert retire.run(path, apply=True) == 0

    with psycopg.connect(database_url_schema) as conn:
        statuses = dict(
            conn.execute("SELECT version, review_status FROM policy_version").fetchall()
        )
    assert statuses["v1.0"] == "pending_parse"
    assert statuses["v2.0"] == "published", "其它已发布行不能被碰"


def test_the_guard_still_blocks_published_updates(database_url_schema, repo, tmp_path, monkeypatch) -> None:
    _version(repo)
    _publish(database_url_schema, "v1.0")
    _version(repo, version="v2.0", source_url="https://example.gov.cn/other.html")
    _publish(database_url_schema, "v2.0")

    path = _data(tmp_path)
    monkeypatch.setenv("DATABASE_URL", database_url_schema)
    assert retire.run(path, apply=True) == 0

    with psycopg.connect(database_url_schema) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "UPDATE policy_version SET revision = gen_random_uuid() WHERE version = 'v2.0'"
            )
        conn.rollback()


def test_a_draft_target_is_refused(database_url_schema, repo, tmp_path, monkeypatch) -> None:
    _version(repo)  # 只创建，不发布
    path = _data(tmp_path)
    monkeypatch.setenv("DATABASE_URL", database_url_schema)

    assert retire.run(path, apply=True) == 4

    status = query_one(
        database_url_schema, "SELECT review_status FROM policy_version WHERE version = 'v1.0'"
    )[0]
    assert status != "published"


def test_a_changed_published_fingerprint_rolls_back(
    database_url_schema, repo, tmp_path, monkeypatch
) -> None:
    """操作前后「除目标外」的已发布行指纹不一致：整批回滚。

    指纹取的是同一范围（排除目标行）的前后两次；这里把函数桩成两次数值不同，
    模拟「别的已发布行在操作期间被动过」。
    """

    _version(repo)
    _publish(database_url_schema, "v1.0")
    _version(repo, version="v2.0", source_url="https://example.gov.cn/other.html")
    _publish(database_url_schema, "v2.0")

    path = _data(tmp_path)
    monkeypatch.setenv("DATABASE_URL", database_url_schema)

    calls = {"n": 0}
    original = retire._published_fingerprint

    def flaky(conn, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            return "changed"
        return original(conn, **kwargs)

    monkeypatch.setattr(retire, "_published_fingerprint", flaky)

    assert retire.run(path, apply=True) == 3
    with psycopg.connect(database_url_schema) as conn:
        status = conn.execute(
            "SELECT review_status FROM policy_version WHERE version = 'v1.0'"
        ).fetchone()[0]
    assert status == "published", "指纹不一致时不能留下降级结果"
