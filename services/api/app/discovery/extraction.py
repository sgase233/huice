"""候选的结构化抽取：复用导入链路的抽取与引用校验，结论只多一个「版本标签」。

# 为什么不另写一套抽取

E2 的候选提交后就是一条政策草稿（DISC-07 → API-04 等价链路），发布时仍要过 API-07
的同一道闸门。如果候选阶段用另一套提示词/另一套引用校验，审核员在候选页看到的
「已通过」就与发布时的结论无关，等于给了一个假的安心。所以这里直接调用
``agents.extraction.extract_criteria`` 与 ``domain.packing.pack_criteria``。

# 版本标签

引用片段带 ``sourceVersion``，校验时要求它与当前版本一致。候选阶段还没有人工命名的
版本号，统一用 :data:`CANDIDATE_SOURCE_VERSION`；提交时把片段改挂到新建版本号上
（同一条正文、同一份来源），再按正式版本重校验。这一步是**改标签不改证据**：
引文与正文逐字未动，``textHash`` 仍与引文一致。

# 失败即失败

抽取没给出任何结构化条件、或模型/预算层面报错，候选就落 ``extraction_failed`` 并带
明确原因，不生成一条「看起来完整」的空候选（EXTENSION_INTERFACES §4.1）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from ..agents.extraction import ExtractionSource, extract_criteria
from ..domain.citations import CriterionInput, SourceText
from ..domain.packing import criterion_wire, finding_wire, pack_criteria
from ..errors import ApiProblem

#: 候选阶段引用片段的版本标签；提交时整体改挂到新建版本号。
CANDIDATE_SOURCE_VERSION = "candidate"

#: 抽取失败时写进候选 ``extraction.failureCode`` 的两个内部码（不进契约枚举）。
CODE_NO_CRITERIA = "EXTRACTION_NO_CRITERIA"
CODE_BODY_EMPTY = "EXTRACTION_NO_BODY"


@dataclass(frozen=True, slots=True)
class CandidateExtraction:
    """一次候选抽取的完整结果，可直接写回 ``policy_candidate``。"""

    status: str
    criteria: list[dict[str, Any]] = field(default_factory=list)
    findings: list[dict[str, Any]] = field(default_factory=list)
    extraction: dict[str, Any] | None = None
    usage: dict[str, Any] | None = None
    tokens_used: int = 0
    tokens_known: bool = True


def extract_candidate(
    *,
    candidate_id: UUID,
    source_url: str,
    text: str,
    llm: Any = None,
    model_version: str | None = None,
) -> CandidateExtraction:
    """在候选正文上抽取条款并做与正式导入一致的校验。

    预算闸门不在这里：``extract_candidate`` 只对**已经决定要发**的正文调用，调用前
    的估算与运行级预算由 :mod:`app.discovery.runner` 掌握。
    """

    if not text.strip():
        return CandidateExtraction(
            status="extraction_failed",
            extraction={"failureCode": CODE_BODY_EMPTY, "failureMessage": "页面没有可抽取的正文。"},
        )

    # 候选自己的 id 就是来源 id：片段的 sourceId 在整个候选生命周期内稳定，
    # 提交时再映射到新建草稿的来源 id（见 routes/discovery.submit_candidate）。
    sources = [ExtractionSource(id=candidate_id, source_url=source_url, text=text, kind="body")]

    try:
        outcome = extract_criteria(
            version=CANDIDATE_SOURCE_VERSION,
            sources=sources,
            llm=llm,
            model_version=model_version,
        )
    except ApiProblem as problem:
        # 模型不可用/超时等：候选明确失败，不把「没抽到」伪装成「没有条件」。
        return CandidateExtraction(
            status="extraction_failed",
            extraction={"failureCode": problem.code, "failureMessage": problem.message},
        )

    usage = outcome.usage
    usage_wire = usage.model_dump(by_alias=True, mode="json") if usage is not None else None
    tokens_known = usage is not None and not usage.tokens_unknown
    tokens_used = (
        usage.input_tokens + usage.output_tokens if usage is not None and tokens_known else 0
    )

    if not outcome.criteria:
        return CandidateExtraction(
            status="extraction_failed",
            extraction={
                "failureCode": CODE_NO_CRITERIA,
                "failureMessage": "没有从正文里抽到可核对的结构化条件，请人工录入。",
                "usage": usage_wire,
            },
            usage=usage_wire,
            tokens_used=tokens_used,
            tokens_known=tokens_known,
        )

    criteria = [
        CriterionInput(
            id=item.id,
            title=item.title,
            review_finding=item.review_finding,
            spans=tuple(item.spans),
            expression=item.expression or None,
        )
        for item in outcome.criteria
    ]
    texts = {candidate_id: SourceText(id=candidate_id, source_url=source_url, text=text)}
    packed, findings = pack_criteria(
        criteria, texts, CANDIDATE_SOURCE_VERSION, extra=outcome.findings
    )

    return CandidateExtraction(
        status="pending_review",
        criteria=[criterion_wire(item) for item in packed],
        findings=[finding_wire(item) for item in findings],
        extraction={
            "mode": usage.mode if usage is not None else None,
            "modelVersion": usage.model_version if usage is not None else model_version,
            "inputChars": len(text),
            "criteriaCount": len(packed),
            "findingsCount": len(findings),
            "elapsedMs": usage.elapsed_ms if usage is not None else 0,
            "usage": usage_wire,
        },
        usage=usage_wire,
        tokens_used=tokens_used,
        tokens_known=tokens_known,
    )
