"""迁移 ``011_date_provenance.sql`` 与回填脚本的真实数据库用例（B1a）。

三件事必须成立：结构就位、``policy_version`` 的已发布行不被回填碰过、回填能把出处绑定
到该版本自己的 ``policy_source`` 行并且幂等。第三条是评审要求的口径，前两条是这条口径
能落地的前提——001 的 ``policy_version_guard_published`` 禁止更新已发布行，所以来源只能
写在旁表里；这条用例就是防止有人日后把列加回 ``policy_version``。
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
import psycopg

from app.schemas import PolicyImportMetadata
from db_support import create, source
from support import metadata_payload
from test_backfill_date_provenance import backfill

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("clean_tables")]

SOURCE_URL = "https://example.gov.cn/notice/backfill.html"


def _version_id(dsn: str, version: str):
    """``db_support.create()`` 返回的 ``PolicyDraft.id`` 是**政策** id，不是版本 id。"""
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT id FROM policy_version WHERE version = %s", (version,)
        ).fetchone()
    assert row is not None, f"没有创建出文号为 {version} 的版本"
    return row[0]


def _publish(dsn: str, version_id) -> None:
    """把草稿直接置为已发布（用例只需要那个状态，不跑完整发布流程）。"""
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "UPDATE policy_version SET review_status = 'published', reviewed_at = now() "
            "WHERE id = %s",
            (version_id,),
        )


def _create_with_sources(repo, version: str, kinds: list[str]):
    """按给定顺序造来源：``policy_source.ordinal`` 就是 ``kinds`` 的下标（正文通常为 0）。"""
    metadata = PolicyImportMetadata.model_validate(
        metadata_payload(version=version, sourceUrl=SOURCE_URL)
    )
    return repo.create_policy_version(
        metadata=metadata,
        sources=[source(f"来源 {i}", kind=kind) for i, kind in enumerate(kinds)],
        storage_keys=[f"local/{i}.txt" for i in range(len(kinds))],
        trace_id=str(uuid4()),
    )


def _attachment_ids(dsn: str, version_id) -> list[str]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT id::text FROM policy_source "
            "WHERE policy_version_id = %s AND kind = 'attachment' ORDER BY ordinal",
            (version_id,),
        ).fetchall()
    return [row[0] for row in rows]


def _write(
    tmp_path,
    provenance: dict,
    *,
    source_url: str = SOURCE_URL,
    version: str = "深工信规〔2025〕4号",
    review_status: str = "approved-by-m3",
):
    data = {
        "version": 1,
        "reviewStatus": review_status,
        "entries": [
            {
                "sourceUrl": source_url,
                "version": version,
                "provenance": provenance,
            }
        ],
    }
    path = tmp_path / "data.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_table_exists(database_url_schema: str) -> None:
    with psycopg.connect(database_url_schema) as conn:
        rows = conn.execute(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'policy_version_date_provenance' "
            "ORDER BY column_name"
        ).fetchall()
    assert rows == [
        ("policy_version_id", "uuid", "NO"),
        ("provenance", "jsonb", "NO"),
        ("updated_at", "timestamp with time zone", "NO"),
    ]


def test_migration_is_registered(database_url_schema: str) -> None:
    with psycopg.connect(database_url_schema) as conn:
        row = conn.execute(
            "SELECT description FROM schema_migration WHERE id = '011_date_provenance'"
        ).fetchone()
    assert row is not None
    # 描述里指向回填脚本：只登记一个 id 不够，得说清楚存量怎么补
    assert "backfill-date-provenance.py" in row[0]


def test_provenance_must_be_object(database_url_schema: str, repo) -> None:
    version_id = create(repo, version="v1.0").id
    with psycopg.connect(database_url_schema) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO policy_version_date_provenance (policy_version_id, provenance) "
                "VALUES (%s, to_jsonb(1))",
                (version_id,),
            )


def test_new_version_has_no_provenance_row(database_url_schema: str, repo) -> None:
    """没有行 = 没记录过来源，与「记录为未知」不同；派生默认值不在库里。"""
    version_id = create(repo, version="v1.0").id
    with psycopg.connect(database_url_schema) as conn:
        row = conn.execute(
            "SELECT count(*) FROM policy_version_date_provenance WHERE policy_version_id = %s",
            (version_id,),
        ).fetchone()
    assert row[0] == 0


def test_backfill_binds_source_to_its_own_version(database_url_schema: str, repo, tmp_path, monkeypatch) -> None:
    create(repo, version="深工信规〔2025〕4号", sourceUrl=SOURCE_URL)
    version_id = _version_id(database_url_schema, "深工信规〔2025〕4号")
    (attachment_id,) = _attachment_ids(database_url_schema, version_id)

    path = _write(
        tmp_path,
        {
            "version": 1,
            "applicationWindow": {
                "status": "verified",
                "source": {"kind": "attachment", "ordinal": 1},
                "locator": "附件 1",
            },
            "effectiveTo": {"status": "unverified"},
        },
    )
    monkeypatch.setenv("DATABASE_URL", database_url_schema)

    assert backfill.run(path, apply=True) == 0

    with psycopg.connect(database_url_schema) as conn:
        stored = conn.execute(
            "SELECT provenance FROM policy_version_date_provenance WHERE policy_version_id = %s",
            (version_id,),
        ).fetchone()[0]

    assert stored["applicationWindow"]["sourceId"] == attachment_id
    assert stored["effectiveTo"] == {"status": "unverified"}

    # 幂等：第二次运行不再改动任何行
    assert backfill.run(path, apply=True) == 0
    with psycopg.connect(database_url_schema) as conn:
        again = conn.execute(
            "SELECT provenance FROM policy_version_date_provenance WHERE policy_version_id = %s",
            (version_id,),
        ).fetchone()[0]
    assert again == stored


def test_attachment_ordinal_is_rank_not_absolute(database_url_schema: str, repo, tmp_path, monkeypatch) -> None:
    """数据文件写「第几份附件」：没有正文时，第一份附件的绝对 ordinal 是 0，仍应命中它。"""
    _create_with_sources(repo, "深工信规〔2025〕4号", ["attachment", "attachment"])
    version_id = _version_id(database_url_schema, "深工信规〔2025〕4号")
    first, second = _attachment_ids(database_url_schema, version_id)

    path = _write(
        tmp_path,
        {
            "version": 1,
            "applicationWindow": {
                "status": "verified",
                "source": {"kind": "attachment", "ordinal": 1},
                "locator": "附件 1",
            },
        },
    )
    monkeypatch.setenv("DATABASE_URL", database_url_schema)

    assert backfill.run(path, apply=True) == 0
    with psycopg.connect(database_url_schema) as conn:
        stored = conn.execute(
            "SELECT provenance FROM policy_version_date_provenance WHERE policy_version_id = %s",
            (version_id,),
        ).fetchone()[0]
    assert stored["applicationWindow"]["sourceId"] == first
    assert stored["applicationWindow"]["sourceId"] != second


def test_second_attachment_of_two_is_picked_by_rank(database_url_schema: str, repo, tmp_path, monkeypatch) -> None:
    """仓库数据文件的形状：正文 + 两个附件，第 2 份附件要落到最后那份。"""
    _create_with_sources(repo, "深工信规〔2025〕4号", ["body", "attachment", "attachment"])
    version_id = _version_id(database_url_schema, "深工信规〔2025〕4号")
    first, second = _attachment_ids(database_url_schema, version_id)

    path = _write(
        tmp_path,
        {
            "version": 1,
            "applicationWindow": {
                "status": "verified",
                "source": {"kind": "attachment", "ordinal": 2},
                "locator": "附件 2",
            },
        },
    )
    monkeypatch.setenv("DATABASE_URL", database_url_schema)

    assert backfill.run(path, apply=True) == 0
    with psycopg.connect(database_url_schema) as conn:
        stored = conn.execute(
            "SELECT provenance FROM policy_version_date_provenance WHERE policy_version_id = %s",
            (version_id,),
        ).fetchone()[0]
    assert stored["applicationWindow"]["sourceId"] == second
    assert stored["applicationWindow"]["sourceId"] != first


def test_backfill_apply_refused_until_data_is_approved(database_url_schema: str, repo, tmp_path, monkeypatch) -> None:
    """``draft-pending-m3`` 连 ``--apply`` 都不放行；干跑照常，且一行不写。"""
    create(repo, version="深工信规〔2025〕4号", sourceUrl=SOURCE_URL)
    version_id = _version_id(database_url_schema, "深工信规〔2025〕4号")
    path = _write(
        tmp_path,
        {"version": 1, "effectiveTo": {"status": "unknown"}},
        review_status="draft-pending-m3",
    )
    monkeypatch.setenv("DATABASE_URL", database_url_schema)

    assert backfill.run(path, apply=True) == 2
    assert backfill.run(path, apply=False) == 0

    with psycopg.connect(database_url_schema) as conn:
        count = conn.execute(
            "SELECT count(*) FROM policy_version_date_provenance WHERE policy_version_id = %s",
            (version_id,),
        ).fetchone()[0]
    assert count == 0


def test_backfill_conflict_needs_overwrite(database_url_schema: str, repo, tmp_path, monkeypatch) -> None:
    """库里已有不同的值：默认停下（返回 3）且不写；``--overwrite`` 才改写。"""
    create(repo, version="深工信规〔2025〕4号", sourceUrl=SOURCE_URL)
    version_id = _version_id(database_url_schema, "深工信规〔2025〕4号")
    monkeypatch.setenv("DATABASE_URL", database_url_schema)

    first = _write(tmp_path, {"version": 1, "effectiveTo": {"status": "unknown"}})
    assert backfill.run(first, apply=True) == 0

    second = _write(tmp_path, {"version": 1, "effectiveTo": {"status": "unverified"}})
    assert backfill.run(second, apply=True) == 3
    with psycopg.connect(database_url_schema) as conn:
        stored = conn.execute(
            "SELECT provenance FROM policy_version_date_provenance WHERE policy_version_id = %s",
            (version_id,),
        ).fetchone()[0]
    assert stored["effectiveTo"] == {"status": "unknown"}

    assert backfill.run(second, apply=True, overwrite=True) == 0
    with psycopg.connect(database_url_schema) as conn:
        stored = conn.execute(
            "SELECT provenance FROM policy_version_date_provenance WHERE policy_version_id = %s",
            (version_id,),
        ).fetchone()[0]
    assert stored["effectiveTo"] == {"status": "unverified"}


def test_backfill_never_touches_published_version(database_url_schema: str, repo, tmp_path, monkeypatch) -> None:
    """回填目标是已发布版本：能写来源，但 ``policy_version`` 那一行必须一字未改。"""
    create(repo, version="深工信规〔2025〕4号", sourceUrl=SOURCE_URL)
    version_id = _version_id(database_url_schema, "深工信规〔2025〕4号")
    _publish(database_url_schema, version_id)

    path = _write(
        tmp_path,
        {
            "version": 1,
            "effectiveFrom": {
                "status": "verified",
                "source": {"kind": "body"},
                "locator": "公告第三条",
            },
        },
    )
    monkeypatch.setenv("DATABASE_URL", database_url_schema)

    with psycopg.connect(database_url_schema) as conn:
        before = conn.execute(
            "SELECT xmin::text, review_status FROM policy_version WHERE id = %s", (version_id,)
        ).fetchone()
    assert before[1] == "published"

    assert backfill.run(path, apply=True) == 0

    with psycopg.connect(database_url_schema) as conn:
        after = conn.execute(
            "SELECT xmin::text, review_status FROM policy_version WHERE id = %s", (version_id,)
        ).fetchone()
        stored = conn.execute(
            "SELECT provenance FROM policy_version_date_provenance WHERE policy_version_id = %s",
            (version_id,),
        ).fetchone()[0]
    # xmin 变了就说明这一行被 UPDATE 过——已发布版本不允许这样
    assert after[0] == before[0]
    assert after[1] == "published"
    assert stored["effectiveFrom"]["status"] == "verified"


def test_backfill_refuses_unknown_version_and_writes_nothing(database_url_schema: str, repo, tmp_path, monkeypatch) -> None:
    """定位不到政策版本时整批停下，且不写任何行——回填错位比回填缺失更难发现。"""
    create(repo, version="v1.0")
    version_id = _version_id(database_url_schema, "v1.0")
    path = _write(
        tmp_path,
        {"version": 1, "effectiveTo": {"status": "unknown"}},
        source_url="https://example.gov.cn/notice/missing.html",
        version="不存在的文号",
    )
    monkeypatch.setenv("DATABASE_URL", database_url_schema)

    assert backfill.run(path, apply=True) == 2
    with psycopg.connect(database_url_schema) as conn:
        count = conn.execute(
            "SELECT count(*) FROM policy_version_date_provenance"
        ).fetchone()[0]
        still_there = conn.execute(
            "SELECT count(*) FROM policy_version WHERE id = %s", (version_id,)
        ).fetchone()[0]
    assert count == 0
    assert still_there == 1
