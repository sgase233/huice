"""一个账号只能有一家企业：#61 review 第 3 条。

路由层先看 ``identity.enterprise_id`` 给出 409，但两个并发请求会同时看到 NULL——
真正挡住第二次写入的是 ``enterprise_owner_user_uidx`` 这个部分唯一索引。索引挡下来
的 ``UniqueViolation`` 必须翻成与串行路径**同一个** 409，不能漏成 500，也不能把
半条轨迹留在库里。
"""

from __future__ import annotations

import threading
from uuid import uuid4

import psycopg
import pytest

from app.agents.profile import normalize_profile
from app.errors import ApiProblem
from app.schemas import EnterpriseInput

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("clean_tables")]

YEAR = {"startDate": "2026-01-01", "endDate": "2026-12-31"}
BODY = {
    "nameSynthetic": "合成企业A",
    "period": YEAR,
    "facts": [
        {"key": "region", "value": "440300", "unit": "code", "period": YEAR, "source": "user_input"},
        {"key": "industry", "value": "F51", "unit": "code", "period": YEAR, "source": "user_input"},
    ],
}

PROFILE = normalize_profile(EnterpriseInput.model_validate(BODY))


def seed_user(dsn: str, username: str = "bind.one"):
    user_id = uuid4()
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "INSERT INTO app_user (id, username, password_hash, role) "
            "VALUES (%s, %s, %s, 'enterprise')",
            (user_id, username, "not-a-real-hash"),
        )
    return user_id


def test_the_second_create_for_the_same_owner_is_a_409(repo, database_url_schema):
    owner = seed_user(database_url_schema)
    first = repo.create_enterprise(profile=PROFILE, trace_id=str(uuid4()), owner_user_id=owner)

    with pytest.raises(ApiProblem) as caught:
        repo.create_enterprise(profile=PROFILE, trace_id=str(uuid4()), owner_user_id=owner)

    assert caught.value.status_code == 409
    assert caught.value.code == "ENTERPRISE_ALREADY_BOUND"
    assert caught.value.details["enterpriseId"] == str(first.id)


def test_the_failed_insert_leaves_no_trace_behind(repo, database_url_schema):
    """唯一索引撞车时整个事务回滚：不能留下一条不属于任何企业的 ``profile`` 轨迹。"""

    owner = seed_user(database_url_schema)
    repo.create_enterprise(profile=PROFILE, trace_id=str(uuid4()), owner_user_id=owner)

    with pytest.raises(ApiProblem):
        repo.create_enterprise(profile=PROFILE, trace_id=str(uuid4()), owner_user_id=owner)

    with psycopg.connect(database_url_schema) as conn:
        enterprises = conn.execute("SELECT count(*) FROM enterprise").fetchone()[0]
        stages = conn.execute(
            "SELECT count(*) FROM trace_step WHERE stage = 'profile'"
        ).fetchone()[0]
        owners = conn.execute(
            "SELECT count(*) FROM enterprise WHERE owner_user_id = %s", (owner,)
        ).fetchone()[0]
    assert (enterprises, owners, stages) == (1, 1, 1)


def test_two_concurrent_creates_produce_one_enterprise_and_one_409(repo, database_url_schema):
    owner = seed_user(database_url_schema)
    start = threading.Barrier(2)
    outcomes: list[object] = []
    lock = threading.Lock()

    def worker() -> None:
        start.wait(5.0)
        try:
            outcomes.append(
                repo.create_enterprise(profile=PROFILE, trace_id=str(uuid4()), owner_user_id=owner)
            )
        except BaseException as exc:  # 失败原因由下面的断言分辨
            with lock:
                outcomes.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10.0)

    assert len(outcomes) == 2
    created = [item for item in outcomes if not isinstance(item, BaseException)]
    rejected = [item for item in outcomes if isinstance(item, BaseException)]
    assert len(created) == 1, outcomes
    assert len(rejected) == 1, outcomes
    assert isinstance(rejected[0], ApiProblem)
    assert rejected[0].status_code == 409
    assert rejected[0].code == "ENTERPRISE_ALREADY_BOUND"
    assert rejected[0].details["enterpriseId"] == str(created[0].id)

    with psycopg.connect(database_url_schema) as conn:
        rows = conn.execute(
            "SELECT count(*) FROM enterprise WHERE owner_user_id = %s", (owner,)
        ).fetchone()[0]
    assert rows == 1


def test_an_unowned_enterprise_is_not_constrained(repo, database_url_schema):
    """demo 建的是无主样本：``owner_user_id IS NULL`` 不参与唯一比较，可以建多家。"""

    first = repo.create_enterprise(profile=PROFILE, trace_id=str(uuid4()), owner_user_id=None)
    second = repo.create_enterprise(profile=PROFILE, trace_id=str(uuid4()), owner_user_id=None)

    assert first.id != second.id
