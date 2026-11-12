"""演示配额与建企业在同一个事务里：#61 review 第 2 条（P2）。

路由层不再「先占名额、建失败再退」——那种做法在进程退出或退额本身失败时会白扣一个
名额。现在 ``demo_quota`` 的计数与 ``enterprise`` 的 INSERT 在同一个事务里：占额失败
（429）连企业都不建，建企业失败（唯一索引、外键……）名额跟着回滚。

这里用真库验证四件事：同成、同败、过期不算、并发不超发。
"""

from __future__ import annotations

import hashlib
import threading
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
import pytest

from app.agents.profile import normalize_profile
from app.demo_quota import DemoQuota
from app.errors import ApiProblem
from app.schemas import EnterpriseInput

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("clean_tables")]

YEAR = {"startDate": "2026-01-01", "endDate": "2026-12-31"}
BODY = {
    "nameSynthetic": "合成企业Q",
    "period": YEAR,
    "facts": [
        {"key": "region", "value": "440300", "unit": "code", "period": YEAR, "source": "user_input"},
        {"key": "industry", "value": "F51", "unit": "code", "period": YEAR, "source": "user_input"},
    ],
}

PROFILE = normalize_profile(EnterpriseInput.model_validate(BODY))
SID_HASH = hashlib.sha256(b"demo-sid-db-0001").hexdigest()


def quota(limit: int = 20) -> DemoQuota:
    return DemoQuota(
        sid_hash=SID_HASH,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        limit=limit,
    )


def counts(dsn: str) -> tuple[int, int]:
    """(已建企业数, demo_quota.created)；没有配额行时计 0。"""

    with psycopg.connect(dsn) as conn:
        enterprises = conn.execute("SELECT count(*) FROM enterprise").fetchone()[0]
        row = conn.execute(
            "SELECT created FROM demo_quota WHERE sid_hash = %s", (SID_HASH,)
        ).fetchone()
    return enterprises, 0 if row is None else int(row[0])


def test_the_claim_and_the_enterprise_commit_together(repo, database_url_schema):
    repo.create_enterprise(
        profile=PROFILE,
        trace_id=str(uuid4()),
        owner_user_id=None,
        demo_quota=quota(limit=2),
    )

    assert counts(database_url_schema) == (1, 1)


def test_an_exhausted_quota_creates_no_enterprise(repo, database_url_schema):
    repo.create_enterprise(
        profile=PROFILE, trace_id=str(uuid4()), owner_user_id=None, demo_quota=quota(limit=1)
    )

    with pytest.raises(ApiProblem) as caught:
        repo.create_enterprise(
            profile=PROFILE, trace_id=str(uuid4()), owner_user_id=None, demo_quota=quota(limit=1)
        )

    assert caught.value.status_code == 429
    assert caught.value.code == "DEMO_QUOTA_EXCEEDED"
    assert caught.value.details["limit"] == 1
    # 占额失败发生在同一条语句里：企业没建、计数也没有被多加一次。
    assert counts(database_url_schema) == (1, 1)


def test_an_expired_quota_row_grants_nothing(repo, database_url_schema):
    """配额行过期后不再给名额：``expires_at > now()`` 写在占额语句的 WHERE 里。

    表在签发演示 cookie 时写入（``ensure_demo_quota``，``created = 0``），过期时间
    与 cookie 同一个值；这里模拟那一行已经过期、而调用方还拿着参数来的情形。
    """

    with psycopg.connect(database_url_schema) as conn:
        conn.execute(
            "INSERT INTO demo_quota (sid_hash, created, expires_at) "
            "VALUES (%s, 0, now() - interval '1 minute')",
            (SID_HASH,),
        )

    with pytest.raises(ApiProblem) as caught:
        repo.create_enterprise(
            profile=PROFILE,
            trace_id=str(uuid4()),
            owner_user_id=None,
            demo_quota=quota(limit=5),
        )

    assert caught.value.code == "DEMO_QUOTA_EXCEEDED"
    assert counts(database_url_schema) == (0, 0)


def test_a_failed_enterprise_insert_rolls_the_quota_back(repo, database_url_schema):
    """占额成功后任何一步失败，名额都跟着事务回滚——这里用不存在的外键触发失败。"""

    missing_owner = uuid4()

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        repo.create_enterprise(
            profile=PROFILE,
            trace_id=str(uuid4()),
            owner_user_id=missing_owner,
            demo_quota=quota(limit=5),
        )

    assert counts(database_url_schema) == (0, 0)


def test_concurrent_creates_never_exceed_the_limit(repo, database_url_schema):
    """8 个并发建样只放行 2 个：``INSERT … ON CONFLICT … WHERE`` 单语句占额不会超发。"""

    limit = 2
    attempts = 8
    start = threading.Barrier(attempts)
    outcomes: list[object] = []
    lock = threading.Lock()

    def worker() -> None:
        start.wait(10.0)
        try:
            result: object = repo.create_enterprise(
                profile=PROFILE,
                trace_id=str(uuid4()),
                owner_user_id=None,
                demo_quota=quota(limit=limit),
            )
        except BaseException as exc:  # 失败原因由下面的断言分辨
            with lock:
                outcomes.append(exc)
            return
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=worker) for _ in range(attempts)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30.0)

    created = [item for item in outcomes if not isinstance(item, BaseException)]
    rejected = [item for item in outcomes if isinstance(item, BaseException)]
    assert len(created) == limit, outcomes
    assert len(rejected) == attempts - limit, outcomes
    assert all(getattr(exc, "code", None) == "DEMO_QUOTA_EXCEEDED" for exc in rejected)
    assert counts(database_url_schema) == (limit, limit)
