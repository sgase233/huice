"""两条链路之间的交接类型（契约见 ``AGENT_PROTOCOL.md``）。

``AgentContext`` 只放上下文：追踪号、判断时点、期间、版本图与预算；业务参数一律走
各自函数的入参（例如判断请求），不能把 ``enterpriseId``/``policyIds`` 塞进 context，
否则上下文会悄悄变成第二个请求体。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Mapping, Sequence
from uuid import UUID

from ..domain.rules import CriterionRule
from ..schemas import SourceSpan


@dataclass(frozen=True, slots=True)
class Budget:
    """一次请求的资源上限；模型调用按次数计，超出即降级为已存说明。"""

    max_model_calls: int = 2


@dataclass(frozen=True, slots=True)
class AgentContext:
    trace_id: str
    as_of: datetime
    period: tuple[date, date]
    policy_versions: Mapping[UUID, str] = field(default_factory=dict)
    fact_version: str = ""
    budget: Budget = field(default_factory=Budget)
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class SnapshotCriterion:
    """已审核条件在 run 里的快照形态。"""

    rule: CriterionRule
    spans: tuple[SourceSpan, ...] = ()


@dataclass(frozen=True, slots=True)
class PolicySnapshot:
    """一次判断用到的政策版本快照，只包含人工审核通过的版本。"""

    id: UUID
    version_id: UUID
    version: str
    title: str
    policy_type: str
    official_entry_url: str
    effective_to: date | None
    window_closes_at: datetime | None
    criteria: Sequence[SnapshotCriterion] = ()

    @property
    def rules(self) -> list[CriterionRule]:
        return [item.rule for item in self.criteria]

    def spans_of(self, criterion_id: Any) -> tuple[SourceSpan, ...]:
        for item in self.criteria:
            if item.rule.id == criterion_id:
                return item.spans
        return ()
