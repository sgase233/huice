"""Request/response models for the implemented subset of the HTTP contract.

Field names follow ``contracts/openapi.yaml`` (camelCase on the wire). These are
wire models only: business rules (URL allow-listing, date ordering, file
content) live in ``agents/ingestion.py`` and the routes.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints
from pydantic.alias_generators import to_camel

NonEmptyStr = Annotated[str, StringConstraints(min_length=1)]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]

PolicyType = Literal["tax", "grant"]
DraftStatus = Literal["pending_parse", "pending_review", "published", "closed"]
SourceKind = Literal["body", "attachment"]
ReviewFinding = Literal["none", "conflict"]
ValidationStatus = Literal["pending", "passed", "failed"]
AgentStage = Literal[
    "ingestion",
    "extraction",
    "citation_validation",
    "human_review",
    "profile",
    "retrieval",
    "evaluation",
    "questioning",
    "explanation",
]
AgentStatus = Literal["ok", "needs_review", "retryable_error", "fatal_error"]


class WireModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        alias_generator=to_camel,
        str_strip_whitespace=False,
    )


class Period(WireModel):
    start_date: date
    end_date: date


class ApplicationWindow(WireModel):
    # 契约的 Timestamp 是 RFC 3339 date-time，必须带时区；无时区输入在入口就拒绝，
    # 否则与库里的 aware 值比较会抛 TypeError 变成 500。
    opens_at: AwareDatetime
    closes_at: AwareDatetime


class PolicyImportMetadata(WireModel):
    policy_id: UUID | None = None
    version: NonEmptyStr
    title: NonEmptyStr
    policy_type: PolicyType = Field(alias="type")
    issuer: NonEmptyStr
    region: NonEmptyStr
    document_number: str | None
    applicable_entities: list[NonEmptyStr] = Field(min_length=1)
    published_at: date
    effective_from: date
    effective_to: date | None
    application_window: ApplicationWindow | None
    source_url: str
    official_entry_url: str


class SourceSpan(WireModel):
    source_id: UUID
    source_url: str
    source_version: NonEmptyStr
    page: Annotated[int, Field(ge=1)] | None
    section: NonEmptyStr | None
    quote: NonEmptyStr
    text_hash: Sha256Hex


class SourceDocument(WireModel):
    id: UUID
    kind: SourceKind
    source_url: str
    title: str
    fetched_at: datetime
    content_hash: Sha256Hex
    text: str


class Criterion(WireModel):
    id: UUID
    title: NonEmptyStr
    expression: dict[str, Any]
    source_spans: list[SourceSpan]
    review_finding: ReviewFinding
    validation_status: ValidationStatus


class ValidationFinding(WireModel):
    criterion_id: UUID | None
    code: str
    message: str
    source_spans: list[SourceSpan]


class PolicyDraft(WireModel):
    id: UUID
    version: str
    title: NonEmptyStr
    policy_type: PolicyType = Field(alias="type")
    issuer: NonEmptyStr
    region: NonEmptyStr
    document_number: str | None
    applicable_entities: list[NonEmptyStr]
    published_at: date
    effective_from: date
    effective_to: date | None
    application_window: ApplicationWindow | None
    source_url: str
    official_entry_url: str
    status: DraftStatus
    revision: UUID
    sources: list[SourceDocument]
    criteria: list[Criterion]
    validation_findings: list[ValidationFinding]


class PolicyDraftList(WireModel):
    items: list[PolicyDraft]
    next_cursor: str | None


class CriterionDraft(WireModel):
    """API-06 请求里的条款草稿；``validationStatus`` 只由服务端计算。"""

    id: UUID
    title: NonEmptyStr
    expression: dict[str, Any]
    source_spans: list[SourceSpan] = Field(min_length=1)
    review_finding: ReviewFinding


class ModelUsage(WireModel):
    mode: Literal["deepseek", "fixture", "saved"]
    model_version: str | None
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    elapsed_ms: int = Field(ge=0)


class ExtractPolicyInput(WireModel):
    version: NonEmptyStr
    model_version: NonEmptyStr | None = None


class ExtractionResult(WireModel):
    draft: PolicyDraft
    criteria_drafts: list[Criterion]
    validation_findings: list[ValidationFinding]
    usage: ModelUsage


class SaveCriteriaInput(WireModel):
    version: NonEmptyStr
    criteria: list[CriterionDraft] = Field(min_length=1)


class SaveCriteriaResult(WireModel):
    draft: PolicyDraft
    criteria: list[Criterion]
    validation_findings: list[ValidationFinding]


class PublishPolicyInput(WireModel):
    version: NonEmptyStr
    review_confirmed: Literal[True]


class PreparationTemplateItem(WireModel):
    title: str
    description: str
    required_fact_refs: list[dict[str, Any]]


class PreparationTemplate(WireModel):
    policy_type: PolicyType = Field(alias="type")
    handling_mode: Literal["tax_self_assessment", "grant_application"]
    official_entry_url: str
    deadline: AwareDatetime | None
    items: list[PreparationTemplateItem]


class PolicyDetail(WireModel):
    id: UUID
    title: NonEmptyStr
    policy_type: PolicyType = Field(alias="type")
    issuer: NonEmptyStr
    region: NonEmptyStr
    version: str
    status: Literal["published", "closed"]
    effective_from: date
    effective_to: date | None
    application_window: ApplicationWindow | None
    source_url: str
    document_number: str | None
    applicable_entities: list[NonEmptyStr]
    published_at: date
    official_entry_url: str
    sources: list[SourceDocument] = Field(min_length=1)
    criteria: list[Criterion] = Field(min_length=1)
    preparation_template: PreparationTemplate


class HealthResponse(WireModel):
    status: Literal["ok"]
    version: str


# ------------------------------------------------------- 企业画像（API-08/09/10）

FactKey = Literal[
    "region",
    "industry",
    "establishedDate",
    "taxableIncome",
    "rdExpense",
    "rdActivity",
    "employeeCountStart",
    "employeeCountEnd",
    "assetsStart",
    "assetsEnd",
    "revenue",
    "equityFinancing",
    "rdExpenseIncrease",
    "rdExpenseRevenueRatio",
    "rdAddDeductionClaimed",
    "creditRecord",
]
FactUnit = Literal["code", "date", "CNY", "boolean", "person", "ratio"]
FactSource = Literal["user_input", "synthetic_material"]

#: 值只能是字符串、布尔或 null；数值一律用十进制字符串，避免浮点误差。
FactValue = str | bool | None


class FactRef(WireModel):
    key: FactKey
    period: Period


class FactInput(WireModel):
    key: FactKey
    value: FactValue
    unit: FactUnit
    period: Period
    source: FactSource


class EnterpriseFact(FactInput):
    verified: bool


class EnterpriseInput(WireModel):
    name_synthetic: str = Field(min_length=1, max_length=100)
    period: Period
    facts: list[FactInput] = Field(min_length=2)


class Enterprise(WireModel):
    id: UUID
    name_synthetic: str = Field(min_length=1, max_length=100)
    period: Period
    facts: list[EnterpriseFact]
    fact_version: Sha256Hex


class FactUpdate(WireModel):
    value: FactValue
    unit: FactUnit
    period: Period
    source: FactSource


# --------------------------------------------------------- 判断与结果（API-11/12）

AssessmentResult = Literal["satisfied", "not_satisfied", "insufficient_evidence", "conflict"]
Validity = Literal["current", "stale"]


class EvidenceRef(WireModel):
    policy_id: UUID
    source_id: UUID
    source_url: str
    source_version: NonEmptyStr
    page: Annotated[int, Field(ge=1)] | None
    section: NonEmptyStr | None
    quote: NonEmptyStr
    text_hash: Sha256Hex


class CalculationInput(WireModel):
    fact_ref: FactRef
    value: str | None


class Calculation(WireModel):
    formula: str
    inputs: list[CalculationInput]
    intermediate_values: list[str]
    value: str | None
    unit: FactUnit


class CriterionResult(WireModel):
    criterion_id: UUID
    result: AssessmentResult
    reason: NonEmptyStr
    fact_refs: list[FactRef]
    evidence_refs: list[EvidenceRef]
    calculation: Calculation | None


class NextQuestion(WireModel):
    fact_ref: FactRef
    prompt: NonEmptyStr
    reason: NonEmptyStr
    rank: Annotated[int, Field(ge=1)]


class StaleReason(WireModel):
    code: Literal[
        "policy_changed",
        "fact_changed",
        "window_closed",
        "policy_expired",
        "valid_until_passed",
    ]
    message: NonEmptyStr
    changed_criterion_ids: list[UUID]
    changed_fact_refs: list[FactRef]


class PreparationItem(WireModel):
    title: NonEmptyStr
    description: str
    required_fact_refs: list[FactRef]
    missing: bool


class Preparation(WireModel):
    policy_type: PolicyType = Field(alias="type")
    handling_mode: Literal["tax_self_assessment", "grant_application"]
    official_entry_url: str
    deadline: AwareDatetime | None
    items: list[PreparationItem]


class PolicyAssessment(WireModel):
    policy_id: UUID
    result: AssessmentResult
    validity: Validity
    stale_reasons: list[StaleReason]
    criteria_results: list[CriterionResult]
    missing_facts: list[FactRef]
    next_question: NextQuestion | None
    valid_until: AwareDatetime | None
    preparation: Preparation
    explanation: NonEmptyStr
    explanation_mode: Literal["deepseek", "saved", "fixture"]
    warnings: list[str]


class AssessmentInput(WireModel):
    enterprise_id: UUID
    policy_ids: list[UUID] = Field(min_length=1, max_length=7)
    as_of: AwareDatetime
    period: Period
    supersedes_run_id: UUID | None = None
    skipped_fact_refs: list[FactRef] | None = None


class AssessmentRun(WireModel):
    id: UUID
    enterprise_id: UUID
    period: Period
    as_of: AwareDatetime
    created_at: AwareDatetime
    policy_version_map: dict[str, NonEmptyStr]
    fact_version: Sha256Hex
    rule_version: NonEmptyStr
    supersedes_run_id: UUID | None
    assessments: list[PolicyAssessment] = Field(min_length=1, max_length=7)


class PolicySummary(WireModel):
    id: UUID
    title: NonEmptyStr
    policy_type: PolicyType = Field(alias="type")
    issuer: NonEmptyStr
    region: NonEmptyStr
    version: str
    status: Literal["published", "closed"]
    effective_from: date
    effective_to: date | None
    application_window: ApplicationWindow | None
    source_url: str


class PolicyList(WireModel):
    items: list[PolicySummary]
    next_cursor: str | None


# ------------------------------------------------------------------ API-13/14


class TraceStep(WireModel):
    """一条脱敏轨迹；只有 ``sequence/stage/status/summary`` 类字段，不含提示词或原始资料。"""

    sequence: Annotated[int, Field(ge=1)]
    stage: AgentStage
    status: AgentStatus
    input_summary: str
    output_summary: str
    evidence_refs: list[dict[str, Any]]
    policy_id: UUID | None
    policy_version: str | None
    model_or_rule_version: str
    elapsed_ms: Annotated[int, Field(ge=0)]
    error_code: str | None
    human_action: str | None


class TraceResponse(WireModel):
    steps: list[TraceStep]
