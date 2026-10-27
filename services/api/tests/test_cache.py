"""版本化求值缓存（FR-07）。

三条验收口径：

1. 键包含企业/政策/政策版本/事实版本/规则版本/期间/判断时点，任一项变化即换键；
2. 命中只复用规则求值，runId 每次新分配、有效期仍按当前时间重算；
3. Redis 不可用（缺客户端、读写报错）一律绕过重算，数据库故障不被缓存掩盖。
"""

from __future__ import annotations

import dataclasses
import json
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app.agents.contracts import PolicySnapshot
from app.domain import rules
from app.errors import StorageUnavailable
from app.integrations.cache import (
    CACHE_SCHEMA_VERSION,
    EvaluationCache,
    NullCache,
    RedisCache,
    build_cache,
    dump_evaluation,
    evaluation_key,
    restore_evaluation,
)
from support import _snapshot_of, published_draft

YEAR = {"startDate": "2026-01-01", "endDate": "2026-12-31"}
AS_OF = "2026-10-23T10:00:00+08:00"

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

ENTERPRISE_ID = UUID("7b1d2f4e-0000-4000-8000-000000000001")
POLICY_ID = UUID("7b1d2f4e-0000-4000-8000-0000000000aa")
FACT_VERSION = "a" * 64
PERIOD = (date(2026, 1, 1), date(2026, 12, 31))
AS_OF_INSTANT = datetime(2026, 10, 23, 2, 0, tzinfo=timezone.utc)


def key_material(**overrides) -> dict:
    material = {
        "enterprise_id": ENTERPRISE_ID,
        "policy_id": POLICY_ID,
        "policy_version": "v1.0",
        "fact_version": FACT_VERSION,
        "rule_version": rules.RULE_VERSION,
        "period": PERIOD,
        "as_of": AS_OF_INSTANT,
    }
    material.update(overrides)
    return material


# --------------------------------------------------------------- 键构造


def test_key_is_deterministic_and_versioned() -> None:
    first = evaluation_key(**key_material())
    second = evaluation_key(**key_material())

    prefix = "huice:eval:" + CACHE_SCHEMA_VERSION + ":"
    assert first == second
    assert first.startswith(prefix)
    digest = first[len(prefix) :]
    assert len(digest) == 64
    assert set(digest) <= set("0123456789abcdef")
    # 键里不出现企业/政策标识，避免敏感信息进 Redis 键名。
    assert str(ENTERPRISE_ID) not in first and str(POLICY_ID) not in first


def test_every_key_field_changes_the_key() -> None:
    baseline = evaluation_key(**key_material())
    variants = {
        "enterpriseId": {"enterprise_id": uuid4()},
        "policyId": {"policy_id": uuid4()},
        "policyVersion": {"policy_version": "v1.1"},
        "factVersion": {"fact_version": "b" * 64},
        "ruleVersion": {"rule_version": "rule:criteria@2"},
        "period": {"period": (date(2026, 1, 1), date(2027, 12, 31))},
        "asOf": {"as_of": AS_OF_INSTANT + timedelta(seconds=1)},
    }
    produced = {name: evaluation_key(**key_material(**extra)) for name, extra in variants.items()}

    assert len(set(produced.values())) == len(variants)
    assert baseline not in produced.values()


def test_key_normalizes_equivalent_instants() -> None:
    shanghai = datetime(2026, 10, 23, 10, 0, tzinfo=timezone(timedelta(hours=8)))
    naive_utc = datetime(2026, 10, 23, 2, 0)

    assert (
        evaluation_key(**key_material(as_of=shanghai))
        == evaluation_key(**key_material())
        == evaluation_key(**key_material(as_of=naive_utc))
    )


def test_key_differs_on_timezone_shift() -> None:
    # 同一串「本地时间」但真实时刻不同，必须换键：不能把 09:00 的结果套给 10:00。
    shanghai_shifted = datetime(2026, 10, 23, 11, 0, tzinfo=timezone(timedelta(hours=8)))
    assert evaluation_key(**key_material(as_of=shanghai_shifted)) != evaluation_key(**key_material())


# ------------------------------------------------------------ 值投影/恢复


@pytest.fixture()
def snapshot() -> PolicySnapshot:
    draft = published_draft(expressions=[INDUSTRY_GATE, INCOME_GATE])
    return _snapshot_of(draft)


def sample_evaluation(snapshot: PolicySnapshot) -> rules.PolicyEvaluation:
    """两条条件：一条成立、一条缺事实，政策级结论应为「证据不足」。"""

    return rules.PolicyEvaluation(
        policy_id=snapshot.id,
        version=snapshot.version,
        result=rules.INSUFFICIENT,
        criteria=[
            rules.CriterionEvaluation(
                criterion_id=snapshot.rules[0].id,
                result=rules.SATISFIED,
                reason="条件已成立。",
                fact_refs=[("taxableIncome", PERIOD[0], PERIOD[1])],
                missing=[],
            ),
            rules.CriterionEvaluation(
                criterion_id=snapshot.rules[1].id,
                result=rules.INSUFFICIENT,
                reason="缺少事实：employeeCount。",
                fact_refs=[],
                missing=[("employeeCount", PERIOD[0], PERIOD[1])],
                calculation={
                    "metric": "employeeCount",
                    "average": "12.000000",
                    "inputs": [{"quarter": "2026-01-01/2026-03-31", "start": "10", "end": "14"}],
                },
            ),
        ],
        dependencies=[("taxableIncome", PERIOD[0], PERIOD[1])],
    )


def test_dump_and_restore_round_trip(snapshot: PolicySnapshot) -> None:
    original = sample_evaluation(snapshot)

    restored = restore_evaluation(json.loads(json.dumps(dump_evaluation(original))), snapshot=snapshot)

    assert restored.policy_id == snapshot.id
    assert restored.version == snapshot.version
    assert restored.result == original.result
    assert restored.dependencies == original.dependencies
    assert [item.criterion_id for item in restored.criteria] == [rule.id for rule in snapshot.rules]
    assert [item.result for item in restored.criteria] == [item.result for item in original.criteria]
    assert [item.reason for item in restored.criteria] == [item.reason for item in original.criteria]
    assert [item.missing for item in restored.criteria] == [item.missing for item in original.criteria]
    assert restored.criteria[1].calculation == original.criteria[1].calculation
    # 条件 ID 由当前政策版本回填，缓存值里不保存 ID。
    assert "criterionId" not in json.dumps(dump_evaluation(original))


def test_dump_keeps_no_run_identity_or_validity(snapshot: PolicySnapshot) -> None:
    payload = dump_evaluation(sample_evaluation(snapshot))

    assert set(payload) == {"policyVersion", "result", "criteria", "dependencies"}
    for forbidden in ("runId", "validity", "staleReasons", "validUntil", "explanation"):
        assert forbidden not in json.dumps(payload)


def test_restore_rejects_other_policy_version(snapshot: PolicySnapshot) -> None:
    payload = dump_evaluation(sample_evaluation(snapshot))
    payload["policyVersion"] = "v9.9"

    with pytest.raises(ValueError):
        restore_evaluation(payload, snapshot=snapshot)


def test_restore_rejects_criterion_count_mismatch(snapshot: PolicySnapshot) -> None:
    payload = dump_evaluation(sample_evaluation(snapshot))
    payload["criteria"] = payload["criteria"][:1]

    with pytest.raises(ValueError):
        restore_evaluation(payload, snapshot=snapshot)


def test_restore_rejects_inconsistent_policy_result(snapshot: PolicySnapshot) -> None:
    payload = dump_evaluation(sample_evaluation(snapshot))
    payload["result"] = rules.SATISFIED  # 仍有一条 insuffient，自洽性检查应拒绝

    with pytest.raises(ValueError):
        restore_evaluation(payload, snapshot=snapshot)


def test_restore_rejects_unknown_result_value(snapshot: PolicySnapshot) -> None:
    payload = dump_evaluation(sample_evaluation(snapshot))
    payload["criteria"][0]["result"] = "maybe"

    with pytest.raises(ValueError):
        restore_evaluation(payload, snapshot=snapshot)


# ------------------------------------------------------------- 后端与门面


class RecordingBackend:
    enabled = True

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.gets: list[str] = []
        self.sets: list[str] = []
        self.closed = False

    def get(self, key: str) -> str | None:
        self.gets.append(key)
        return self.values.get(key)

    def set(self, key: str, value: str) -> None:
        self.sets.append(key)
        self.values[key] = value

    def close(self) -> None:
        self.closed = True


def test_null_cache_never_hits_and_drops_writes(snapshot: PolicySnapshot) -> None:
    cache = EvaluationCache(NullCache())
    key = evaluation_key(**key_material())

    assert cache.enabled is False
    cache.remember(key=key, evaluation=sample_evaluation(snapshot))
    assert cache.lookup(key=key, snapshot=snapshot) is None


def test_build_cache_uses_null_backend_without_redis_url(settings) -> None:
    assert build_cache(settings).enabled is False


def test_build_cache_uses_redis_backend_with_url(settings) -> None:
    configured = dataclasses.replace(settings, redis_url="redis://127.0.0.1:6379/0")

    cache = build_cache(configured)

    assert cache.enabled is True
    assert isinstance(cache.backend, RedisCache)


def test_lookup_treats_corrupt_values_as_miss(snapshot: PolicySnapshot) -> None:
    backend = RecordingBackend()
    cache = EvaluationCache(backend)
    key = evaluation_key(**key_material())

    for raw in ("not-json", "[]", json.dumps({"policyVersion": "v0.0"})):
        backend.values[key] = raw
        assert cache.lookup(key=key, snapshot=snapshot) is None

    value = dump_evaluation(sample_evaluation(snapshot))
    backend.values[key] = json.dumps(value)
    assert cache.lookup(key=key, snapshot=snapshot) is not None


def test_remember_skips_unserializable_payload(snapshot: PolicySnapshot) -> None:
    backend = RecordingBackend()
    cache = EvaluationCache(backend)
    broken = sample_evaluation(snapshot)
    broken.criteria[1].calculation = {"average": object()}  # 不可 JSON 化

    cache.remember(key=evaluation_key(**key_material()), evaluation=broken)

    assert backend.sets == []


def test_redis_backend_degrades_when_client_missing(monkeypatch, caplog) -> None:
    monkeypatch.setitem(sys.modules, "redis", None)  # 等价于未安装客户端
    backend = RedisCache("redis://127.0.0.1:6379/0")

    with caplog.at_level(logging.WARNING):
        assert backend.get("k") is None
        backend.set("k", "v")  # 不抛错
        assert backend.get("k") is None  # 不再反复撞同一个故障

    assert "判断缓存不可用" in caplog.text
    # 日志里不能出现连接串。
    assert "127.0.0.1" not in caplog.text


class ExplodingClient:
    def __init__(self) -> None:
        self.closed = False

    def get(self, key: str):
        raise RuntimeError("boom")

    def set(self, key: str, value: str, ex: int | None = None):
        raise RuntimeError("boom")

    def close(self) -> None:
        self.closed = True


def test_redis_read_error_bypasses_and_closes(monkeypatch, caplog) -> None:
    monkeypatch.setitem(sys.modules, "redis", None)
    backend = RedisCache("redis://127.0.0.1:6379/0")
    client = ExplodingClient()
    backend._client = client

    with caplog.at_level(logging.WARNING):
        assert backend.get("k") is None

    assert client.closed is True
    assert "读取失败" in caplog.text


def test_unreachable_endpoint_degrades_with_real_client(caplog) -> None:
    """端口 1 必然连不上：用真实客户端库走一遍「连不上就绕过」，不依赖外部服务。"""

    backend = RedisCache("redis://127.0.0.1:1/0")

    with caplog.at_level(logging.WARNING):
        assert backend.get("k") is None
        backend.set("k", "v")  # 不抛错

    ours = [record.getMessage() for record in caplog.records if record.name == "app.integrations.cache"]
    assert any("判断缓存不可用" in message for message in ours)
    assert all("127.0.0.1" not in message for message in ours)  # 连接串不进日志


# --------------------------------------------------------------- 编排接线


def enterprise_payload() -> dict:
    return {
        "nameSynthetic": "合成企业A",
        "period": YEAR,
        "facts": [
            {"key": "region", "value": "440300", "unit": "code", "period": YEAR, "source": "user_input"},
            {"key": "industry", "value": "F51", "unit": "code", "period": YEAR, "source": "user_input"},
            {"key": "taxableIncome", "value": None, "unit": "CNY", "period": YEAR, "source": "user_input"},
        ],
    }


def test_cache_hit_still_allocates_new_run_id(client, app, repository) -> None:
    backend = RecordingBackend()
    app.state.cache = EvaluationCache(backend)

    policy = published_draft(expressions=[INDUSTRY_GATE, INCOME_GATE])
    repository.drafts.append(policy)
    enterprise = client.post("/api/v1/enterprises", json=enterprise_payload()).json()
    body = {
        "enterpriseId": enterprise["id"],
        "policyIds": [str(policy.id)],
        "asOf": AS_OF,
        "period": YEAR,
    }

    first = client.post("/api/v1/assessment-runs", json=body)
    second = client.post("/api/v1/assessment-runs", json=body)

    assert first.status_code == second.status_code == 200, first.text
    assert len(backend.sets) == 1  # 第二次命中，没有再次写入
    assert backend.gets[-1] == backend.sets[0]

    one, two = first.json(), second.json()
    assert one["id"] != two["id"]  # 缓存命中也要新 runId
    assert one["ruleVersion"] == two["ruleVersion"] == rules.RULE_VERSION
    assert one["factVersion"] == two["factVersion"]
    # 有效期与四态仍按当前时间重算，不在缓存里。
    assert two["assessments"][0]["validity"] == "current"
    assert two["assessments"][0]["result"] == one["assessments"][0]["result"] == "not_satisfied"
    assert two["assessments"][0]["criteriaResults"] == one["assessments"][0]["criteriaResults"]

    evaluation_step = next(
        step for step in repository.saved_runs[-1]["trace_steps"] if step.stage == "evaluation"
    )
    assert "复用缓存求值" in evaluation_step.output_summary


def test_changed_fact_version_produces_new_key(client, app, repository) -> None:
    backend = RecordingBackend()
    app.state.cache = EvaluationCache(backend)

    policy = published_draft(expressions=[INDUSTRY_GATE])
    repository.drafts.append(policy)
    enterprise = client.post("/api/v1/enterprises", json=enterprise_payload()).json()
    body = {
        "enterpriseId": enterprise["id"],
        "policyIds": [str(policy.id)],
        "asOf": AS_OF,
        "period": YEAR,
    }
    client.post("/api/v1/assessment-runs", json=body)

    # 事实变化 → factVersion 变化 → 新键，旧缓存不得复用。
    update = {"value": "3500000.00", "unit": "CNY", "period": YEAR, "source": "user_input"}
    patched = client.patch(
        f"/api/v1/enterprises/{enterprise['id']}/facts/taxableIncome",
        json=update,
        headers={"If-Match": f'"{enterprise["factVersion"]}"'},
    )
    assert patched.status_code == 200, patched.text
    client.post("/api/v1/assessment-runs", json=body)

    assert len(backend.sets) == 2
    assert backend.gets[-1] != backend.gets[0]


def test_database_failure_is_not_masked_by_cache(client, app, repository, monkeypatch) -> None:
    backend = RecordingBackend()
    app.state.cache = EvaluationCache(backend)
    policy = published_draft(expressions=[INDUSTRY_GATE])
    repository.drafts.append(policy)

    def unavailable(*args, **kwargs):
        raise StorageUnavailable()

    monkeypatch.setattr(repository, "get_enterprise", unavailable)
    body = {
        "enterpriseId": str(uuid4()),
        "policyIds": [str(policy.id)],
        "asOf": AS_OF,
        "period": YEAR,
    }

    response = client.post("/api/v1/assessment-runs", json=body)

    assert response.status_code == 503
    assert backend.gets == []  # 数据库不可用时压根不进缓存
