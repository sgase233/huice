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
        except (PoolTimeout, OperationalError, ValueError) as exc:
            raise StorageUnavailable("数据库连接不可用") from exc
        try:
            try:
                yield conn
            except OperationalError as exc:
                # 拿到连接后才发现断连（被实例踢掉、网络中断、语句超时）同样是
                # 依赖不可用 → 503。SQL 语法/约束错误是 ProgrammingError /
                # IntegrityError，不会走进这里，仍按 500 处理。
                raise StorageUnavailable("数据库连接不可用") from exc
        finally:
            try:
                pool.putconn(conn)
            except Exception:  # noqa: BLE001 - 坏的连接可能归还失败，不能掩盖原始异常
                logger.debug("归还数据库连接失败，已忽略", exc_info=True)

    @contextmanager
    def dedicated_connection(self) -> Iterator[Connection]:
        """单开一条**不走池**的连接，供发现运行持 advisory lock 并承担本轮全部写入。

        E0 D3.1 要求「锁与写共用同一条连接」：池里的连接可能被归还、被别处复用、
        被池的健康检查替换，而会话级 advisory lock 跟着连接走。只有一条独立连接
        才能保证「连接断了 = 锁没了 = 未提交的写入一起没了」这个等式成立。

        这里不开事务：调用方自己用 ``conn.transaction()`` 划事务边界，运行器借此把
        「置旧候选过期 + 插运行行 + 推进 next_run_at」放进同一个事务。
        """

        try:
            conn = Connection.connect(self._dsn, autocommit=True)
        except (OperationalError, ValueError) as exc:
            raise StorageUnavailable("数据库连接不可用") from exc
        try:
            _configure(conn)
            try:
                yield conn
            except OperationalError as exc:
                # 与池连接路径同一口径：运行期间连接断开是依赖不可用，不是内部错误。
                raise StorageUnavailable("数据库连接不可用") from exc
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001 - 关闭失败不能盖掉本轮的真实结果
                logger.debug("关闭发现运行专用连接失败，已忽略", exc_info=True)

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            self._pool = None
