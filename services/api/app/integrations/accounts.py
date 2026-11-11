"""账号、会话与演示配额的仓储（E1，#46）。

与 :mod:`app.integrations.storage` 分开：政策/画像/运行是业务数据，账号与会话是
凭据数据，两者的读路径几乎不重叠，混在一个类里只会让「谁能写什么」更难看清。

三条不变量：

1. **注册是一个事务**：账号与（企业用户必填的）企业同成同败，失败不留半个账号。
   并发同名靠 ``app_user.username`` 唯一约束，不靠先查后插。
2. **会话只存摘要**：``token_hash`` 是 Cookie 明文的 sha256；登出写 ``revoked_at``
   而不是删行——「被登出」与「从来没存在过」必须可区分。
3. **演示配额一条语句完成首建与计数**：``INSERT … ON CONFLICT DO UPDATE … WHERE``，
   并发下不会两个请求都拿到第 20 个名额。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row

from ..agents.profile import NormalizedProfile
from ..db import Database
from ..errors import ApiProblem
from .storage import insert_enterprise_profile

_USER_COLUMNS = "id, username, password_hash, role, status, created_at, updated_at"

_SESSION_QUERY = """
SELECT s.id, s.user_id, s.expires_at, s.last_seen_at, s.revoked_at,
       u.username, u.role, u.status AS user_status,
       (SELECT e.id FROM enterprise e WHERE e.owner_user_id = u.id) AS enterprise_id
FROM user_session s
JOIN app_user u ON u.id = s.user_id
WHERE s.token_hash = %s
"""


class AccountsRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    # ----------------------------------------------------------------- 账号

    def find_user_by_username(self, username: str) -> dict[str, Any] | None:
        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"SELECT {_USER_COLUMNS} FROM app_user WHERE username = %s", (username,)
            )
            return cur.fetchone()

    def get_user(self, user_id: UUID) -> dict[str, Any] | None:
        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(f"SELECT {_USER_COLUMNS} FROM app_user WHERE id = %s", (user_id,))
            return cur.fetchone()

    def register(
        self,
        *,
        username: str,
        password_hash: str,
        role: str,
        profile: NormalizedProfile | None,
    ) -> dict[str, Any]:
        """建账号，并在同一个事务里建企业（有 ``profile`` 时）并绑定归属。"""

        user_id = uuid4()
        enterprise_id = uuid4() if profile is not None else None
        try:
            with self._db.connection() as conn, conn.transaction():
                conn.execute(
                    "INSERT INTO app_user (id, username, password_hash, role) "
                    "VALUES (%s, %s, %s, %s)",
                    (user_id, username, password_hash, role),
                )
                if profile is not None and enterprise_id is not None:
                    insert_enterprise_profile(
                        conn,
                        enterprise_id=enterprise_id,
                        profile=profile,
                        owner_user_id=user_id,
                    )
        except UniqueViolation as exc:
            # 唯一冲突只可能来自用户名：企业是本次事务新建的，owner 唯一索引不会撞。
            raise ApiProblem(409, "USERNAME_TAKEN", "该用户名已被占用，请换一个。") from exc
        return {
            "id": user_id,
            "username": username,
            "role": role,
            "enterprise_id": enterprise_id,
        }

    def enterprise_id_for_user(self, user_id: UUID) -> UUID | None:
        """账号绑定的企业（至多一个，由 ``owner_user_id`` 的部分唯一索引保证）。"""

        with self._db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM enterprise WHERE owner_user_id = %s", (user_id,)
            )
            row = cur.fetchone()
        return None if row is None else row[0]

    def update_password(self, *, user_id: UUID, password_hash: str) -> None:
        with self._db.connection() as conn, conn.transaction():
            conn.execute(
                "UPDATE app_user SET password_hash = %s, updated_at = now() WHERE id = %s",
                (password_hash, user_id),
            )
            conn.execute(
                "UPDATE user_session SET revoked_at = now() "
                "WHERE user_id = %s AND revoked_at IS NULL",
                (user_id,),
            )

    # ----------------------------------------------------------------- 会话

    def create_session(
        self,
        *,
        user_id: UUID,
        token_hash: str,
        csrf_hash: str,
        created_at: datetime,
        expires_at: datetime,
    ) -> UUID:
        session_id = uuid4()
        with self._db.connection() as conn, conn.transaction():
            conn.execute(
                "INSERT INTO user_session "
                "(id, user_id, token_hash, csrf_hash, created_at, last_seen_at, expires_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    session_id,
                    user_id,
                    token_hash,
                    csrf_hash,
                    created_at,
                    created_at,
                    expires_at,
                ),
            )
        return session_id

    def find_session(self, token_hash: str) -> dict[str, Any] | None:
        with self._db.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(_SESSION_QUERY, (token_hash,))
            return cur.fetchone()

    def touch_session(self, session_id: UUID, seen_at: datetime) -> None:
        with self._db.connection() as conn, conn.transaction():
            conn.execute(
                "UPDATE user_session SET last_seen_at = %s "
                "WHERE id = %s AND revoked_at IS NULL",
                (seen_at, session_id),
            )

    def revoke_session(self, session_id: UUID, *, at: datetime) -> None:
        with self._db.connection() as conn, conn.transaction():
            conn.execute(
                "UPDATE user_session SET revoked_at = %s "
                "WHERE id = %s AND revoked_at IS NULL",
                (at, session_id),
            )

    # ------------------------------------------------------------- 演示配额

    def ensure_demo_quota(self, *, sid_hash: str, expires_at: datetime) -> None:
        """签发 demo cookie 时写一行（``created = 0``），已存在则不动。"""

        with self._db.connection() as conn, conn.transaction():
            conn.execute(
                "INSERT INTO demo_quota (sid_hash, created, expires_at) "
                "VALUES (%s, 0, %s) ON CONFLICT (sid_hash) DO NOTHING",
                (sid_hash, expires_at),
            )

    def claim_demo_sample(
        self, *, sid_hash: str, expires_at: datetime, limit: int
    ) -> int | None:
        """占用一个建样名额；返回占用后的计数，无名额（或已过期）时返回 ``None``。"""

        with self._db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO demo_quota (sid_hash, created, expires_at)
                VALUES (%(sid)s, 1, %(expires)s)
                ON CONFLICT (sid_hash) DO UPDATE
                   SET created = demo_quota.created + 1
                 WHERE demo_quota.created < %(limit)s
                   AND demo_quota.expires_at > now()
                RETURNING created
                """,
                {"sid": sid_hash, "expires": expires_at, "limit": limit},
            )
            row = cur.fetchone()
        return None if row is None else int(row[0])
