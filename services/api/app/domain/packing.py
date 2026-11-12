"""把抽取或人工保存的条款整理成「可直接落库、可直接比对」的形态。

政策导入后的抽取（API-05）、人工保存条款（API-06）、发布前的重算（API-07），以及
E2 候选的抽取与提交，走的是**同一套**校验：引用校验（``domain.citations``）＋ AST
结构校验（``domain.rules``）＋ 跨条款的「合取为空」（``policy_contradictions``）。
哪里少拼一步，哪里就会多出一条「看着通过了、发布时才被拦下」的条款。

因此判定只写在这里一份，调用方只负责把结果转成自己要的形状（仓储记录、候选的
JSONB、接口响应）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence
from uuid import UUID

from .citations import CriterionInput, SourceText, validate_criteria
from .rules import CriterionRule, policy_contradictions, validate_expression


@dataclass(frozen=True, slots=True)
class PackedCriterion:
    """一版条款：``spans`` 已是契约 ``SourceSpan`` 的线上形状（camelCase 键）。"""

    id: UUID
    title: str
    expression: dict[str, Any]
    spans: list[dict[str, Any]]
    review_finding: str
    validation_status: str


@dataclass(frozen=True, slots=True)
class PackedFinding:
    criterion_id: UUID | None
    code: str
    message: str
    spans: list[dict[str, Any]]


def pack_criteria(
    criteria: Sequence[CriterionInput],
    sources: Mapping[UUID, SourceText],
    version: str,
    *,
    extra: Iterable[Any] = (),
) -> tuple[list[PackedCriterion], list[PackedFinding]]:
    """逐条校验并整理；``extra`` 是抽取阶段已经产生的发现（原样带上，不当作通过）。

    ``version`` 是**这次校验所依据的版本标签**：政策草稿用人工填的版本号，候选阶段
    用 :data:`app.discovery.extraction.CANDIDATE_SOURCE_VERSION`。两者不一致时引用
    校验会如实报 ``SPAN_SOURCE_VERSION_MISMATCH``——这条不变量正是「同一段引文不能
    被悄悄挂到另一个版本上」的保障。
    """

    verdicts = validate_criteria(criteria, sources, version)
    ast_problems = {
        criterion.id: validate_expression(criterion.expression or {}) for criterion in criteria
    }

    packed: list[PackedCriterion] = []
    findings: list[PackedFinding] = []
    for verdict, criterion in zip(verdicts, criteria):
        problems = ast_problems[criterion.id]
        wire_spans = [span.to_wire() for span in criterion.spans]
        packed.append(
            PackedCriterion(
                id=criterion.id,
                title=criterion.title,
                expression=dict(criterion.expression or {}),
                spans=wire_spans,
                review_finding=criterion.review_finding,
                validation_status="failed" if problems else verdict.status,
            )
        )
        for problem in problems:
            findings.append(
                PackedFinding(
                    criterion_id=criterion.id,
                    code=problem.code,
                    message=problem.message,
                    spans=wire_spans,
                )
            )
        for finding in verdict.findings:
            findings.append(
                PackedFinding(
                    criterion_id=criterion.id,
                    code=finding.code,
                    message=finding.message,
                    spans=[span.to_wire() for span in finding.source_spans],
                )
            )

    # 跨条款的「合取为空」只能在这里查：单条 AST 各自合法，合起来却永远不成立。
    comparable = [
        CriterionRule(
            id=criterion.id,
            title=criterion.title,
            expression=criterion.expression or {},
            review_finding=criterion.review_finding,
        )
        for criterion in criteria
        if criterion.expression and not ast_problems[criterion.id]
    ]
    for contradiction in policy_contradictions(comparable):
        findings.append(
            PackedFinding(
                criterion_id=None,
                code=contradiction.code,
                message=contradiction.message,
                spans=[
                    span.to_wire()
                    for criterion in criteria
                    if criterion.id in contradiction.criterion_ids
                    for span in criterion.spans
                ],
            )
        )

    for finding in extra:
        findings.append(
            PackedFinding(
                criterion_id=None,
                code=finding.code,
                message=finding.message,
                spans=[span.to_wire() for span in finding.source_spans],
            )
        )
    return packed, findings


def criterion_wire(item: PackedCriterion) -> dict[str, Any]:
    """契约 ``Criterion`` 的线上形状：候选的 ``criteria`` JSONB 与详情响应用它。"""

    return {
        "id": str(item.id),
        "title": item.title,
        "expression": item.expression,
        "sourceSpans": item.spans,
        "reviewFinding": item.review_finding,
        "validationStatus": item.validation_status,
    }


def finding_wire(item: PackedFinding) -> dict[str, Any]:
    return {
        "criterionId": str(item.criterion_id) if item.criterion_id else None,
        "code": item.code,
        "message": item.message,
        "sourceSpans": item.spans,
    }
