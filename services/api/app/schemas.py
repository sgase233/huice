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


class HealthResponse(WireModel):
    status: Literal["ok"]
    version: str
