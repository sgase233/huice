"""``infra/deploy/fix-published-effective-dates.py``：受控修正已发布版本有效期。

锁住三件事：只动目标行、旧值/计划不一致时整批回滚、操作后守卫仍然生效。
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
SCRIPT = REPO_ROOT / "infra" / "deploy" / "fix-published-effective-dates.py"

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("clean_tables")]

TARGET_URL = "https://www.sz.gov.cn/cn/xxgk/zfxxgj/tzgg/content/post_12433720.html"
OTHER_URL = "https://example.gov.cn/other.html"


def _load():
    spec = importlib.util.spec_from_file_location("fix_published_effective_dates", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("fix_published_effective_dates", module)
    spec.loader.exec_module(module)
    return module


fix_dates = _load()


def _version(repo, *, version: str = "v1.0", source_url: str = TARGET_URL):
    metadata = PolicyImportMetadata.model_validate(
        metadata_payload(
            version=version,
            sourceUrl=source_url,
            publishedAt="2025-10-13",
            effectiveFrom="2025-10-15",
            effectiveTo="2025-11-10",
        )
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


def _data(tmp_path: Path, *, old_from: str = "2025-10-15") -> Path:
    path = tmp_path / "dates.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "entries": [
                    {
                        "sourceUrl": TARGET_URL,
                        "version": "v1.0",
                        "expectedOld": {
                            "reviewStatus": "published",
                            "effectiveFrom": old_from,
                            "effectiveTo": "2025-11-10",
                        },
                        "new": {"effectiveFrom": "2025-10-14", "effectiveTo": "2030-10-13"},
                        "reason": "细则第二十四条有效期 5 年",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def test_a_published_version_dates_are_corrected(database_url_schema, repo, tmp_path, monkeypatch) -> None:
    _version(repo)
    _publish(database_url_schema, "v1.0")
    _version(repo, version="v2.0", source_url=OTHER_URL)
    _publish(database_url_schema, "v2.0")
    before_other = query_one(
        database_url_schema, "SELECT xmin::text FROM policy_version WHERE version = 'v2.0'"
    )[0]

    path = _data(tmp_path)
    plan = tmp_path / "plan.json"
    monkeypatch.setenv("DATABASE_URL", database_url_schema)
    assert fix_dates.run(path, write_plan=plan) == 0
    assert fix_dates.run(path, apply=True, plan_path=plan) == 0

    row = query_one(
        database_url_schema,
        "SELECT effective_from::text, effective_to::text, review_status, revision::text, "
        "xmin::text FROM policy_version WHERE version = 'v1.0'",
    )
    assert row[0] == "2025-10-14"
    assert row[1] == "2030-10-13"
    assert row[2] == "published"
    assert row[3] != ""
    after_other = query_one(
        database_url_schema, "SELECT xmin::text FROM policy_version WHERE version = 'v2.0'"
    )[0]
    assert after_other == before_other, "其它已发布行不能被碰"


def test_the_guard_still_blocks_published_updates(database_url_schema, repo, tmp_path, monkeypatch) -> None:
    _version(repo)
    _publish(database_url_schema, "v1.0")
    _version(repo, version="v2.0", source_url=OTHER_URL)
    _publish(database_url_schema, "v2.0")

    path = _data(tmp_path)
    plan = tmp_path / "plan.json"
    monkeypatch.setenv("DATABASE_URL", database_url_schema)
    assert fix_dates.run(path, write_plan=plan) == 0
    assert fix_dates.run(path, apply=True, plan_path=plan) == 0

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
    assert fix_dates.run(path, apply=True, plan_path=tmp_path / "missing.json") == 2
    plan = tmp_path / "plan.json"
    assert fix_dates.run(path, write_plan=plan) == 4


def test_a_wrong_old_value_is_refused(database_url_schema, repo, tmp_path, monkeypatch) -> None:
    _version(repo)
    _publish(database_url_schema, "v1.0")
    path = _data(tmp_path, old_from="2025-10-01")
    monkeypatch.setenv("DATABASE_URL", database_url_schema)

    assert fix_dates.run(path, write_plan=tmp_path / "plan.json") == 2
    value = query_one(
        database_url_schema, "SELECT effective_from::text FROM policy_version WHERE version = 'v1.0'"
    )[0]
    assert value == "2025-10-15"


def test_a_changed_data_file_after_the_plan_is_refused(
    database_url_schema, repo, tmp_path, monkeypatch
) -> None:
    _version(repo)
    _publish(database_url_schema, "v1.0")
    path = _data(tmp_path)
    plan = tmp_path / "plan.json"
    monkeypatch.setenv("DATABASE_URL", database_url_schema)
    assert fix_dates.run(path, write_plan=plan) == 0

    path.write_text(path.read_text(encoding="utf-8").replace("2030-10-13", "2030-10-14"), encoding="utf-8")
    assert fix_dates.run(path, apply=True, plan_path=plan) == 2
    value = query_one(
        database_url_schema, "SELECT effective_from::text FROM policy_version WHERE version = 'v1.0'"
    )[0]
    assert value == "2025-10-15"


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
    assert fix_dates.run(path, write_plan=plan) == 0

    calls = {"n": 0}
    original = fix_dates._published_fingerprint

    def flaky(conn, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            return "changed"
        return original(conn, **kwargs)

    monkeypatch.setattr(fix_dates, "_published_fingerprint", flaky)

    assert fix_dates.run(path, apply=True, plan_path=plan) == 3
    value = query_one(
        database_url_schema, "SELECT effective_from::text FROM policy_version WHERE version = 'v1.0'"
    )[0]
    assert value == "2025-10-15", "指纹不一致时不能留下修正结果"
