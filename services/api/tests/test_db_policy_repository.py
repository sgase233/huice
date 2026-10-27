"""仓储与库表的真实数据库用例。

仅在设置 ``HUICE_TEST_DATABASE_URL`` 时运行（本地可用 infra/local 的 Compose 实例）。
每个会话使用独立 schema，结束时删除，不会影响开发库里的数据。
"""

from __future__ import annotations

from uuid import uuid4

import psycopg
import pytest

from app.errors import ApiProblem
from db_support import create, query_all, query_one, source
from support import MIGRATIONS

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("clean_tables")]


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


def test_schema_migration_rows_are_idempotent(database_url_schema):
    ids = [row[0] for row in query_all(database_url_schema, "SELECT id FROM schema_migration ORDER BY id")]
    assert ids == [path.stem for path in MIGRATIONS]


# ------------------------------------------------------- 002 迁移后的结构


def test_idempotent_record_roundtrip(repo):
    key = uuid4()
    revision = uuid4()
    payload = {"draft": {"revision": str(revision), "status": "pending_review"}}

    assert repo.read_idempotent("extract", key) is None

    repo.save_idempotent("extract", key, "a" * 64, payload, revision)

    stored = repo.read_idempotent("extract", key)
    assert stored == {"fingerprint": "a" * 64, "response": payload, "revision": revision}
    # 作用域隔离：同一个键在别的接口下不算重放
    assert repo.read_idempotent("publish", key) is None


def test_idempotent_record_keeps_the_first_write_and_is_append_only(repo, database_url_schema):
    key = uuid4()
    repo.save_idempotent("extract", key, "b" * 64, {"n": 1}, uuid4())
    repo.save_idempotent("extract", key, "c" * 64, {"n": 2}, uuid4())

    stored = repo.read_idempotent("extract", key)
    assert stored["fingerprint"] == "b" * 64
    assert stored["response"] == {"n": 1}
    assert query_one(database_url_schema, "SELECT count(*) FROM idempotency_record")[0] == 1

    for statement in (
        "UPDATE idempotency_record SET revision = gen_random_uuid()",
        "DELETE FROM idempotency_record",
    ):
        with pytest.raises(psycopg.errors.CheckViolation):
            with psycopg.connect(database_url_schema) as conn:
                conn.execute(statement)
                conn.commit()


def test_enterprise_fact_uses_the_contract_source_enum(database_url_schema):
    """契约 FactSource 是 user_input / synthetic_material；verified_document 不再接受。"""

    enterprise_id = uuid4()
    with psycopg.connect(database_url_schema) as conn:
        conn.execute(
            "INSERT INTO enterprise (id, name_synthetic, period_start, period_end) "
            "VALUES (%s, %s, %s, %s)",
            (enterprise_id, "合成演示企业", "2026-01-01", "2026-12-31"),
        )
        conn.execute(
            "INSERT INTO enterprise_fact "
            "(enterprise_id, key, period_start, period_end, unit, source, value_amount) "
            "VALUES (%s, 'taxableIncome', '2026-01-01', '2026-12-31', 'CNY', "
            "'synthetic_material', 1200000)",
            (enterprise_id,),
        )
        conn.commit()

        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO enterprise_fact "
                "(enterprise_id, key, period_start, period_end, unit, source, value_amount) "
                "VALUES (%s, 'rdExpense', '2026-01-01', '2026-12-31', 'CNY', "
                "'verified_document', 500000)",
                (enterprise_id,),
            )
        conn.rollback()


def test_assessment_run_stores_period_and_supersedes(database_url_schema):
    enterprise_id, first_run, second_run = uuid4(), uuid4(), uuid4()
    with psycopg.connect(database_url_schema) as conn:
        conn.execute(
            "INSERT INTO enterprise (id, name_synthetic, period_start, period_end) "
            "VALUES (%s, %s, %s, %s)",
            (enterprise_id, "合成演示企业", "2026-01-01", "2026-12-31"),
        )
        insert_run = (
            "INSERT INTO assessment_run "
            "(id, enterprise_id, as_of, status, fact_version, model_or_rule_version, "
            " period_start, period_end, supersedes_run_id) "
            "VALUES (%s, %s, now(), 'queued', %s, 'rule:criteria@1', %s, %s, %s)"
        )
        conn.execute(
            insert_run,
            (first_run, enterprise_id, "1" * 64, "2026-01-01", "2026-12-31", None),
        )
        conn.execute(
            insert_run,
            (second_run, enterprise_id, "2" * 64, "2026-01-01", "2026-12-31", first_run),
        )
        conn.commit()

        period, supersedes = query_one(
            database_url_schema,
            "SELECT period_start, supersedes_run_id FROM assessment_run WHERE id = %s",
            (second_run,),
        )
        assert str(period) == "2026-01-01"
        assert supersedes == first_run

        # 期间与重算是运行快照的一部分，写入后冻结
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "UPDATE assessment_run SET period_end = '2027-12-31' WHERE id = %s",
                (second_run,),
            )
        conn.rollback()

        # status/finished_at 仍可推进
        conn.execute(
            "UPDATE assessment_run SET status = 'succeeded', finished_at = now() WHERE id = %s",
            (second_run,),
        )
        conn.commit()
