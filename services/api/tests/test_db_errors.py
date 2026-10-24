"""连接池的错误映射：拿连接后断连 → 503，SQL 编程错误仍按 500 冒泡。"""

from __future__ import annotations

import pytest
from psycopg import OperationalError, ProgrammingError

from app.db import Database
from app.errors import StorageUnavailable

DSN = "postgresql://huice:huice@127.0.0.1:5432/huice_test"


class FakeConnection:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def execute(self, *args, **kwargs):
        raise self._error


class FakePool:
    def __init__(self, connection) -> None:
        self._connection = connection
        self.returned: list[object] = []

    def getconn(self, timeout=None):
        return self._connection

    def putconn(self, connection) -> None:
        self.returned.append(connection)


def database_with(connection) -> tuple[Database, FakePool]:
    database = Database(DSN)
    pool = FakePool(connection)
    database._pool = pool  # 绕开真实连接池，只验证错误映射
    return database, pool


def test_disconnect_after_acquire_becomes_storage_unavailable():
    database, _ = database_with(FakeConnection(OperationalError("connection lost")))

    with pytest.raises(StorageUnavailable):
        with database.connection() as conn:
            conn.execute("SELECT 1")


def test_programming_error_is_not_masked():
    database, _ = database_with(FakeConnection(ProgrammingError("syntax error")))

    with pytest.raises(ProgrammingError):
        with database.connection() as conn:
            conn.execute("SELEC 1")


def test_connection_is_returned_even_when_the_body_fails():
    database, pool = database_with(FakeConnection(OperationalError("lost")))

    with pytest.raises(StorageUnavailable):
        with database.connection():
            raise OperationalError("连接在事务中中断")

    assert len(pool.returned) == 1


def test_return_to_pool_failure_does_not_mask_the_original_error():
    class ExplodingPool(FakePool):
        def putconn(self, connection) -> None:
            raise RuntimeError("pool already closed")

    database = Database(DSN)
    database._pool = ExplodingPool(FakeConnection(OperationalError("lost")))

    with pytest.raises(StorageUnavailable):
        with database.connection() as conn:
            conn.execute("SELECT 1")
