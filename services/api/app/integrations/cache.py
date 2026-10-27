"""版本化求值缓存（FR-07）：只缓存「规则求值」，不缓存历史判断。

缓存键包含企业、政策、政策版本、企业事实版本、规则版本、期间与判断时点这七项，
外加缓存结构版本；任一项变化即换键，不把一个时间的结果套给另一个时间。

纪律（与 ``docs/IMPLEMENTATION_AND_INTERFACE.md`` §2.7 一致）：

* 命中只复用求值结果；``validity``/``staleReasons``/``validUntil`` 仍由仓储与编排器
  按当前时间重算，缓存里不存这些视图，也就不会把旧时点当成有效。
* 缓存不存 runId 与解释文本：runId 每次请求在 :func:`app.agents.orchestrator.assess`
  里新分配，历史 run 照常落库。
* Redis 未配置、连不上、缺客户端或值损坏，一律当作未命中重算；数据库不可用由仓储
  抛 ``StorageUnavailable`` 并按 503 返回，缓存不参与。
* 只换缓存键不能代替数据库的失效记录：失效裁定始终以 ``invalidation_event`` 为准。

缓存值是政策级四态与条件级依据的投影（无 UUID 字段）：恢复时按当前政策版本的条件
顺序逐条对回 ``criterion_id``，数量或版本不符即视为未命中，避免把 A 版本的条件结果
套到 B 版本上。
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime, timezone
from typing import Any, Protocol

from ..agents.contracts import PolicySnapshot
from ..config import Settings
from ..domain import rules

logger = logging.getLogger(__name__)

# 缓存值结构变化时改这里，旧键自然过期，不需要手动清理。
CACHE_SCHEMA_VERSION = "v1"
KEY_PREFIX = "huice:eval"
DEFAULT_TTL_SECONDS = 300

# 判断是同步请求路径，Redis 抖动不能拖住接口：连接与读写的超时都压到 0.5 秒，
# 超时即绕过重算。
_TIMEOUT_SECONDS = 0.5

_RESULTS = (rules.SATISFIED, rules.NOT_SATISFIED, rules.INSUFFICIENT, rules.CONFLICT)


class CacheBackend(Protocol):
    """缓存后端只需 get/set/close 三个动作。"""

    enabled: bool

    def get(self, key: str) -> str | None: ...

    def set(self, key: str, value: str) -> None: ...

    def close(self) -> None: ...


class NullCache:
    """未配置 ``REDIS_URL`` 时的替身：永远未命中，写入直接丢弃。"""

    enabled = False

    def get(self, key: str) -> str | None:
        return None

    def set(self, key: str, value: str) -> None:
        return None

    def close(self) -> None:
        return None


class RedisCache:
    """Redis 后端；任何故障都退化为「未命中」，不向调用方抛错。

    ``redis`` 客户端采用延迟导入：未安装时缓存自动绕过，但会在日志里提示一次。
    """

    enabled = True

    def __init__(self, url: str, *, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self._url = url
        self._ttl_seconds = ttl_seconds
        self._client: Any | None = None
        self._warned = False

    # ------------------------------------------------------------- 后端动作

    def get(self, key: str) -> str | None:
        client = self._connection()
        if client is None:
            return None
        try:
            value = client.get(key)
        except Exception:  # noqa: BLE001 - 缓存故障一律绕过，不打断判断
            self._degrade("读取失败")
            return None
        return value if isinstance(value, str) else None

    def set(self, key: str, value: str) -> None:
        client = self._connection()
        if client is None:
            return
        try:
            client.set(key, value, ex=self._ttl_seconds)
        except Exception:  # noqa: BLE001
            self._degrade("写入失败")

    def close(self) -> None:
        client, self._client = self._client, None
        if client is None:
            return
        try:
            client.close()
        except Exception:  # noqa: BLE001 - 关闭失败不影响退出
            pass

    # ------------------------------------------------------------- 内部

    def _connection(self) -> Any | None:
        if self._client is not None:
            return self._client
        try:
            import redis  # 延迟导入：未装客户端时整条链路退化为不缓存
        except ImportError:
            self._degrade("未安装 redis 客户端，判断缓存已绕过")
            return None
        try:
            self._client = redis.Redis.from_url(
                self._url,
                decode_responses=True,
                socket_connect_timeout=_TIMEOUT_SECONDS,
                socket_timeout=_TIMEOUT_SECONDS,
            )
        except Exception:  # noqa: BLE001 - 连接串不合法也按不可用处理
            self._degrade("连接参数不合法")
            return None
        return self._client

    def _degrade(self, reason: str) -> None:
        """记录一次降级并停用本实例，避免每个请求都去撞同一个故障。"""

        self.close()
        if not self._warned:
            self._warned = True
            logger.warning("判断缓存不可用（%s），本次及后续请求绕过缓存重算。", reason)


class EvaluationCache:
    """应用层门面：键构造、序列化与「失败即未命中」都在这里收口。"""

    def __init__(self, backend: CacheBackend) -> None:
        self.backend = backend

    @property
    def enabled(self) -> bool:
        return self.backend.enabled

    def lookup(
        self, *, key: str, snapshot: PolicySnapshot
    ) -> rules.PolicyEvaluation | None:
        """命中返回可用的求值；任何不一致都当未命中，交给规则引擎重算。"""

        raw = self.backend.get(key)
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
            return restore_evaluation(payload, snapshot=snapshot)
        except (KeyError, TypeError, ValueError):
            return None

    def remember(self, *, key: str, evaluation: rules.PolicyEvaluation) -> None:
        try:
            raw = json.dumps(
                dump_evaluation(evaluation), ensure_ascii=False, separators=(",", ":")
            )
        except (TypeError, ValueError):
            # 出现了不可 JSON 化的中间值：不写缓存，但判断结果照常返回。
            return
        self.backend.set(key, raw)

    def close(self) -> None:
        self.backend.close()


def build_cache(settings: Settings) -> EvaluationCache:
    """按配置选择后端：没配 ``REDIS_URL`` 就是纯计算，不引入缓存依赖。"""

    if not settings.redis_url:
        return EvaluationCache(NullCache())
    return EvaluationCache(RedisCache(settings.redis_url))


# ------------------------------------------------------------------ 键构造


def evaluation_key(
    *,
    enterprise_id: Any,
    policy_id: Any,
    policy_version: str,
    fact_version: str,
    rule_version: str,
    period: tuple[date, date],
    as_of: datetime,
    schema_version: str = CACHE_SCHEMA_VERSION,
) -> str:
    """七项版本化键 + 结构版本；字段名与文档 §2.7 一一对应。

    键用规范化 JSON 的 SHA-256，长度固定、不含企业名称等敏感信息；同一个键只可能
    对应同一组输入，因此命中不会漏掉期间或判断时点。
    """

    start, end = period
    material = {
        "enterpriseId": str(enterprise_id),
        "policyId": str(policy_id),
        "policyVersion": str(policy_version),
        "factVersion": str(fact_version),
        "ruleVersion": str(rule_version),
        "period": [start.isoformat(), end.isoformat()],
        "asOf": _instant(as_of),
    }
    canonical = json.dumps(
        material, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{KEY_PREFIX}:{schema_version}:{digest}"


def _instant(value: datetime) -> str:
    """判断时点统一到 UTC 微秒精度；不带时区的值按 UTC 解释，避免机器时区参与。"""

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


# ------------------------------------------------------------ 值的投影/恢复


def dump_evaluation(evaluation: rules.PolicyEvaluation) -> dict[str, Any]:
    """只投影可复用、且不随时间变化的部分；不带 runId、有效期与解释文本。"""

    return {
        "policyVersion": evaluation.version,
        "result": evaluation.result,
        "criteria": [
            {
                "result": criterion.result,
                "reason": criterion.reason,
                "factRefs": [_dump_ref(ref) for ref in criterion.fact_refs],
                "missing": [_dump_ref(ref) for ref in criterion.missing],
                "calculation": criterion.calculation,
            }
            for criterion in evaluation.criteria
        ],
        "dependencies": [_dump_ref(ref) for ref in evaluation.dependencies],
    }


def restore_evaluation(
    payload: dict[str, Any], *, snapshot: PolicySnapshot
) -> rules.PolicyEvaluation:
    """把缓存值对回当前政策版本；结构或结论不自洽时抛 ``ValueError``（即未命中）。"""

    if not isinstance(payload, dict):
        raise ValueError("缓存值不是对象")
    if payload.get("policyVersion") != snapshot.version:
        raise ValueError("缓存值属于其它政策版本")

    raw_criteria = payload.get("criteria")
    if not isinstance(raw_criteria, list) or len(raw_criteria) != len(snapshot.rules):
        raise ValueError("缓存值的条件数量与当前政策版本不一致")
    if not all(isinstance(item, dict) for item in raw_criteria):
        raise ValueError("缓存值的条件格式不合法")

    # criterion_id 不落缓存：按当前版本的条件顺序回填，保证与原文跨度（证据）对得上。
    criteria = [
        rules.CriterionEvaluation(
            criterion_id=rule.id,
            result=_check_result(item.get("result")),
            reason=str(item.get("reason") or ""),
            fact_refs=_refs(item.get("factRefs")),
            missing=_refs(item.get("missing")),
            calculation=item.get("calculation") or None,
        )
        for rule, item in zip(snapshot.rules, raw_criteria)
    ]

    result = _check_result(payload.get("result"))
    recomputed = rules.aggregate(
        rules.Evaluation(item.result, item.reason, item.fact_refs, item.missing)
        for item in criteria
    )
    if recomputed != result:
        # 结论与条件不自洽说明缓存值被改写或规则版本对不上，宁可重算。
        raise ValueError("缓存值的政策级结论与条件级结果不一致")

    return rules.PolicyEvaluation(
        policy_id=snapshot.id,
        version=snapshot.version,
        result=result,
        criteria=criteria,
        dependencies=_refs(payload.get("dependencies")),
    )


def _check_result(value: Any) -> str:
    if value not in _RESULTS:
        raise ValueError("缓存值出现未知的四态取值")
    return str(value)


def _dump_ref(ref: tuple[str, date, date]) -> list[str]:
    return [ref[0], ref[1].isoformat(), ref[2].isoformat()]


def _refs(value: Any) -> list[tuple[str, date, date]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("缓存值的事实引用不是数组")
    refs: list[tuple[str, date, date]] = []
    for item in value:
        if not isinstance(item, list) or len(item) != 3:
            raise ValueError("缓存值的事实引用格式不对")
        refs.append((str(item[0]), date.fromisoformat(item[1]), date.fromisoformat(item[2])))
    return refs
