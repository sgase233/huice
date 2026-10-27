"""共用夹具。

默认全部用例不依赖数据库：路由挂载的仓储与对象存储被替换为内存实现。
``db`` 标记的用例需要设置 ``HUICE_TEST_DATABASE_URL``（指向可写的测试库）。
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

import psycopg

from app.config import Settings
from app.db import Database
from app.integrations.storage import PolicyRepository
from app.main import create_app
from support import ADMIN_TOKEN, FakeObjectStore, FakeRepository, MIGRATIONS, SCHEMA, with_schema

TEST_DATABASE_URL = "postgresql://huice:huice@127.0.0.1:5432/huice_test"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        app_env="local",
        api_host="127.0.0.1",
        api_port=8000,
        database_url=TEST_DATABASE_URL,
        cors_origins=("http://127.0.0.1:5173",),
        admin_token=ADMIN_TOKEN,
        demo_access_mode="local",
        object_store_mode="local",
        local_object_dir=".local-objects-test",
    )


@pytest.fixture
def app(settings):
    application = create_app(settings)
    application.state.repository = FakeRepository()
    application.state.object_store = FakeObjectStore()
    return application


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)


@pytest.fixture
def repository(app) -> FakeRepository:
    return app.state.repository


@pytest.fixture
def object_store(app) -> FakeObjectStore:
    return app.state.object_store


@pytest.fixture
def auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {ADMIN_TOKEN}"}


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get("HUICE_TEST_DATABASE_URL", "").strip()
    if not url:
        pytest.skip("未设置 HUICE_TEST_DATABASE_URL，跳过数据库用例")
    return url


@pytest.fixture(scope="module")
def database_url_schema(database_url) -> str:
    """给一个测试模块建独立 schema 并执行全部迁移（连跑两遍，验证可重复执行）。"""

    scripts = [path.read_text(encoding="utf-8") for path in MIGRATIONS]
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        admin.execute(f"CREATE SCHEMA {SCHEMA}")

    scoped = with_schema(database_url, SCHEMA)
    with psycopg.connect(scoped, autocommit=True) as conn:
        for script in scripts:
            conn.execute(script)
        for script in scripts:
            conn.execute(script)

    yield scoped

    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")


@pytest.fixture()
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
