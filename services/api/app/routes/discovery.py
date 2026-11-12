"""政策发现的后台接口（DISC-01—08）：配置、运行记录、候选与提交/丢弃。

# 候选为什么不能直接改政策库

``policy_candidate`` 与 ``policy`` / ``policy_version`` 不共表（迁移 010 第 1 条取舍）：
候选阶段只写暂存区，审核员确认后通过 DISC-07 走**与 API-04/06 相同的**仓储方法建草稿
并保存条款。「未审核候选不进政策库」因此是结构上的事实，不是靠前端不显示。

# 提交是一个事务

锁候选行 → 校验状态与 ETag → 建草稿（pending_parse）→ 保存条款（pending_review）→
标记候选已提交 → 写幂等记录，全部在同一事务里提交或一起回滚（§4.3.6）。对象存储的
正文在事务之外写、失败即删，避免留下孤儿对象；事务回滚时同样删除。

# 引用片段的版本标签

候选阶段片段的 ``sourceVersion`` 是 ``candidate``；提交时改挂到新建版本号上再按正式
版本重新校验——引文与 ``textHash`` 逐字未动，只是「这段原文属于哪个版本」这件事由
审核员保存草稿时才确定。
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError

from ..agents.ingestion import PreparedSource
from ..discovery.extraction import CANDIDATE_SOURCE_VERSION
from ..discovery.fetch import (
    MAX_BODY_BYTES,
    MAX_CANDIDATES_PER_RUN,
    MAX_REDIRECTS,
    MAX_RUN_SECONDS,
    MIN_HOST_INTERVAL_SECONDS,
    FetchError,
    require_official_url,
)
from ..discovery.metadata import missing_fields_of
from ..discovery.runner import CANDIDATE_TOKEN_BUDGET, RUN_TOKEN_BUDGET
from ..domain.citations import CriterionInput, SourceSpanCheck, SourceText
from ..domain.findings import ATTACHMENT_NOT_EXTRACTED
from ..domain.packing import criterion_wire, finding_wire, pack_criteria
from ..errors import ApiProblem, trace_id_of
from ..integrations.storage import CriterionRecord, FindingRecord
from ..schemas import (
    CandidateDetail,
    CandidateList,
    CandidateMetadata,
    CandidateSummary,
    CandidateStatus,
    CandidateUpdate,
    Criterion,
    DiscoveryLimits,
    DiscoveryRun,
    DiscoveryRunList,
    DiscoverySettings,
    DiscoverySettingsUpdate,
    DiscardCandidateInput,
    ModelUsage,
    PolicyDraft,
    ValidationFinding,
)
from ..security import GatedRoute
from .guards import require_idempotency_key, require_if_match, require_reviewer_write

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"], route_class=GatedRoute)

#: 提交候选的幂等作用域。与抽取（``extract``）分开：同一个键在两条链路上互不干扰。
SUBMIT_SCOPE = "policy-candidate-submit"


MIN_INTERVAL_MINUTES = 360
MAX_INTERVAL_MINUTES = 43_200
MAX_SOURCE_URLS = 20

#: 候选可编辑的状态。已提交/已丢弃/已过期都是终态，PATCH 一律 409。
_EDITABLE = ("pending_review", "extraction_failed")


def _limits() -> DiscoveryLimits:
    return DiscoveryLimits(
        min_interval_minutes=MIN_INTERVAL_MINUTES,
        max_interval_minutes=MAX_INTERVAL_MINUTES,
        max_source_urls=MAX_SOURCE_URLS,
        max_candidates_per_run=MAX_CANDIDATES_PER_RUN,
        run_timeout_minutes=MAX_RUN_SECONDS // 60,
        run_token_budget=RUN_TOKEN_BUDGET,
        candidate_token_budget=CANDIDATE_TOKEN_BUDGET,
        max_response_bytes=MAX_BODY_BYTES,
        min_host_interval_seconds=int(MIN_HOST_INTERVAL_SECONDS),
        max_redirects=MAX_REDIRECTS,
    )


# ---------------------------------------------------------------- DISC-01/02


@router.get(
    "/discovery/settings",
    response_model=DiscoverySettings,
    operation_id="getDiscoverySettings",
    summary="DISC-01",
)
def get_discovery_settings(request: Request, response: Response) -> DiscoverySettings:
    require_reviewer_read(request)
    row = request.app.state.discovery.get_settings()
    response.headers["ETag"] = f'"{row["revision"]}"'
    return _settings_of(row)


@router.put(
    "/discovery/settings",
    response_model=DiscoverySettings,
    operation_id="updateDiscoverySettings",
    summary="DISC-02",
)
def update_discovery_settings(
    request: Request, response: Response, body: DiscoverySettingsUpdate
) -> DiscoverySettings:
    require_reviewer_write(request)
    expected_revision = require_if_match(request)

    if not MIN_INTERVAL_MINUTES <= body.interval_minutes <= MAX_INTERVAL_MINUTES:
        raise _field_problem(
            "intervalMinutes",
            f"抓取周期必须在 {MIN_INTERVAL_MINUTES}—{MAX_INTERVAL_MINUTES} 分钟之间。",
        )
    if len(body.source_urls) > MAX_SOURCE_URLS:
        raise _field_problem("sourceUrls", f"最多配置 {MAX_SOURCE_URLS} 个来源网址。")
    if body.enabled and not body.source_urls:
        raise _field_problem("sourceUrls", "启用自动发现前至少要配置一个来源网址。")

    urls: list[str] = []
    for index, raw in enumerate(body.source_urls):
        try:
            require_official_url(raw, f"sourceUrls[{index}]")
        except FetchError as exc:
            raise _field_problem(f"sourceUrls[{index}]", exc.message) from exc
        if raw in urls:
            raise _field_problem(f"sourceUrls[{index}]", "同一个来源网址不能重复配置。")
        urls.append(raw)

    try:
        ZoneInfo(body.time_zone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise _field_problem("timeZone", "不是合法的时区名称（例如 Asia/Shanghai）。") from exc

    row = request.app.state.discovery.update_settings(
        expected_revision=expected_revision,
        enabled=body.enabled,
        source_urls=urls,
        interval_minutes=body.interval_minutes,
        time_zone=body.time_zone,
    )
    response.headers["ETag"] = f'"{row["revision"]}"'
    return _settings_of(row)


# ----------------------------------------------------------------- DISC-03


@router.get(
    "/discovery/runs",
    response_model=DiscoveryRunList,
    operation_id="listDiscoveryRuns",
    summary="DISC-03",
)
def list_discovery_runs(
    request: Request,
    cursor: str | None = Query(default=None, min_length=1),
    limit: int = Query(default=20, ge=1, le=50),
) -> DiscoveryRunList:
    require_reviewer_read(request)
    page = request.app.state.discovery.list_runs(cursor=cursor, limit=limit)
    return DiscoveryRunList(
        items=[_run_of(row) for row in page["items"]], next_cursor=page["next_cursor"]
    )


# ---------------------------------------------------------------- DISC-04/05


@router.get(
    "/policy-candidates",
    response_model=CandidateList,
    operation_id="listPolicyCandidates",
    summary="DISC-04",
)
def list_policy_candidates(
    request: Request,
    status: CandidateStatus | None = Query(default=None),
    cursor: str | None = Query(default=None, min_length=1),
    limit: int = Query(default=20, ge=1, le=50),
) -> CandidateList:
    require_reviewer_read(request)
    page = request.app.state.discovery.list_candidates(
        status=status, cursor=cursor, limit=limit
    )
    return CandidateList(
        items=[_candidate_summary(row) for row in page["items"]],
        next_cursor=page["next_cursor"],
    )


@router.get(
    "/policy-candidates/{candidateId}",
    response_model=CandidateDetail,
    operation_id="getPolicyCandidate",
    summary="DISC-05",
)
def get_policy_candidate(
    request: Request, response: Response, candidateId: UUID
) -> CandidateDetail:
    require_reviewer_read(request)
    row = _load_candidate(request, candidateId)
    response.headers["ETag"] = f'"{row["revision"]}"'
    return _candidate_detail(row)


# ------------------------------------------------------------------- DISC-06


@router.patch(
    "/policy-candidates/{candidateId}",
    response_model=CandidateDetail,
    operation_id="updatePolicyCandidate",
    summary="DISC-06",
)
def update_policy_candidate(
    request: Request,
    response: Response,
    candidateId: UUID,
    body: CandidateUpdate,
) -> CandidateDetail:
    require_reviewer_write(request)
    expected_revision = require_if_match(request)
    metadata = body.metadata.model_dump()
    _check_ordering(body.metadata)

    discovery = request.app.state.discovery
    with request.app.state.db.connection() as conn, conn.transaction():
        row = discovery.lock_candidate(conn, candidateId)
        if row is None:
            raise _not_found()
        _ensure_editable(row, expected_revision)

        criteria_wire = None
        findings_wire = None
        status = None
        if body.criteria_drafts is not None:
            inputs = [_criterion_input(item) for item in body.criteria_drafts]
            texts = {
                candidateId: SourceText(
                    id=candidateId, source_url=row["source_url"], text=row["body_text"]
                )
            }
            packed, packed_findings = pack_criteria(inputs, texts, CANDIDATE_SOURCE_VERSION)
            criteria_wire = [criterion_wire(item) for item in packed]
            findings_wire = [finding_wire(item) for item in packed_findings]
            # 人工补齐条件后，候选就不再是「抽取失败」——否则它会永远卡在不可提交状态。
            if row["status"] == "extraction_failed":
                status = "pending_review"

        missing = _missing_fields({**row, **metadata})
        updated = discovery.edit_candidate(
            conn,
            candidate_id=candidateId,
            revision=expected_revision,
            metadata=metadata,
            criteria=criteria_wire,
            findings=findings_wire,
            missing_fields=missing,
            now=datetime.now(timezone.utc),
            status=status,
        )
    response.headers["ETag"] = f'"{updated["revision"]}"'
    return _candidate_detail(updated)


# ------------------------------------------------------------------- DISC-07


@router.post(
    "/policy-candidates/{candidateId}/submit",
    status_code=201,
    response_model=PolicyDraft,
    operation_id="submitPolicyCandidate",
    summary="DISC-07",
)
def submit_policy_candidate(
    request: Request, response: Response, candidateId: UUID
) -> PolicyDraft:
    require_reviewer_write(request)
    expected_revision = require_if_match(request)
    idempotency_key = require_idempotency_key(request)

    discovery = request.app.state.discovery
    policies = request.app.state.repository
    fingerprint = _submit_fingerprint(candidateId, expected_revision)

    storage_key: str | None = None
    try:
        with request.app.state.db.connection() as conn, conn.transaction():
            # 查键、锁候选、写结果必须在同一把键锁下，否则并发重试会绕过幂等：
            # 后到的请求在候选行锁上等到的是「已提交」状态，直接变成 409 而不是重放。
            policies.lock_idempotency_key(conn, SUBMIT_SCOPE, idempotency_key)
            replayed = policies.read_idempotent(
                SUBMIT_SCOPE, idempotency_key, connection=conn
            )
            if replayed is not None:
                if replayed["fingerprint"] != fingerprint:
                    raise ApiProblem(
                        409,
                        "IDEMPOTENCY_KEY_REUSED",
                        "同一个 Idempotency-Key 已用于另一次提交，请换一个新的键。",
                    )
                draft = PolicyDraft.model_validate(replayed["response"])
                response.headers["ETag"] = f'"{draft.revision}"'
                return draft

            locked = discovery.lock_candidate(conn, candidateId)
            if locked is None:
                raise _not_found()
            _ensure_submittable(locked, expected_revision)

            # 正文落盘放在事务里：重放路径在写之前就返回了，不会留下没人引用的对象。
            # 演示环境是本地磁盘写入，长度可忽略；对象存储模式下一次 PUT 会占住这个
            # 事务，但提交候选是低频管理操作，换来的是幂等与草稿严格同事务。
            contents = locked["body_text"].encode("utf-8")
            storage_key = request.app.state.object_store.put(contents, suffix=".txt")

            metadata = _policy_metadata(locked)
            sources = [
                PreparedSource(
                    kind="body",
                    file_name=f"discovery-{candidateId}.txt",
                    mime_type="text/plain",
                    text=locked["body_text"],
                    content_hash=locked["content_hash"],
                    byte_size=len(contents),
                    source_url=locked["source_url"],
                )
            ]
            draft = policies.create_policy_version(
                metadata=metadata,
                sources=sources,
                storage_keys=[storage_key],
                trace_id=trace_id_of(request),
                started_at=datetime.now(timezone.utc),
                connection=conn,
            )
            records, findings = _criteria_for_draft(locked, draft)
            saved = policies.save_criteria(
                policy_id=draft.id,
                version=draft.version,
                expected_revision=draft.revision,
                criteria=records,
                findings=findings,
                status="pending_review",
                trace_id=trace_id_of(request),
                started_at=datetime.now(timezone.utc),
                connection=conn,
            )
            discovery.mark_submitted(
                conn,
                candidate_id=candidateId,
                policy_id=draft.id,
                version=draft.version,
                now=datetime.now(timezone.utc),
            )
            policies.save_idempotent(
                SUBMIT_SCOPE,
                idempotency_key,
                fingerprint,
                saved.model_dump(by_alias=True, mode="json"),
                saved.revision,
                usage_semantics="explicit",
                connection=conn,
            )
    except BaseException:
        # 事务没提交（或提交失败）：刚写的正文没有任何草稿引用它，就地删掉。
        # 重放/校验失败发生在落盘之前，此时没有对象可删。
        if storage_key is not None:
            _delete_object(request, storage_key)
        raise

    response.headers["ETag"] = f'"{saved.revision}"'
    return saved


# ------------------------------------------------------------------- DISC-08


@router.post(
    "/policy-candidates/{candidateId}/discard",
    response_model=CandidateDetail,
    operation_id="discardPolicyCandidate",
    summary="DISC-08",
)
def discard_policy_candidate(
    request: Request,
    response: Response,
    candidateId: UUID,
    body: DiscardCandidateInput | None = None,
) -> CandidateDetail:
    require_reviewer_write(request)
    expected_revision = require_if_match(request)
    discovery = request.app.state.discovery
    reason = body.reason if body is not None else None

    with request.app.state.db.connection() as conn, conn.transaction():
        row = discovery.lock_candidate(conn, candidateId)
        if row is None:
            raise _not_found()
        if row["revision"] != expected_revision:
            raise _version_conflict(row)
        if row["status"] not in _EDITABLE:
            raise _state_conflict(row)
        updated = discovery.mark_discarded(
            conn, candidate_id=candidateId, reason=reason, now=datetime.now(timezone.utc)
        )
    response.headers["ETag"] = f'"{updated["revision"]}"'
    return _candidate_detail(updated)


# ------------------------------------------------------------------ 读模型

_SOURCE_FIELDS = (
    "url",
    "status",
    "links",
    "candidates",
    "articleFailures",
    "errorCategory",
    "message",
)


def _settings_of(row: dict[str, Any]) -> DiscoverySettings:
    return DiscoverySettings(
        enabled=row["enabled"],
        source_urls=list(row["source_urls"] or []),
        interval_minutes=row["interval_minutes"],
        time_zone=row["time_zone"],
        next_run_at=row["next_run_at"],
        limits=_limits(),
    )


def _run_of(row: dict[str, Any]) -> DiscoveryRun:
    return DiscoveryRun(
        id=row["id"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        status=row["status"],
        terminal_reason=row["terminal_reason"],
        sources_checked=row["sources_checked"],
        links_seen=row["links_seen"],
        duplicates=row["duplicates"],
        new_candidates=row["new_candidates"],
        failed_sources=row["failed_sources"],
        failed_articles=row["failed_articles"],
        tokens_used=row["tokens_used"],
        tokens_unknown=row["tokens_unknown"],
        late_by_seconds=row["late_by_seconds"],
        source_status=[_source_status(item) for item in row["source_status"] or []],
        detail=row["detail"],
    )


def _source_status(item: dict[str, Any]) -> Any:
    from ..schemas import DiscoverySourceStatus

    return DiscoverySourceStatus(**{key: item.get(key) for key in _SOURCE_FIELDS})


def _candidate_metadata(row: dict[str, Any]) -> CandidateMetadata:
    return CandidateMetadata(
        version=row["version"],
        policy_id=row["policy_id"],
        title=row["title"],
        policy_type=row["policy_type"],
        issuer=row["issuer"],
        region=row["region"],
        document_number=row["document_number"],
        applicable_entities=row["applicable_entities"],
        published_at=row["published_at"],
        effective_from=row["effective_from"],
        effective_to=row["effective_to"],
        application_window=_window(row["application_window"]),
        official_entry_url=row["official_entry_url"],
    )


def _candidate_summary(row: dict[str, Any]) -> CandidateSummary:
    return CandidateSummary(
        id=row["id"],
        run_id=row["run_id"],
        revision=row["revision"],
        status=row["status"],
        source_url=row["source_url"],
        fetched_at=row["fetched_at"],
        content_hash=row["content_hash"],
        title=row["title"],
        policy_type=row["policy_type"],
        issuer=row["issuer"],
        missing_fields=list(row["missing_fields"] or []),
        duplicate_of=row["duplicate_of"],
        existing_policy_id=row["existing_policy_id"],
        existing_version=row["existing_version"],
        version_changed=row["version_changed"],
        submitted_policy_id=row["submitted_policy_id"],
        submitted_version=row["submitted_version"],
        created_at=row["created_at"],
    )


def _attachment_finding(row: dict[str, Any]) -> dict[str, Any] | None:
    """页面有附件时按 ``attachment_urls`` 派生一条复核提示。

    候选侧这条提示**不写进候选的 ``validation_findings`` 列**：那一列放的是条件级结论，
    DISC-06 保存 ``criteriaDrafts`` 时会整体重算，存进去的副本第一次编辑就没了。它由
    ``attachment_urls`` 派生，因此编辑不掉。

    提交（DISC-07）时它随草稿作为来源级 finding 落进 ``policy_validation_finding``，
    之后 API-05 重新抽取、API-06 保存条款整体替换 findings 时，由
    ``PolicyRepository`` 按 :data:`PRESERVED_SOURCE_FINDING_CODES` 从库里保留——
    来源没被完整读取这件事不会因为某条条件被改好而消失。附件本身写入后不再变化，
    所以保留的副本也不会与 ``attachment_urls`` 漂移。
    """

    attachments = list(row["attachment_urls"] or [])
    if not attachments:
        return None
    return {
        "criterionId": None,
        "code": ATTACHMENT_NOT_EXTRACTED,
        "message": (
            f"该页面另有 {len(attachments)} 个附件（如 PDF/DOCX），本轮未下载、未纳入抽取；"
            "条件若写在附件里，请在保存条件时人工补录。"
        ),
        "sourceSpans": [],
    }


def _draft_level_findings(row: dict[str, Any]) -> list[FindingRecord]:
    """草稿里的来源级提示：与候选详情看到的是同一条，来源同样是 ``attachment_urls``。

    提交时随草稿一起落库；此后重新保存条款或重新抽取都会把它原样带回（见
    ``PolicyRepository.save_criteria``）。
    """

    note = _attachment_finding(row)
    if note is None:
        return []
    return [
        FindingRecord(
            criterion_id=None,
            code=note["code"],
            message=note["message"],
            spans=[],
        )
    ]


def _candidate_detail(row: dict[str, Any]) -> CandidateDetail:
    extraction = row["extraction"] or {}
    usage = extraction.get("usage")
    notes = [_attachment_finding(row)]
    return CandidateDetail(
        id=row["id"],
        run_id=row["run_id"],
        revision=row["revision"],
        status=row["status"],
        source_url=row["source_url"],
        fetched_at=row["fetched_at"],
        content_hash=row["content_hash"],
        raw_hash=row["raw_hash"],
        metadata=_candidate_metadata(row),
        attachment_urls=list(row["attachment_urls"] or []),
        missing_fields=list(row["missing_fields"] or []),
        body_text=row["body_text"],
        criteria=[Criterion.model_validate(item) for item in row["criteria"] or []],
        validation_findings=[
            ValidationFinding.model_validate(item)
            for item in [*(row["validation_findings"] or []), *(n for n in notes if n)]
        ],
        usage=ModelUsage.model_validate(usage) if usage else None,
        duplicate_of=row["duplicate_of"],
        existing_policy_id=row["existing_policy_id"],
        existing_version=row["existing_version"],
        version_changed=row["version_changed"],
        submitted_policy_id=row["submitted_policy_id"],
        submitted_version=row["submitted_version"],
        discard_reason=row["discard_reason"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# ------------------------------------------------------------------ 校验


def require_reviewer_read(request: Request) -> None:
    """读接口：审核员会话或只读运维 Bearer（与 API-15/16 同一口径）。"""

    from ..identity import require_reviewer_or_bearer

    require_reviewer_or_bearer(request)


def _load_candidate(request: Request, candidate_id: UUID) -> dict[str, Any]:
    row = request.app.state.discovery.get_candidate(candidate_id)
    if row is None:
        raise _not_found()
    return row


def _not_found() -> ApiProblem:
    return ApiProblem(404, "CANDIDATE_NOT_FOUND", "候选不存在或已被清理。")


def _version_conflict(row: dict[str, Any]) -> ApiProblem:
    return ApiProblem(
        409,
        "VERSION_CONFLICT",
        "候选已被更新，请重新读取后再操作。",
        {"currentRevision": str(row["revision"])},
    )


def _state_conflict(row: dict[str, Any]) -> ApiProblem:
    return ApiProblem(
        409,
        "CANDIDATE_STATE_CONFLICT",
        _STATE_MESSAGES.get(row["status"], "当前候选状态不允许该操作。"),
        {"currentStatus": row["status"]},
    )


_STATE_MESSAGES = {
    "submitted": "候选已经提交为草稿，不能再次处理。",
    "discarded": "候选已被丢弃。",
    "expired": "候选已随上一轮抓取过期，请从新的候选中处理。",
    "extraction_failed": "候选抽取失败，请先补齐结构化条件再提交。",
}


def _ensure_editable(row: dict[str, Any], expected_revision: UUID) -> None:
    if row["revision"] != expected_revision:
        raise _version_conflict(row)
    if row["status"] not in _EDITABLE:
        raise _state_conflict(row)


def _ensure_submittable(row: dict[str, Any], expected_revision: UUID) -> None:
    if row["revision"] != expected_revision:
        raise _version_conflict(row)
    if row["status"] == "pending_review":
        pass
    elif row["status"] in ("submitted", "discarded", "expired"):
        raise ApiProblem(
            409,
            "CANDIDATE_STATE_CONFLICT",
            _STATE_MESSAGES[row["status"]],
            {
                "currentStatus": row["status"],
                "submittedPolicyId": str(row["submitted_policy_id"])
                if row["submitted_policy_id"]
                else None,
                "submittedVersion": row["submitted_version"],
            },
        )
    else:
        raise _state_conflict(row)

    missing = list(row["missing_fields"] or [])
    if missing:
        raise ApiProblem(
            422,
            "VALIDATION_ERROR",
            "候选还有必填字段没有确认，请补齐后再提交。",
            {
                "fieldErrors": [
                    {"field": field, "message": "提交前必须确认该字段"} for field in missing
                ]
            },
        )
    if not row["criteria"]:
        raise ApiProblem(
            422,
            "VALIDATION_ERROR",
            "提交前必须有至少一条结构化的资格条件。",
            {"fieldErrors": [{"field": "criteria", "message": "请先保存结构化条件"}]},
        )


def _missing_fields(row: dict[str, Any]) -> list[str]:
    """提交前仍缺哪些必填字段；``sourceUrl`` 来自取证字段，不在待补之列。

    与抽取侧共用 :func:`app.discovery.metadata.missing_fields_of` 与
    :data:`FIELD_COLUMNS`：两处各写一份字段映射时，「哪些还缺」会在抓取与人工确认
    之间得出不同答案。
    """

    return missing_fields_of(row)


def _check_ordering(metadata: CandidateMetadata) -> None:
    problems: list[dict[str, str]] = []
    published = metadata.published_at
    effective_from = metadata.effective_from
    effective_to = metadata.effective_to
    if published and effective_from and published > effective_from:
        problems.append({"field": "effectiveFrom", "message": "生效日期不能早于发布日期"})
    if effective_to and effective_from and effective_to < effective_from:
        problems.append({"field": "effectiveTo", "message": "失效日期不能早于生效日期"})
    window = metadata.application_window
    if window is not None and window.closes_at <= window.opens_at:
        problems.append(
            {"field": "applicationWindow.closesAt", "message": "申报截止时间必须晚于开始时间"}
        )
    if problems:
        raise ApiProblem(
            422, "VALIDATION_ERROR", "日期或申报窗口的先后顺序不合法。", {"fieldErrors": problems}
        )


def _field_problem(field: str, message: str) -> ApiProblem:
    return ApiProblem(
        422,
        "VALIDATION_ERROR",
        message,
        {"fieldErrors": [{"field": field, "message": message}]},
    )


# ------------------------------------------------------------- 提交链路


def _submit_fingerprint(candidate_id: UUID, revision: UUID) -> str:
    payload = f"{candidate_id}:{revision}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _policy_metadata(row: dict[str, Any]):
    from ..schemas import PolicyImportMetadata

    payload = {
        "policy_id": row["policy_id"],
        "version": row["version"],
        "title": row["title"],
        "type": row["policy_type"],
        "issuer": row["issuer"],
        "region": row["region"],
        "document_number": row["document_number"],
        "applicable_entities": list(row["applicable_entities"] or []),
        "published_at": row["published_at"],
        "effective_from": row["effective_from"],
        "effective_to": row["effective_to"],
        "application_window": _window(row["application_window"]),
        "source_url": row["source_url"],
        "official_entry_url": row["official_entry_url"],
    }
    try:
        return PolicyImportMetadata.model_validate(payload)
    except ValidationError as exc:
        # 理论上已被 missingFields 挡在前面；这里兜底成 422，而不是 500。
        raise ApiProblem(
            422,
            "VALIDATION_ERROR",
            "候选元数据不完整，无法生成政策草稿。",
            {
                "fieldErrors": [
                    {"field": ".".join(str(part) for part in error["loc"]), "message": error["msg"]}
                    for error in exc.errors()
                ]
            },
        ) from exc


def _window(raw: Any):
    from ..schemas import ApplicationWindow

    if not raw:
        return None
    return ApplicationWindow.model_validate(raw)


def _criterion_input(item: Any) -> CriterionInput:
    return CriterionInput(
        id=item.id,
        title=item.title,
        review_finding=item.review_finding,
        spans=tuple(_span_of(span) for span in item.source_spans),
        expression=item.expression if isinstance(item.expression, dict) else None,
    )


def _span_of(span: Any) -> SourceSpanCheck:
    return SourceSpanCheck(
        source_id=span.source_id,
        source_url=span.source_url,
        source_version=span.source_version,
        page=span.page,
        section=span.section,
        quote=span.quote,
        text_hash=span.text_hash,
    )


def _criteria_for_draft(
    row: dict[str, Any], draft: PolicyDraft
) -> tuple[list[CriterionRecord], list[FindingRecord]]:
    """把候选里保存的条件改挂到新草稿的来源与版本上，并重新校验。

    改的是 ``sourceId`` / ``sourceUrl`` / ``sourceVersion`` 三个**指向**字段：引文
    正文与 ``textHash`` 原样保留，校验会逐条确认「这段引文确实还在这份正文里」。
    """

    source = draft.sources[0]
    items: list[CriterionInput] = []
    for item in row["criteria"] or []:
        spans = []
        for span in item.get("sourceSpans") or []:
            spans.append(
                SourceSpanCheck(
                    source_id=source.id,
                    source_url=source.source_url,
                    source_version=draft.version,
                    page=span.get("page"),
                    section=span.get("section"),
                    quote=span.get("quote") or "",
                    text_hash=span.get("textHash") or "",
                )
            )
        items.append(
            CriterionInput(
                id=UUID(item["id"]),
                title=item["title"],
                review_finding=item.get("reviewFinding") or "none",
                spans=tuple(spans),
                expression=item.get("expression"),
            )
        )

    texts = {source.id: SourceText(id=source.id, source_url=source.source_url, text=source.text)}
    packed, findings = pack_criteria(items, texts, draft.version)
    return (
        [
            CriterionRecord(
                id=item.id,
                title=item.title,
                expression=item.expression,
                spans=item.spans,
                review_finding=item.review_finding,
                validation_status=item.validation_status,
            )
            for item in packed
        ],
        [
            FindingRecord(
                criterion_id=item.criterion_id,
                code=item.code,
                message=item.message,
                spans=item.spans,
            )
            for item in findings
        ]
        + _draft_level_findings(row),
    )


def _delete_object(request: Request, storage_key: str) -> None:
    try:
        request.app.state.object_store.delete(storage_key)
    except Exception:  # noqa: BLE001 - 清理失败不能盖掉原始异常
        logger.warning("提交失败后清理对象未成功，需人工核对 storage_key=%s", storage_key)


__all__ = ["router"]
