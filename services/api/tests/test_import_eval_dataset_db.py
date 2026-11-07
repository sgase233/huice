"""导入器重跑的真实数据库用例（设置 ``HUICE_TEST_DATABASE_URL`` 时才跑）。

重新导入的三种状态只有在真库里才看得出来：建版、存条款、发布是三个事务，进程在
任意两步之间被杀都会留下一个能在库里读出来的中间态。这里把三种状态逐个造出来，
验证重跑是「跳过 / 续跑 / 拒绝」，而不是一律按文号跳过。
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

import psycopg
import pytest

from db_support import query_one

SCRIPT = Path(__file__).resolve().parents[3] / "infra" / "scripts" / "import_eval_dataset.py"

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("clean_tables")]


@pytest.fixture(scope="module")
def importer():
    spec = importlib.util.spec_from_file_location("import_eval_dataset_db", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


@pytest.fixture()
def plan(importer):
    """条件最少、没有跨文件来源的一版：跑得快，也不牵扯 P1-2 的出处问题。"""

    plans, _ = importer.build_plans()
    return next(item for item in plans if item.code == "CN-TAX-SME-2023-012")


@pytest.fixture(autouse=True)
def no_enterprises(importer, monkeypatch):
    """企业画像是另一条路径，这里只验政策版本的重跑。"""

    monkeypatch.setattr(importer, "_apply_enterprises", lambda repository, dsn: None)


def _import(importer, repo, dsn, plan) -> int:
    return importer._apply([plan], dsn, repository=repo)


def _create_draft(importer, repo, plan):
    return repo.create_policy_version(
        metadata=plan.metadata,
        sources=importer._prepared(plan),
        storage_keys=importer._storage_keys(plan),
        trace_id="test-interrupt",
    )


def test_连续跑两次只建一个版本(importer, repo, database_url_schema, plan):
    assert _import(importer, repo, database_url_schema, plan) == 0
    assert _import(importer, repo, database_url_schema, plan) == 0

    assert query_one(database_url_schema, "SELECT count(*) FROM policy_version")[0] == 1
    assert query_one(database_url_schema, "SELECT count(*) FROM policy_criterion")[0] == len(
        plan.criteria
    )
    assert query_one(database_url_schema, "SELECT review_status FROM policy_version")[0] == "published"


def test_建版后中断的重跑会续跑并发布(importer, repo, database_url_schema, plan):
    _create_draft(importer, repo, plan)
    assert query_one(database_url_schema, "SELECT review_status FROM policy_version")[0] == "pending_parse"
    assert query_one(database_url_schema, "SELECT count(*) FROM policy_criterion")[0] == 0

    assert _import(importer, repo, database_url_schema, plan) == 0

    assert query_one(database_url_schema, "SELECT count(*) FROM policy_version")[0] == 1
    assert query_one(database_url_schema, "SELECT review_status FROM policy_version")[0] == "published"
    assert query_one(database_url_schema, "SELECT count(*) FROM policy_criterion")[0] == len(
        plan.criteria
    )


def test_存条款后中断的重跑会重存并发布(importer, repo, database_url_schema, plan):
    """中断在「条款已存、还没发布」：续跑要把条款整版重写，而不是叠一份。"""

    draft = _create_draft(importer, repo, plan)
    repo.save_criteria(
        policy_id=draft.id,
        version=draft.version,
        expected_revision=draft.revision,
        criteria=[],
        findings=[],
        status="pending_review",
        trace_id="test-interrupt",
    )
    assert query_one(database_url_schema, "SELECT review_status FROM policy_version")[0] == "pending_review"

    assert _import(importer, repo, database_url_schema, plan) == 0

    assert query_one(database_url_schema, "SELECT review_status FROM policy_version")[0] == "published"
    assert query_one(database_url_schema, "SELECT count(*) FROM policy_criterion")[0] == len(
        plan.criteria
    )


def test_草稿的来源变过就拒绝而不是继续写(importer, repo, database_url_schema, plan):
    _create_draft(importer, repo, plan)
    with psycopg.connect(database_url_schema) as conn:
        conn.execute(
            "UPDATE policy_source SET content_hash = %s "
            "WHERE policy_version_id = (SELECT id FROM policy_version WHERE document_number = %s)",
            (hashlib.sha256("被换过的正文".encode()).hexdigest(), plan.document),
        )

    assert _import(importer, repo, database_url_schema, plan) == 1

    assert query_one(database_url_schema, "SELECT review_status FROM policy_version")[0] == "pending_parse"


def test_已发布但条件被删掉的版本重跑返回非零(importer, repo, database_url_schema, plan):
    assert _import(importer, repo, database_url_schema, plan) == 0
    with psycopg.connect(database_url_schema) as conn:
        conn.execute(
            "DELETE FROM policy_criterion WHERE criterion_id = ("
            "  SELECT criterion_id FROM policy_criterion ORDER BY criterion_id LIMIT 1)"
        )

    assert _import(importer, repo, database_url_schema, plan) == 1
