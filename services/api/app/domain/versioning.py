"""失效事件与读取时有效性（FR-07）。

两条原则：

* **历史不可变**：run、事实快照、政策快照、解释正文写完就是历史，永远不改写。
* **有效性是读取视图**：``validity``/``staleReasons``/``validUntil`` 每次读取都按
  失效事件与当前时间重算，不回写四态结果，也不用「换缓存键」冒充失效记录。

失效事件只记「发生了什么」（政策换版、某条事实被改），不记哪个条件该变；条件级
影响在读的时候用 run 保存的依赖快照算出来，这样同一条事件只影响真正读过该事实的
政策，没读过的政策保持 ``current``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence
from uuid import UUID

#: 中国标准时间固定 +08:00（无夏令时），政策日期按北京时间解释。
SHANGHAI = timezone(timedelta(hours=8))

POLICY_VERSION_CHANGED = "policy_version_changed"
FACT_CHANGED = "fact_changed"

CURRENT = "current"
STALE = "stale"

FactRefKey = tuple[str, date, date]


@dataclass(frozen=True, slots=True)
class ChangedEvent:
    """一条失效事件。``fact_key`` 只在事实变更时有值。"""

    reason: str
    occurred_at: datetime
    policy_id: UUID | None = None
    fact_key: str | None = None


@dataclass(frozen=True, slots=True)
class PolicyBoundary:
    """政策版本的时间边界，全部按契约口径。"""

    effective_to: date | None = None
    window_closes_at: datetime | None = None


@dataclass(slots=True)
class ValidityView:
    validity: str = CURRENT
    stale_reasons: list[dict[str, Any]] = field(default_factory=list)

    def add(
        self,
        code: str,
        message: str,
        *,
        criterion_ids: Sequence[UUID] = (),
        fact_refs: Sequence[FactRefKey] = (),
    ) -> None:
        self.validity = STALE
        self.stale_reasons.append(
            {
                "code": code,
                "message": message,
                "changedCriterionIds": [str(item) for item in criterion_ids],
                "changedFactRefs": [fact_ref_json(ref) for ref in fact_refs],
            }
        )


def shanghai_date(moment: datetime) -> date:
    return moment.astimezone(SHANGHAI).date()


def valid_until(*, boundary: PolicyBoundary, now: datetime) -> datetime | None:
    """最早的一个**未来**边界；没有可靠边界时返回 ``None``。

    边界取「适用期结束次日零点（北京时间）」与「申报窗口关闭时刻」的最小值，且只
    保留晚于 ``now`` 的；已过边界不再作为有效期，改由 :func:`resolve_validity` 标 stale。
    """

    candidates: list[datetime] = []
    if boundary.effective_to is not None:
        candidates.append(
            datetime.combine(boundary.effective_to + timedelta(days=1), time.min, tzinfo=SHANGHAI)
        )
    if boundary.window_closes_at is not None:
        candidates.append(boundary.window_closes_at.astimezone(SHANGHAI))
    future = [item for item in candidates if item > now]
    return min(future) if future else None


def resolve_validity(
    *,
    boundary: PolicyBoundary,
    events: Iterable[ChangedEvent],
    criterion_dependencies: Mapping[UUID, Sequence[FactRefKey]],
    run_created_at: datetime,
    now: datetime,
    valid_until_at: datetime | None = None,
) -> ValidityView:
    """按事件与时间给出 ``validity`` 与中文原因。"""

    view = ValidityView()
    today = shanghai_date(now)

    relevant = [event for event in events if event.occurred_at > run_created_at]

    if any(event.reason == POLICY_VERSION_CHANGED for event in relevant):
        view.add(
            "policy_changed",
            "该政策已发布新版本，本结论基于旧版本，请重新判断。",
        )

    changed_keys = {
        event.fact_key for event in relevant if event.reason == FACT_CHANGED and event.fact_key
    }
    if changed_keys:
        affected = sorted(
            (
                criterion_id
                for criterion_id, refs in criterion_dependencies.items()
                if any(ref[0] in changed_keys for ref in refs)
            ),
            key=str,
        )
        affected_refs = [
            ref
            for criterion_id in affected
            for ref in criterion_dependencies.get(criterion_id, ())
            if ref[0] in changed_keys
        ]
        view.add(
            "fact_changed",
            "本次判断用到的企业事实已被修改，结论需要重算。",
            criterion_ids=affected,
            fact_refs=_unique(affected_refs),
        )

    if boundary.window_closes_at is not None and boundary.window_closes_at <= now:
        view.add("window_closed", "该政策的申报窗口已关闭，不能再提交申报。")

    if boundary.effective_to is not None and boundary.effective_to < today:
        view.add("policy_expired", "该政策已过有效期，仅用于历史展示。")

    if valid_until_at is not None and valid_until_at <= now:
        view.add("valid_until_passed", "该结论的有效期已过，请重新判断。")

    return view


def _unique(refs: Iterable[FactRefKey]) -> list[FactRefKey]:
    seen: list[FactRefKey] = []
    for ref in refs:
        if ref not in seen:
            seen.append(ref)
    return seen


def fact_ref_json(ref: FactRefKey) -> dict[str, Any]:
    key, start, end = ref
    return {
        "key": key,
        "period": {"startDate": start.isoformat(), "endDate": end.isoformat()},
    }
