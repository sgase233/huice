"""在线轨迹（FR-09）。

``TraceStep`` 记录 stage 序列，供验收逐段核对。摘要只写「做了什么、影响了哪些对象」，
绝不写提示词、密钥、连接串或企业原始资料；``evidenceRefs`` 只放结构化引用。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Sequence
from uuid import UUID

STAGES = (
    "ingestion",
    "extraction",
    "citation_validation",
    "human_review",
    "profile",
    "retrieval",
    "evaluation",
    "questioning",
    "explanation",
)

STATUSES = ("ok", "needs_review", "retryable_error", "fatal_error")

MAX_SUMMARY = 200


@dataclass(frozen=True, slots=True)
class TraceStep:
    sequence: int
    stage: str
    status: str
    input_summary: str
    output_summary: str
    evidence_refs: list[dict[str, Any]] = field(default_factory=list)
    policy_id: UUID | None = None
    policy_version: str | None = None
    model_or_rule_version: str = ""
    elapsed_ms: int = 0
    error_code: str | None = None
    human_action: str | None = None

    def to_wire(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "stage": self.stage,
            "status": self.status,
            "inputSummary": self.input_summary,
            "outputSummary": self.output_summary,
            "evidenceRefs": self.evidence_refs,
            "policyId": str(self.policy_id) if self.policy_id else None,
            "policyVersion": self.policy_version,
            "modelOrRuleVersion": self.model_or_rule_version,
            "elapsedMs": self.elapsed_ms,
            "errorCode": self.error_code,
            "humanAction": self.human_action,
        }


class TraceRecorder:
    """按调用顺序累计步骤；``stage`` 必须是契约枚举，摘要自动截断。"""

    def __init__(self, *, trace_id: str, model_or_rule_version: str) -> None:
        self.trace_id = trace_id
        self._rule_version = model_or_rule_version
        self._steps: list[TraceStep] = []
        self._started = time.monotonic()

    def add(
        self,
        stage: str,
        *,
        status: str = "ok",
        input_summary: str,
        output_summary: str,
        evidence_refs: Sequence[dict[str, Any]] = (),
        policy_id: UUID | None = None,
        policy_version: str | None = None,
        error_code: str | None = None,
        human_action: str | None = None,
    ) -> TraceStep:
        if stage not in STAGES:
            raise ValueError(f"未知的轨迹阶段：{stage}")
        if status not in STATUSES:
            raise ValueError(f"未知的轨迹状态：{status}")
        step = TraceStep(
            sequence=len(self._steps) + 1,
            stage=stage,
            status=status,
            input_summary=_clip(input_summary),
            output_summary=_clip(output_summary),
            evidence_refs=list(evidence_refs),
            policy_id=policy_id,
            policy_version=policy_version,
            model_or_rule_version=self._rule_version,
            elapsed_ms=int((time.monotonic() - self._started) * 1000),
            error_code=error_code,
            human_action=human_action,
        )
        self._steps.append(step)
        return step

    @property
    def steps(self) -> list[TraceStep]:
        return list(self._steps)


def _clip(text: str) -> str:
    cleaned = " ".join(str(text).split())
    return cleaned if len(cleaned) <= MAX_SUMMARY else cleaned[: MAX_SUMMARY - 1] + "…"
