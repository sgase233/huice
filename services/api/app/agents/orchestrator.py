"""在线链路编排（API-11，FR-04/FR-09）。

``assess`` 把一次判断串起来：画像快照 → 已审核规则检索 → 确定性求值 → 补问 →
解释 → 轨迹与落库。纪律：

* 判断只由 :func:`app.domain.rules.evaluate_policy` 做；模型只能改解释的措辞。
* 未发布或引用未通过的政策**整体 422**，不在一次运行里悄悄跳过。
* run 是历史：写进去之后不覆盖，重算一律新 ID；有效性是读取时算的视图。
* 只补问一个问题：由 :func:`app.domain.questioning.choose_next_question` 排序决定。
* 缓存只复用规则求值：键含企业/政策/政策版本/事实版本/规则版本/期间/时点，命中
  仍按当前时间重算有效性与 ``validUntil``，且每次请求都分配新 runId（FR-07）。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Sequence
from uuid import UUID, uuid4

from ..domain import preparation, questioning, rules, versioning
from ..errors import ApiProblem
from ..integrations.cache import EvaluationCache, NullCache, evaluation_key
from ..integrations.llm import LlmClient, LlmResult
from ..schemas import (
    AssessmentInput,
    AssessmentRun,
    Calculation,
    CalculationInput,
    CriterionResult,
    Enterprise,
    EvidenceRef,
    FactRef,
    NextQuestion,
    Period,
    PolicyAssessment,
    Preparation,
    PreparationItem,
)
from .contracts import AgentContext, PolicySnapshot
from .explanation import CriterionLine, Explanation, explain
from .profile import NormalizedFact, fact_snapshots
from .trace import TraceRecorder

logger = logging.getLogger(__name__)

MATERIAL_LIST_WARNING = "申报材料清单待 M3 审核材料导入后补齐，当前只展示办理方式与入口。"

# 未传缓存时的替身：永远未命中，行为与「没有 Redis」完全一致。
DISABLED_CACHE = EvaluationCache(NullCache())


def assess(
    *,
    request: AssessmentInput,
    context: AgentContext,
    repository,
    llm: LlmClient | None = None,
    cache: EvaluationCache | None = None,
    user_id=None,
) -> AssessmentRun:
    trace = TraceRecorder(trace_id=context.trace_id, model_or_rule_version=rules.RULE_VERSION)
    created_at = datetime.now(timezone.utc)

    if request.supersedes_run_id is not None:
        _check_supersedes(repository, request)

    enterprise = repository.get_enterprise(request.enterprise_id)
    trace.add(
        "profile",
        input_summary=f"读取企业画像 {request.enterprise_id}",
        output_summary=f"画像含 {len(enterprise.facts)} 条事实，版本 {enterprise.fact_version[:12]}",
        object_refs=[{"type": "enterprise", "id": str(enterprise.id)}],
    )

    snapshots = repository.published_policy_snapshots(list(request.policy_ids))
    trace.add(
        "retrieval",
        input_summary=f"检索 {len(request.policy_ids)} 项政策的已发布版本",
        output_summary="、".join(f"{item.id}@{item.version}" for item in snapshots),
        object_refs=[{"type": "policy", "id": str(item.id)} for item in snapshots],
    )

    facts = rules.FactTable(fact_snapshots(_enterprise_facts(enterprise)))
    period = (request.period.start_date, request.period.end_date)
    as_of_date = versioning.shanghai_date(request.as_of)

    evaluations, cache_hits = _evaluate_all(
        snapshots=snapshots,
        facts=facts,
        period=period,
        as_of_date=as_of_date,
        as_of=request.as_of,
        enterprise_id=enterprise.id,
        fact_version=enterprise.fact_version,
        cache=cache or DISABLED_CACHE,
    )
    evaluation_summary = "；".join(f"{item.policy_id}={item.result}" for item in evaluations)
    if cache_hits:
        evaluation_summary += f"（其中 {cache_hits}/{len(evaluations)} 项复用缓存求值）"
    trace.add(
        "evaluation",
        input_summary=f"按 {len(snapshots)} 项政策、{len(enterprise.facts)} 条事实求值",
        output_summary=evaluation_summary,
    )

    missing_by_policy = {item.policy_id: _missing_refs(item) for item in evaluations}
    pending = [
        questioning.PendingAssessment(
            policy_id=item.policy_id, result=item.result, missing=missing_by_policy[item.policy_id]
        )
        for item in evaluations
    ]
    skipped = [
        (ref.key, ref.period.start_date, ref.period.end_date)
        for ref in (request.skipped_fact_refs or [])
    ]
    question = questioning.choose_next_question(pending, skipped)
    trace.add(
        "questioning",
        input_summary=f"待定政策 {sum(1 for item in pending if item.result == rules.INSUFFICIENT)} 项",
        output_summary=(
            f"下一个问题：{question.fact_ref[0]}，影响 {sum(1 for item in pending if question.fact_ref in item.missing)} 项政策"
            if question
            else "没有需要补充的事实"
        ),
    )

    assessments: list[PolicyAssessment] = []
    model_usages: list[LlmResult] = []
    unknown_usages = 0
    known_refs = {fact.ref for fact in facts.values() if fact.known}
    for snapshot, evaluation in zip(snapshots, evaluations):
        assessment, usage, failure = _policy_assessment(
            snapshot=snapshot,
            evaluation=evaluation,
            missing=missing_by_policy[snapshot.id],
            question=question,
            llm=llm,
            boundary=versioning.PolicyBoundary(
                effective_to=snapshot.effective_to,
                window_closes_at=snapshot.window_closes_at,
            ),
            period=period,
            known_refs=known_refs,
            now=created_at,
        )
        assessments.append(assessment)
        if usage is not None and usage.usage_known:
            model_usages.append(usage)
        elif usage is not None:
            # 回答拿到了但上游没给用量：不计入合计，按未知单列。
            unknown_usages += 1
        if failure is not None and failure.usage_unknown:
            unknown_usages += 1
        if usage is not None or failure is not None:
            # 失败也记一行：模型可能已被调用并计费，不能因为解析失败就从账上消失。
            _log_model_call(
                purpose="explanation",
                usage=usage,
                usage_unknown=failure.usage_unknown if failure is not None else False,
                policy_id=snapshot.id,
                version=snapshot.version,
            )

    trace.add(
        "explanation",
        input_summary="按四态生成解释",
        output_summary=_explanation_summary(assessments, model_usages, unknown_usages),
    )

    run = AssessmentRun(
        id=uuid4(),
        enterprise_id=enterprise.id,
        period=Period(start_date=period[0], end_date=period[1]),
        as_of=request.as_of,
        created_at=created_at,
        policy_version_map={str(item.id): item.version for item in snapshots},
        fact_version=enterprise.fact_version,
        rule_version=rules.RULE_VERSION,
        supersedes_run_id=request.supersedes_run_id,
        assessments=assessments,
    )

    repository.save_run(
        run=run,
        dependencies={item.policy_id: item.dependencies for item in evaluations},
        trace_steps=trace.steps,
        trace_id=context.trace_id,
        policy_version_ids={item.id: item.version_id for item in snapshots},
        user_id=user_id,
    )
    return run


# ------------------------------------------------------------------ 组装件


def _check_supersedes(repository, request: AssessmentInput) -> None:
    """旧 run 必须属于同一企业、同一组政策与同一期间，否则不是「重算」。"""

    header = repository.get_run_header(request.supersedes_run_id)
    if header is None:
        raise ApiProblem(
            422, "VALIDATION_ERROR", "supersedesRunId 指向的运行不存在。", {"fieldErrors": []}
        )
    expected = set(request.policy_ids)
    if (
        header["enterprise_id"] != request.enterprise_id
        or set(header["policy_ids"]) != expected
        or header["period_start"] != request.period.start_date
        or header["period_end"] != request.period.end_date
    ):
        raise ApiProblem(
            422,
            "VALIDATION_ERROR",
            "被替代的运行与企业、政策集合或期间不一致，不能作为重算依据。",
            {"fieldErrors": [{"field": "supersedesRunId", "message": "同一企业、政策集合与期间才可替代。"}]},
        )


def _evaluate_all(
    *,
    snapshots: Sequence[PolicySnapshot],
    facts: rules.FactTable,
    period: tuple[date, date],
    as_of_date: date,
    as_of: datetime,
    enterprise_id: UUID,
    fact_version: str,
    cache: EvaluationCache,
) -> tuple[list[rules.PolicyEvaluation], int]:
    """逐项取缓存或重算；返回结果与命中数（命中数只用于轨迹摘要）。"""

    evaluations: list[rules.PolicyEvaluation] = []
    hits = 0
    for snapshot in snapshots:
        key = evaluation_key(
            enterprise_id=enterprise_id,
            policy_id=snapshot.id,
            policy_version=snapshot.version,
            fact_version=fact_version,
            rule_version=rules.RULE_VERSION,
            period=period,
            as_of=as_of,
        )
        cached = cache.lookup(key=key, snapshot=snapshot)
        if cached is not None:
            hits += 1
            evaluations.append(cached)
            continue
        outcome = rules.evaluate_policy(
            policy_id=snapshot.id,
            version=snapshot.version,
            criteria=snapshot.rules,
            facts=facts,
            period=period,
            as_of=as_of_date,
        )
        cache.remember(key=key, evaluation=outcome)
        evaluations.append(outcome)
    return evaluations, hits


def _enterprise_facts(enterprise: Enterprise) -> list[NormalizedFact]:
    """把线上画像转回可求值的事实：金额/人数回到 Decimal，日期回到 date。"""

    facts: list[NormalizedFact] = []
    for item in enterprise.facts:
        value = item.value
        if isinstance(value, str) and item.unit in ("CNY", "person"):
            value = Decimal(value)
        elif isinstance(value, str) and item.unit == "date":
            value = date.fromisoformat(value)
        facts.append(
            NormalizedFact(
                key=item.key,
                unit=item.unit,
                period_start=item.period.start_date,
                period_end=item.period.end_date,
                source=item.source,
                value=value,
            )
        )
    return facts


def _missing_refs(evaluation: rules.PolicyEvaluation) -> list[tuple[str, date, date]]:
    """政策级缺失事实：只有结论仍悬着时才有。

    政策一旦定性（成立、不成立、冲突），个别条件缺事实也不影响这一结论，
    再报出来只会让 ``missingFacts`` 和补问都带上无关项。
    """
    if evaluation.result != rules.INSUFFICIENT:
        return []
    refs: list[tuple[str, date, date]] = []
    for criterion in evaluation.criteria:
        for ref in criterion.missing:
            if ref not in refs:
                refs.append(ref)
    return refs


def _policy_assessment(
    *,
    snapshot: PolicySnapshot,
    evaluation: rules.PolicyEvaluation,
    missing: Sequence[tuple[str, date, date]],
    question: questioning.Question | None,
    llm: LlmClient | None,
    boundary: versioning.PolicyBoundary,
    period: tuple[date, date],
    known_refs: set[tuple[str, date, date]],
    now: datetime,
) -> tuple[PolicyAssessment, LlmResult | None, Explanation | None]:
    """返回契约里的单政策判断、解释阶段的模型用量（未调模型为 ``None``），
    以及调用失败时的解释结果（成功或未调模型为 ``None``）。"""
    criteria_results = [
        _criterion_result(snapshot, criterion) for criterion in evaluation.criteria
    ]
    missing_facts = [FactRef(key=ref[0], period=_period(ref)) for ref in missing]

    next_question = None
    if question is not None and question.fact_ref in missing:
        next_question = NextQuestion(
            fact_ref=FactRef(key=question.fact_ref[0], period=_period(question.fact_ref)),
            prompt=question.prompt,
            reason=question.reason,
            rank=question.rank,
        )

    handling_mode = (
        "tax_self_assessment" if snapshot.policy_type == "tax" else "grant_application"
    )
    explanation = explain(
        policy_title=snapshot.title,
        result=evaluation.result,
        criteria=[
            CriterionLine(title=item.rule.title, result=outcome.result, reason=outcome.reason)
            for item, outcome in zip(snapshot.criteria, evaluation.criteria)
        ],
        handling_mode=handling_mode,
        missing_keys=[ref.key for ref in missing_facts],
        llm=llm,
    )

    items = _material_items(
        snapshot=snapshot, period=period, known_refs=known_refs
    )

    assessment = PolicyAssessment(
        policy_id=snapshot.id,
        result=evaluation.result,
        validity=versioning.CURRENT,
        stale_reasons=[],
        criteria_results=criteria_results,
        missing_facts=missing_facts,
        next_question=next_question,
        valid_until=versioning.valid_until(boundary=boundary, now=now),
        preparation=Preparation(
            policy_type=snapshot.policy_type,
            handling_mode=handling_mode,
            official_entry_url=snapshot.official_entry_url,
            deadline=snapshot.window_closes_at,
            items=items,
        ),
        explanation=explanation.text,
        explanation_mode=explanation.mode,
        # 「待补齐」只在真的一条材料都没有时说；有清单还挂这句话会让人以为清单是空的
        warnings=[*explanation.warnings, *([] if items else [MATERIAL_LIST_WARNING])],
    )
    return assessment, explanation.usage, (explanation if explanation.call_failed else None)


def _material_items(
    *,
    snapshot: PolicySnapshot,
    period: tuple[date, date],
    known_refs: set[tuple[str, date, date]],
) -> list[PreparationItem]:
    """这次判断要交的材料；政策条件实际用到哪些事实，就从条件里读，不另写一份。"""

    referenced: list[tuple[str, date, date]] = []
    for criterion in snapshot.criteria:
        referenced.extend(
            rules.referenced_facts(criterion.rule.expression, period=period)
        )
    items = preparation.build_items(
        materials=snapshot.materials,
        referenced=referenced,
        known=known_refs,
        period=period,
        policy_closed=snapshot.closed,
    )
    return [
        PreparationItem(
            material_id=item.material_id,
            title=item.title,
            description=item.description,
            required_fact_refs=[
                FactRef(key=ref[0], period=_period(ref)) for ref in item.fact_refs
            ],
            missing=item.missing,
            status=item.status,
        )
        for item in items
    ]


def _explanation_summary(
    assessments: Sequence[PolicyAssessment],
    usages: Sequence[LlmResult],
    unknown_usages: int = 0,
) -> str:
    """轨迹摘要：先写模型用量（有真实调用时），再写每项政策的解释来源。

    用量只进摘要与日志，不进契约响应；``TraceRecorder`` 会按 200 字截断，
    所以多政策时把用量放前面，避免被截掉。失败次数单列，不与已知用量相加。
    """

    parts = []
    if usages:
        prompt = sum(item.prompt_tokens for item in usages)
        completion = sum(item.completion_tokens for item in usages)
        parts.append(f"模型 tokens 入 {prompt} 出 {completion}")
    if unknown_usages:
        # 失败与「成功但响应缺用量」同属未知：都可能已计费，都不并入上面的合计。
        parts.append(f"另有 {unknown_usages} 次调用用量未知（失败或响应缺用量，可能已计费）")
    parts.append("、".join(f"{item.policy_id}={item.explanation_mode}" for item in assessments))
    return "；".join(parts)


def _log_model_call(
    *,
    purpose: str,
    usage: LlmResult | None,
    usage_unknown: bool = False,
    policy_id: UUID | None = None,
    version: str | None = None,
) -> None:
    """一次模型调用一行；只记用量与对象标识，不记提示词与正文。

    ``usage`` 为 ``None`` 表示调用失败：``usage_unknown`` 为真时用量写 ``unknown``
    （上游是否计费无法确认），否则写 ``0`` / ``none``（未发起调用）。
    成功但上游没给用量（``usage.usage_known`` 为假）同样写 ``unknown``。
    """

    if usage is not None:
        known = usage.usage_known
        logger.info(
            "model_call purpose=%s outcome=ok usage=%s model=%s prompt=%s completion=%s "
            "elapsed_ms=%d policy_id=%s version=%s",
            purpose,
            "known" if known else "unknown",
            usage.model,
            usage.prompt_tokens if known else "unknown",
            usage.completion_tokens if known else "unknown",
            usage.elapsed_ms,
            policy_id,
            version,
        )
        return
    logger.info(
        "model_call purpose=%s outcome=error usage=%s prompt=%s completion=%s "
        "elapsed_ms=unknown policy_id=%s version=%s",
        purpose,
        "unknown" if usage_unknown else "none",
        "unknown" if usage_unknown else 0,
        "unknown" if usage_unknown else 0,
        policy_id,
        version,
    )


def _criterion_result(
    snapshot: PolicySnapshot, criterion: rules.CriterionEvaluation
) -> CriterionResult:
    evidence = [
        EvidenceRef(
            policy_id=snapshot.id,
            source_id=span.source_id,
            source_url=span.source_url,
            source_version=span.source_version,
            page=span.page,
            section=span.section,
            quote=span.quote,
            text_hash=span.text_hash,
        )
        for span in snapshot.spans_of(criterion.criterion_id)
    ]
    return CriterionResult(
        criterion_id=criterion.criterion_id,
        result=criterion.result,
        reason=criterion.reason,
        fact_refs=[FactRef(key=ref[0], period=_period(ref)) for ref in criterion.fact_refs],
        evidence_refs=evidence,
        calculation=_calculation(criterion.calculation),
    )


def _calculation(payload: dict | None) -> Calculation | None:
    """把规则层的季度计算过程映射成契约 ``Calculation``（保留输入与中间值）。"""

    if not payload:
        return None
    metric = str(payload.get("metric") or "")
    start_key, end_key, unit = rules.QUARTERLY_METRICS.get(
        metric, ("employeeCountStart", "employeeCountEnd", "person")
    )
    inputs: list[CalculationInput] = []
    intermediates: list[str] = []
    for item in payload.get("inputs") or []:
        start, _, end = str(item.get("quarter") or "").partition("/")
        inputs.append(CalculationInput(fact_ref=_wire_ref(start_key, start, end), value=item.get("start")))
        inputs.append(CalculationInput(fact_ref=_wire_ref(end_key, start, end), value=item.get("end")))
        intermediates.append(str(item.get("midpoint")))
    return Calculation(
        formula="各季度（期初+期末）/2 后对完整四个季度取算术平均，与阈值同口径比较。",
        inputs=inputs,
        intermediate_values=intermediates,
        value=payload.get("average"),
        unit=unit,
    )


def _wire_ref(key: str, start: str, end: str) -> FactRef:
    return FactRef(
        key=key,
        period=Period(start_date=date.fromisoformat(start), end_date=date.fromisoformat(end)),
    )


def _period(ref: tuple[str, date, date]) -> Period:
    return Period(start_date=ref[1], end_date=ref[2])
