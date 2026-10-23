"""仓储与库表的真实数据库用例。

仅在设置 ``HUICE_TEST_DATABASE_URL`` 时运行（本地可用 infra/local 的 Compose 实例）。
每个会话使用独立 schema，结束时删除，不会影响开发库里的数据。
"""

from __future__ import annotations

import os
from uuid import uuid4

import psycopg
import pytest

from app.agents.ingestion import PreparedSource
from app.db import Database
from app.errors import ApiProblem
from app.integrations.storage import PolicyRepository
from app.schemas import PolicyImportMetadata
from support import REPO_ROOT, metadata_payload

pytestmark = pytest.mark.db

SCHEMA = f"huice_test_{os.getpid()}"
INIT_SQL = REPO_ROOT / "infra" / "sql" / "001_init.sql"


def with_schema(dsn: str, schema: str) -> str:
    separator = "&" if "?" in dsn else "?"
    return f"{dsn}{separator}options=-csearch_path%3D{schema}"


@pytest.fixture(scope="module")
def database_url_schema(database_url) -> str:
    script = INIT_SQL.read_text(encoding="utf-8")
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        admin.execute(f"CREATE SCHEMA {SCHEMA}")

    scoped = with_schema(database_url, SCHEMA)
    with psycopg.connect(scoped, autocommit=True) as conn:
        conn.execute(script)
        # 再执行一次，确认脚本可重复执行
        conn.execute(script)

    yield scoped

    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")


@pytest.fixture(autouse=True)
def clean_tables(database_url_schema):
    """用例之间清空业务表；``schema_migration`` 保留，用于证明脚本已执行。"""
    yield
    with psycopg.connect(database_url_schema, autocommit=True) as conn:
        conn.execute(
            """
            DO $$
            DECLARE names text;
            BEGIN
                SELECT string_agg(format('%I.%I', schemaname, tablename), ', ')
                  INTO names
                  FROM pg_tables
                 WHERE schemaname = current_schema()
                   AND tablename <> 'schema_migration';
                IF names IS NOT NULL THEN
                    EXECUTE 'TRUNCATE ' || names || ' CASCADE';
                END IF;
            END $$;
            """
        )


@pytest.fixture()
def db(database_url_schema):
    database = Database(database_url_schema)
    yield database
    database.close()


@pytest.fixture()
def repo(db) -> PolicyRepository:
    return PolicyRepository(db, cursor_secret="db-test-secret")


def query_one(dsn: str, sql: str, params=()):
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def source(text: str = "政策正文", kind: str = "body") -> PreparedSource:
    import hashlib

    return PreparedSource(
        kind=kind,
        file_name=f"{kind}.txt",
        mime_type="text/plain",
        text=text,
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
        byte_size=len(text.encode()) or 1,
    )


def create(repo: PolicyRepository, *, version: str = "v1.0", policy_id=None, **overrides):
    payload = metadata_payload(version=version, **overrides)
    if policy_id is not None:
        payload["policyId"] = str(policy_id)
    metadata = PolicyImportMetadata.model_validate(payload)
    sources = [source("正文"), source("附件", kind="attachment")]
    return repo.create_policy_version(
        metadata=metadata,
        sources=sources,
        storage_keys=["local/body.txt", "local/annex.txt"],
        trace_id=str(uuid4()),
    )


def test_import_persists_every_child_row(repo, database_url_schema):
    draft = create(repo)

    assert query_one(database_url_schema, "SELECT count(*) FROM policy")[0] == 1
    assert query_one(database_url_schema, "SELECT count(*) FROM policy_version")[0] == 1
    assert query_one(database_url_schema, "SELECT count(*) FROM policy_source")[0] == 2
    assert query_one(database_url_schema, "SELECT count(*) FROM trace_step")[0] == 1

    status, revision = query_one(
        database_url_schema, "SELECT review_status, revision::text FROM policy_version"
    )
    assert status == "pending_parse"
    assert revision == str(draft.revision)
    assert [item.kind for item in draft.sources] == ["body", "attachment"]
    assert draft.sources[0].content_hash == source("正文").content_hash


def test_new_version_reuses_the_same_policy(repo, database_url_schema):
    first = create(repo, version="v1.0")
    second = create(repo, version="v1.1", policy_id=first.id)

    assert second.id == first.id
    assert query_one(database_url_schema, "SELECT count(*) FROM policy")[0] == 1
    assert query_one(database_url_schema, "SELECT count(*) FROM policy_version")[0] == 2
    assert second.revision != first.revision


def test_duplicate_version_conflicts_without_partial_writes(repo, database_url_schema):
    # 不带 policyId 的导入会新建政策，因此冲突只在同一 policyId 下重复版本时成立。
    first = create(repo, version="v1.0")

    with pytest.raises(ApiProblem) as info:
        create(repo, version="v1.0", policy_id=first.id)

    assert info.value.status_code == 409
    assert info.value.code == "VERSION_CONFLICT"
    assert query_one(database_url_schema, "SELECT count(*) FROM policy")[0] == 1
    assert query_one(database_url_schema, "SELECT count(*) FROM policy_version")[0] == 1
    assert query_one(database_url_schema, "SELECT count(*) FROM policy_source")[0] == 2


def test_unknown_policy_id_is_rejected(repo):
    with pytest.raises(ApiProblem) as info:
        create(repo, policy_id=uuid4())

    assert info.value.status_code == 422
    assert info.value.code == "POLICY_NOT_FOUND"


def test_list_paginates_with_signed_cursor(repo):
    for index in range(3):
        create(repo, version=f"v1.{index}")

    first_page = repo.list_admin_policies(status=None, policy_type=None, cursor=None, limit=2)
    assert len(first_page.items) == 2
    assert first_page.next_cursor is not None

    second_page = repo.list_admin_policies(
        status=None, policy_type=None, cursor=first_page.next_cursor, limit=2
    )
    assert len(second_page.items) == 1
    assert second_page.next_cursor is None

    seen = {item.version for item in first_page.items} | {item.version for item in second_page.items}
    assert seen == {"v1.0", "v1.1", "v1.2"}


def test_list_filters_by_status_and_type(repo):
    create(repo, version="grant-v1", type="grant")
    create(repo, version="tax-v1", type="tax")

    grants = repo.list_admin_policies(status="pending_parse", policy_type="grant", cursor=None, limit=20)
    assert [item.version for item in grants.items] == ["grant-v1"]

    taxes = repo.list_admin_policies(status="pending_parse", policy_type="tax", cursor=None, limit=20)
    assert [item.version for item in taxes.items] == ["tax-v1"]


def test_get_by_version_and_latest(repo):
    first = create(repo, version="v1.0")
    second = create(repo, version="v1.1", policy_id=first.id)

    explicit = repo.get_admin_policy(first.id, "v1.0")
    latest = repo.get_admin_policy(first.id, None)

    assert explicit.revision == first.revision
    assert latest.revision == second.revision


def test_get_unknown_version_does_not_fall_back(repo):
    first = create(repo, version="v1.0")

    with pytest.raises(ApiProblem) as info:
        repo.get_admin_policy(first.id, "v9.9")

    assert info.value.status_code == 404


def test_expired_published_version_is_listed_as_closed(repo, database_url_schema):
    draft = create(
        repo,
        version="v1.0",
        publishedAt="2025-12-01",
        effectiveFrom="2026-01-01",
        effectiveTo="2026-06-30",
    )
    with psycopg.connect(database_url_schema) as conn:
        conn.execute(
            "UPDATE policy_version SET review_status = 'published', reviewed_at = now() "
            "WHERE revision = %s",
            (draft.revision,),
        )
        conn.commit()

    page = repo.list_admin_policies(status="closed", policy_type=None, cursor=None, limit=20)
    assert [item.status for item in page.items] == ["closed"]

    published = repo.list_admin_policies(
        status="published", policy_type=None, cursor=None, limit=20
    )
    assert published.items == []


def test_published_version_is_immutable(repo, database_url_schema):
    draft = create(repo, version="v1.0")
    with psycopg.connect(database_url_schema) as conn:
        conn.execute(
            "UPDATE policy_version SET review_status = 'published', reviewed_at = now() "
            "WHERE revision = %s",
            (draft.revision,),
        )
        conn.commit()

    for statement in (
        "UPDATE policy_version SET title = '改标题' WHERE revision = %s",
        "DELETE FROM policy_version WHERE revision = %s",
        "DELETE FROM policy_source WHERE policy_version_id = "
        "(SELECT id FROM policy_version WHERE revision = %s)",
    ):
        with pytest.raises(psycopg.errors.CheckViolation):
            with psycopg.connect(database_url_schema) as conn:
                conn.execute(statement, (draft.revision,))
                conn.commit()


def test_append_only_tables_reject_updates(repo, database_url_schema):
    create(repo)

    with pytest.raises(psycopg.errors.CheckViolation):
        with psycopg.connect(database_url_schema) as conn:
            conn.execute("UPDATE trace_step SET summary = '改写'")
            conn.commit()

    with pytest.raises(psycopg.errors.CheckViolation):
        with psycopg.connect(database_url_schema) as conn:
            conn.execute("DELETE FROM trace_step")
            conn.commit()


def test_schema_migration_row_is_idempotent(database_url_schema):
    rows = query_one(
        database_url_schema, "SELECT count(*) FROM schema_migration WHERE id = '001_init'"
    )
    assert rows[0] == 1
