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

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Sequence
from uuid import UUID, uuid4

from ..domain import questioning, rules, versioning
from ..errors import ApiProblem
from ..integrations.cache import EvaluationCache, NullCache, evaluation_key
from ..integrations.llm import LlmClient
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
)
from .contracts import AgentContext, PolicySnapshot
from .explanation import CriterionLine, explain
from .profile import NormalizedFact, fact_snapshots
from .trace import TraceRecorder

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
        evidence_refs=[{"type": "enterprise", "id": str(enterprise.id)}],
    )

    snapshots = repository.published_policy_snapshots(list(request.policy_ids))
    trace.add(
        "retrieval",
        input_summary=f"检索 {len(request.policy_ids)} 项政策的已发布版本",
        output_summary="、".join(f"{item.id}@{item.version}" for item in snapshots),
        evidence_refs=[{"type": "policy", "id": str(item.id)} for item in snapshots],
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
        input_summary=f"待定政策 {sum(1 for item in pending if item.missing)} 项",
        output_summary=(
            f"下一个问题：{question.fact_ref[0]}，影响 {sum(1 for item in pending if question.fact_ref in item.missing)} 项政策"
            if question
            else "没有需要补充的事实"
        ),
    )

    assessments = [
        _policy_assessment(
            snapshot=snapshot,
            evaluation=evaluation,
            missing=missing_by_policy[snapshot.id],
            question=question,
            llm=llm,
            boundary=versioning.PolicyBoundary(
                effective_to=snapshot.effective_to,
                window_closes_at=snapshot.window_closes_at,
            ),
            now=created_at,
        )
        for snapshot, evaluation in zip(snapshots, evaluations)
    ]
    trace.add(
        "explanation",
        input_summary="按四态生成解释",
        output_summary="、".join(f"{item.policy_id}={item.explanation_mode}" for item in assessments),
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
    now: datetime,
) -> PolicyAssessment:
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

    return PolicyAssessment(
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
            items=[],
        ),
        explanation=explanation.text,
        explanation_mode=explanation.mode,
        warnings=[*explanation.warnings, MATERIAL_LIST_WARNING],
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
