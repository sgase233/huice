"""B1b 读侧：``PolicyRepository.date_provenance``（尚未接进响应）。

形状按 D-1：``{version, effectiveFrom, effectiveTo, applicationWindow, note}``；每个日期
字段 ``{status, source, locator, note}``，``derived`` 另带 ``basis``/``derivedFrom``。
三种降级都要覆盖：旁表无行、单字段缺失、来源行已不存在。
"""

from __future__ import annotations

from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from db_support import create, query_one

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("clean_tables")]


def _version(repo, dsn: str, version: str = "深工信规〔2025〕4号"):
    create(repo, version=version)
    return query_one(dsn, "SELECT id FROM policy_version WHERE version = %s", (version,))[0]


def _store(dsn: str, version_id, provenance: dict) -> None:
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "INSERT INTO policy_version_date_provenance (policy_version_id, provenance) "
            "VALUES (%s, %s)",
            (version_id, Jsonb(provenance)),
        )
        conn.commit()


def test_no_row_means_null(database_url_schema: str, repo) -> None:
    version_id = create(repo, version="v1.0").id
    assert repo.date_provenance(version_id) is None


def test_a_verified_field_projects_its_source_kind(database_url_schema: str, repo) -> None:
    version_id = _version(repo, database_url_schema, version="v1.0")
    body_id = query_one(
        database_url_schema,
        "SELECT id FROM policy_source WHERE policy_version_id = %s AND kind = 'body'",
        (version_id,),
    )[0]
    _store(
        database_url_schema,
        version_id,
        {
            "version": 1,
            "effectiveFrom": {
                "status": "verified",
                "source": {"kind": "body"},
                "locator": "第十七条",
                "sourceId": str(body_id),
            },
            "effectiveTo": {
                "status": "derived",
                "basis": "有效期 5 年",
                "derivedFrom": "2026-02-24",
            },
            "applicationWindow": {"status": "not_applicable"},
            "note": "窗口不得当作有效期",
        },
    )

    payload = repo.date_provenance(version_id)

    assert payload["version"] == 1
    assert payload["effectiveFrom"] == {
        "status": "verified",
        "source": "body",
        "locator": "第十七条",
    }
    assert payload["effectiveTo"]["status"] == "derived"
    assert payload["effectiveTo"]["basis"] == "有效期 5 年"
    assert payload["applicationWindow"]["status"] == "not_applicable"
    assert payload["note"] == "窗口不得当作有效期"
    assert "sourceId" not in payload["effectiveFrom"]


def test_a_missing_field_is_null(database_url_schema: str, repo) -> None:
    version_id = _version(repo, database_url_schema)
    _store(database_url_schema, version_id, {"version": 1, "effectiveFrom": {"status": "unknown"}})

    payload = repo.date_provenance(version_id)

    assert payload["effectiveFrom"]["status"] == "unknown"
    assert payload["effectiveTo"] is None
    assert payload["applicationWindow"] is None


def test_a_vanished_source_row_downgrades_to_unverified(database_url_schema: str, repo) -> None:
    version_id = _version(repo, database_url_schema)
    _store(
        database_url_schema,
        version_id,
        {
            "version": 1,
            "effectiveFrom": {
                "status": "verified",
                "source": {"kind": "body"},
                "locator": "第十七条",
                "sourceId": str(uuid4()),  # 指向一个不存在的来源行
            },
        },
    )

    payload = repo.date_provenance(version_id)

    field = payload["effectiveFrom"]
    assert field["status"] == "unverified"
    assert field["source"] is None
    assert field["locator"] == "第十七条"
    assert "来源行已不存在" in field["note"]
