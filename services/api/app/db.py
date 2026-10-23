"""PostgreSQL connection pool shared by the API process.

Connections are opened lazily so the app can start while the database is down;
``/health`` then reports 503 instead of crashing the process at import time.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

from psycopg import Connection, OperationalError
from psycopg_pool import ConnectionPool, PoolTimeout

from .errors import StorageUnavailable

logger = logging.getLogger("huice.api")

STATEMENT_TIMEOUT_MS = 30_000


def _configure(conn: Connection) -> None:
    conn.execute("SET TIME ZONE 'UTC'")
    conn.execute(f"SET statement_timeout = {STATEMENT_TIMEOUT_MS}")


class Database:
    def __init__(self, dsn: str, *, max_size: int = 5, acquire_timeout: float = 5.0) -> None:
        self._dsn = dsn
        self._max_size = max_size
        self._acquire_timeout = acquire_timeout
        self._pool: ConnectionPool | None = None

    def _get_pool(self) -> ConnectionPool:
        if self._pool is None:
            try:
                self._pool = ConnectionPool(
                    conninfo=self._dsn,
                    min_size=0,
                    max_size=self._max_size,
                    open=False,
                    kwargs={"autocommit": True},
                    configure=_configure,
                    timeout=self._acquire_timeout,
                    name="huice-api",
                )
                self._pool.open(wait=False)
            except (OperationalError, ValueError) as exc:
                self._pool = None
                raise StorageUnavailable("数据库连接不可用") from exc
        return self._pool

    def ping(self, timeout: float = 2.0) -> bool:
        try:
            pool = self._get_pool()
            with pool.connection(timeout=timeout) as conn:
                conn.execute("SELECT 1")
            return True
        except Exception:
            return False

    @contextmanager
    def connection(self, timeout: float | None = None) -> Iterator[Connection]:
        pool = self._get_pool()
        try:
            conn = pool.getconn(timeout=timeout or self._acquire_timeout)
        except PoolTimeout as exc:
            raise StorageUnavailable("数据库连接不可用") from exc
        except (OperationalError, ValueError) as exc:
            raise StorageUnavailable("数据库连接不可用") from exc
        try:
            yield conn
        finally:
            pool.putconn(conn)

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            self._pool = None
