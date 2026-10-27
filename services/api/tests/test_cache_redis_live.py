"""真实 Redis 上的键写入、TTL 与命中（可选用例）。

设置 ``HUICE_TEST_REDIS_URL`` 才运行（本地 compose 的 `redis://127.0.0.1:6379/0`
即可）。用例只读写自己产生的 `huice:eval:v1:` 键并在结束时删除，不 `FLUSHDB`，
因此可以指向有其它数据的库。
"""

from __future__ import annotations

import os

import pytest
import redis

from app.integrations.cache import EvaluationCache, RedisCache
from support import published_draft

pytestmark = [pytest.mark.redis]

YEAR = {"startDate": "2026-01-01", "endDate": "2026-12-31"}
AS_OF = "2026-10-26T15:00:00+08:00"

INDUSTRY_GATE = {
    "op": "industry_exclusion",
    "factKey": "industry",
    "period": "assessment",
    "excluded": ["F51", "F52"],
}

INCOME_GATE = {
    "op": "compare",
    "factKey": "taxableIncome",
    "period": "assessment",
    "comparator": "gte",
    "value": "3000000.00",
    "unit": "CNY",
}


@pytest.fixture(scope="module")
def redis_url() -> str:
    url = os.environ.get("HUICE_TEST_REDIS_URL", "").strip()
    if not url:
        pytest.skip("未设置 HUICE_TEST_REDIS_URL，跳过真实 Redis 用例")
    return url


@pytest.fixture()
def client_redis(redis_url):
    client = redis.Redis.from_url(redis_url, decode_responses=True)
    yield client
    # 只清掉本用例可能留下的缓存键，不动库里其它数据。
    for key in client.scan_iter("huice:eval:v1:*"):
        client.delete(key)
    client.close()


@pytest.fixture()
def wired(client, app, repository, redis_url, client_redis):
    app.state.cache = EvaluationCache(RedisCache(redis_url))
    policy = published_draft(expressions=[INDUSTRY_GATE, INCOME_GATE])
    repository.drafts.append(policy)
    enterprise = client.post("/api/v1/enterprises", json=_enterprise_payload()).json()
    return {
        "policy": policy,
        "enterprise": enterprise,
        "body": {
            "enterpriseId": enterprise["id"],
            "policyIds": [str(policy.id)],
            "asOf": AS_OF,
            "period": YEAR,
        },
    }


def _enterprise_payload() -> dict:
    return {
        "nameSynthetic": "合成企业A",
        "period": YEAR,
        "facts": [
            {"key": "region", "value": "440300", "unit": "code", "period": YEAR, "source": "user_input"},
            {"key": "industry", "value": "F51", "unit": "code", "period": YEAR, "source": "user_input"},
            {"key": "taxableIncome", "value": None, "unit": "CNY", "period": YEAR, "source": "user_input"},
        ],
    }


def test_first_request_writes_one_key_with_ttl(client, wired, client_redis) -> None:
    response = client.post("/api/v1/assessment-runs", json=wired["body"])

    assert response.status_code == 200, response.text
    keys = list(client_redis.scan_iter("huice:eval:v1:*"))
    assert len(keys) == 1
    ttl = client_redis.ttl(keys[0])
    assert 0 < ttl <= 300

    raw = client_redis.get(keys[0])
    # 缓存值只有求值投影：没有 runId、有效期视图与解释文本。
    assert '"runId"' not in raw and '"validity"' not in raw and '"explanation"' not in raw
    assert response.json()["id"] not in raw


def test_second_request_hits_cache_without_new_key(client, wired, client_redis) -> None:
    first = client.post("/api/v1/assessment-runs", json=wired["body"])
    second = client.post("/api/v1/assessment-runs", json=wired["body"])

    assert first.status_code == second.status_code == 200
    assert len(list(client_redis.scan_iter("huice:eval:v1:*"))) == 1
    assert first.json()["id"] != second.json()["id"]  # 命中也要新 runId
    assert second.json()["assessments"][0]["result"] == "not_satisfied"


def test_changed_fact_adds_a_second_key(client, wired, client_redis) -> None:
    client.post("/api/v1/assessment-runs", json=wired["body"])
    enterprise = wired["enterprise"]

    patched = client.patch(
        f"/api/v1/enterprises/{enterprise['id']}/facts/taxableIncome",
        json={"value": "3500000.00", "unit": "CNY", "period": YEAR, "source": "user_input"},
        headers={"If-Match": f'"{enterprise["factVersion"]}"'},
    )
    assert patched.status_code == 200, patched.text
    client.post("/api/v1/assessment-runs", json=wired["body"])

    assert len(list(client_redis.scan_iter("huice:eval:v1:*"))) == 2
