"""后台政策接口：API-04 导入、API-15 列表、API-16 详情。

导入是「先校验、再落库」：文件与元数据全部通过后，才写对象存储并开启单个数据库
事务；任一步失败都会清理已写入的对象，避免留下孤儿文件或半条记录。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException

from ..agents.extraction import ExtractionSource, extract_criteria
from ..agents.ingestion import (
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_METADATA_BYTES,
    MAX_REQUEST_BYTES,
    PreparedSource,
    prepare_sources,
    validate_official_url,
)
from ..domain.citations import CriterionInput, SourceSpanCheck, SourceText, validate_criteria
from ..domain.rules import (
    POLICY_BLOCKING_CODES,
    POLICY_UNSATISFIABLE,
    CriterionRule,
    policy_contradictions,
    validate_expression,
)
from ..errors import ApiProblem, trace_id_of
from ..integrations.storage import CriterionRecord, FindingRecord
from ..schemas import (
    DraftStatus,
    ExtractPolicyInput,
    ExtractionResult,
    PolicyDetail,
    PolicyDraft,
    PolicyDraftList,
    PolicyImportMetadata,
    PublishPolicyInput,
    SaveCriteriaInput,
    SaveCriteriaResult,
)
from ..security import require_admin

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)

_MAX_PART_KB = MAX_FILE_BYTES // 1024
ALLOWED_FORM_PARTS = frozenset({"metadata", "files"})


@router.post(
    "/policies/import",
    status_code=201,
    response_model=PolicyDraft,
    operation_id="importPolicy",
    summary="API-04",
)
async def import_policy(request: Request, response: Response) -> PolicyDraft:
    started_at = datetime.now(timezone.utc)

    content_type = request.headers.get("content-type", "")
    if not content_type.lower().startswith("multipart/form-data"):
        raise ApiProblem(
            415,
            "UNSUPPORTED_MEDIA_TYPE",
            "导入接口只接受 multipart/form-data 请求。",
        )

    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > MAX_REQUEST_BYTES:
                raise ApiProblem(413, "FILE_TOO_LARGE", "请求体超过 51 MiB 上限。")
        except ValueError as exc:
            raise ApiProblem(400, "INVALID_REQUEST", "Content-Length 不是合法整数。") from exc

    try:
        form = await request.form(
            max_files=MAX_FILES,
            max_fields=1,
            max_part_size=MAX_FILE_BYTES,
        )
    except StarletteHTTPException as exc:
        # Starlette 把 Multipart 解析失败包成 400 HTTPException，这里换回契约错误码。
        raise _multipart_problem(str(exc.detail)) from exc
    except MultiPartException as exc:  # 直接调用路由（无 app 作用域）时
        raise _multipart_problem(str(exc)) from exc

    metadata = _parse_metadata(form.get("metadata"))

    raw_files: list[tuple[str, str | None, bytes]] = []
    for name, value in form.multi_items():
        # 未知字段（尤其是把 files 拼成 file）不能静默忽略：那样会返回 201 但正文丢失。
        if name not in ALLOWED_FORM_PARTS:
            raise ApiProblem(
                422,
                "UNEXPECTED_FIELD",
                "表单包含导入接口未定义的字段，只接受 metadata 与 files。",
                {"fieldErrors": [{"field": _display_name(name), "message": "未定义的字段名"}]},
            )
        if name == "metadata":
            continue
        if not isinstance(value, UploadFile):
            raise ApiProblem(
                422,
                "UNEXPECTED_FIELD",
                "files 字段的每个部分都必须是文件。",
                {"fieldErrors": [{"field": "files", "message": "该字段必须是文件"}]},
            )
        if value.size is not None and value.size > MAX_FILE_BYTES:
            raise ApiProblem(413, "FILE_TOO_LARGE", "单个文件不能超过 10 MiB。")
        raw_files.append((value.filename or "source", value.content_type, await value.read()))

    if len(raw_files) > MAX_FILES:
        raise ApiProblem(422, "TOO_MANY_FILES", f"一次最多上传 {MAX_FILES} 个文件。")

    prepared = await run_in_threadpool(prepare_sources, metadata, raw_files)

    draft = await run_in_threadpool(
        _store_and_persist,
        request.app.state.object_store,
        request.app.state.repository,
        metadata,
        prepared,
        [data for _, _, data in raw_files],
        trace_id_of(request),
        started_at,
    )

    response.headers["ETag"] = f'"{draft.revision}"'
    return draft


@router.get(
    "/policies",
    response_model=PolicyDraftList,
    operation_id="listAdminPolicies",
    summary="API-15",
)
def list_admin_policies(
    request: Request,
    status: DraftStatus | None = Query(default=None),
    policy_type: Literal["tax", "grant"] | None = Query(default=None, alias="type"),
    cursor: str | None = Query(default=None, min_length=1),
    limit: int = Query(default=20, ge=1, le=50),
) -> PolicyDraftList:
    return request.app.state.repository.list_admin_policies(
        status=status,
        policy_type=policy_type,
        cursor=cursor,
        limit=limit,
    )


@router.get(
    "/policies/{policyId}",
    response_model=PolicyDraft,
    operation_id="getAdminPolicy",
    summary="API-16",
)
def get_admin_policy(
    request: Request,
    response: Response,
    policyId: UUID,
    version: str | None = Query(default=None, min_length=1),
) -> PolicyDraft:
    draft = request.app.state.repository.get_admin_policy(policyId, version)
    response.headers["ETag"] = f'"{draft.revision}"'
    return draft


# ------------------------------------------------------ API-05 / 06 / 07


@router.post(
    "/policies/{policyId}/extract",
    response_model=ExtractionResult,
    operation_id="extractPolicy",
    summary="API-05",
)
async def extract_policy(
    request: Request,
    response: Response,
    policyId: UUID,
    body: ExtractPolicyInput,
) -> ExtractionResult:
    started_at = datetime.now(timezone.utc)
    expected_revision = _require_if_match(request)
    idempotency_key = _require_idempotency_key(request)
    repository = request.app.state.repository

    draft = await run_in_threadpool(repository.get_version_for_edit, policyId, body.version)
    _ensure_editable(draft)

    fingerprint = _fingerprint(policyId, body)
    replayed = await run_in_threadpool(repository.read_idempotent, "extract", idempotency_key)
    if replayed is not None:
        if replayed["fingerprint"] != fingerprint:
            raise ApiProblem(
                409,
                "IDEMPOTENCY_CONFLICT",
                "同一个 Idempotency-Key 已用于不同的抽取请求，请换一个新的键。",
            )
        if str(replayed["revision"]) != str(draft.revision):
            raise ApiProblem(
                409,
                "VERSION_CONFLICT",
                "草稿已被更新，不能重放旧结果，请重新读取后再抽取。",
                {"currentVersion": str(draft.revision)},
            )
        response.headers["ETag"] = f'"{draft.revision}"'
        return ExtractionResult.model_validate(replayed["response"])

    outcome = await run_in_threadpool(
        extract_criteria,
        version=draft.version,
        sources=_extraction_sources(draft),
        llm=request.app.state.llm,
        model_version=body.model_version,
    )
    drafts = [
        _domain_criterion(item.id, item.title, item.expression, item.spans, item.review_finding)
        for item in outcome.criteria
    ]
    records, findings = _collect_validation(draft, drafts, extra=outcome.findings)

    saved = await run_in_threadpool(
        repository.save_criteria,
        policy_id=policyId,
        version=draft.version,
        expected_revision=expected_revision,
        criteria=records,
        findings=findings,
        status="pending_review",
        trace_id=trace_id_of(request),
        started_at=started_at,
    )
    result = ExtractionResult(
        draft=saved,
        criteria_drafts=saved.criteria,
        validation_findings=saved.validation_findings,
        usage=outcome.usage,
    )
    await run_in_threadpool(
        repository.save_idempotent,
        "extract",
        idempotency_key,
        fingerprint,
        result.model_dump(by_alias=True, mode="json"),
        saved.revision,
    )
    response.headers["ETag"] = f'"{saved.revision}"'
    return result


@router.put(
    "/policies/{policyId}/criteria",
    response_model=SaveCriteriaResult,
    operation_id="saveCriteria",
    summary="API-06",
)
def save_criteria(
    request: Request,
    response: Response,
    policyId: UUID,
    body: SaveCriteriaInput,
) -> SaveCriteriaResult:
    started_at = datetime.now(timezone.utc)
    expected_revision = _require_if_match(request)
    repository = request.app.state.repository

    draft = repository.get_version_for_edit(policyId, body.version)
    _ensure_editable(draft)

    ids = [item.id for item in body.criteria]
    if len(set(ids)) != len(ids):
        raise ApiProblem(
            422,
            "DUPLICATE_CRITERION_ID",
            "同一政策版本内条款 ID 不能重复。",
            {"fieldErrors": [{"field": "criteria.id", "message": "存在重复的条款 ID"}]},
        )

    drafts = [
        _domain_criterion(
            item.id, item.title, item.expression, item.source_spans, item.review_finding
        )
        for item in body.criteria
    ]
    records, findings = _collect_validation(draft, drafts)

    saved = repository.save_criteria(
        policy_id=policyId,
        version=draft.version,
        expected_revision=expected_revision,
        criteria=records,
        findings=findings,
        status="pending_review",
        trace_id=trace_id_of(request),
        started_at=started_at,
    )
    response.headers["ETag"] = f'"{saved.revision}"'
    return SaveCriteriaResult(
        draft=saved, criteria=saved.criteria, validation_findings=saved.validation_findings
    )


@router.post(
    "/policies/{policyId}/publish",
    response_model=PolicyDetail,
    operation_id="publishPolicy",
    summary="API-07",
)
def publish_policy(
    request: Request,
    response: Response,
    policyId: UUID,
    body: PublishPolicyInput,
) -> PolicyDetail:
    started_at = datetime.now(timezone.utc)
    expected_revision = _require_if_match(request)
    repository = request.app.state.repository

    draft = repository.get_version_for_edit(policyId, body.version)
    if draft.status in ("published", "closed"):
        raise ApiProblem(
            409,
            "POLICY_ALREADY_PUBLISHED",
            "该版本已发布，不能重复发布；如需修改请导入新版本。",
            {"currentVersion": str(draft.revision)},
        )

    # 发布前重算引用校验：不能信任草稿里存着的 status，更不能信任前端传入的结论。
    drafts = [
        _domain_criterion(item.id, item.title, item.expression, item.source_spans, item.review_finding)
        for item in draft.criteria
    ]
    _, findings = _collect_validation(draft, drafts)
    blocking = [item for item in draft.criteria if item.validation_status != "passed"]
    # 政策级 finding 没有 criterion_id，但「合取为空」这类同样必须挡住发布：这样的政策
    # 发布出去，任何企业查到的都是「不达标」，比不放出来更误导。
    unsupported = [
        finding
        for finding in findings
        if finding.criterion_id is not None or finding.code in POLICY_BLOCKING_CODES
    ]
    if blocking or unsupported:
        unsatisfiable = any(finding.code == POLICY_UNSATISFIABLE for finding in findings)
        raise ApiProblem(
            422,
            "CRITERIA_NOT_VALIDATED",
            (
                "存在互斥的条件，政策按「且」合并后永远无法满足；请把「或」的分支写进同一条条件，"
                "修正后重新保存再发布。"
                if unsatisfiable
                else "存在引用校验未通过的条件，请修正后重新保存，再执行发布。"
            ),
            {
                "criterionIds": [str(item.id) for item in blocking],
                "findings": [
                    {
                        "criterionId": str(finding.criterion_id) if finding.criterion_id else None,
                        "code": finding.code,
                        "message": finding.message,
                    }
                    for finding in unsupported
                ],
            },
        )

    saved = repository.publish(
        policy_id=policyId,
        version=draft.version,
        expected_revision=expected_revision,
        trace_id=trace_id_of(request),
        started_at=started_at,
    )
    detail = repository.get_policy_detail(policyId, draft.version)
    response.headers["ETag"] = f'"{saved.revision}"'
    return detail


# ------------------------------------------------------------- 写请求公共校验


def _require_if_match(request: Request) -> UUID:
    raw = request.headers.get("if-match")
    if raw is None or not raw.strip():
        raise ApiProblem(
            428,
            "MISSING_IF_MATCH",
            "写请求必须带 If-Match：取自上一步响应里的 ETag，原样回传。",
        )
    value = raw.strip()
    if value == "*" or value.upper().startswith("W/"):
        raise ApiProblem(400, "INVALID_IF_MATCH", "If-Match 只接受强 ETag，不接受 * 或弱校验。")
    if value.startswith('"') and value.endswith('"') and len(value) >= 2:
        value = value[1:-1]
    try:
        return UUID(value)
    except ValueError as exc:
        raise ApiProblem(400, "INVALID_IF_MATCH", "If-Match 不是合法的草稿 revision。") from exc


def _require_idempotency_key(request: Request) -> UUID:
    raw = (request.headers.get("idempotency-key") or "").strip()
    if not raw:
        raise ApiProblem(
            400,
            "MISSING_IDEMPOTENCY_KEY",
            "抽取接口必须带 Idempotency-Key（UUID），用于防止重复抽取与重复计费。",
        )
    try:
        return UUID(raw)
    except ValueError as exc:
        raise ApiProblem(400, "INVALID_IDEMPOTENCY_KEY", "Idempotency-Key 必须是 UUID。") from exc


def _ensure_editable(draft: PolicyDraft) -> None:
    if draft.status in ("published", "closed"):
        raise ApiProblem(
            409,
            "POLICY_ALREADY_PUBLISHED",
            "已发布的版本不能再抽取或修改；如需修订请导入新版本。",
            {"currentVersion": str(draft.revision)},
        )


def _fingerprint(policy_id: UUID, body: ExtractPolicyInput) -> str:
    payload = json.dumps(
        {"policyId": str(policy_id), **body.model_dump(by_alias=True, mode="json")},
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _extraction_sources(draft: PolicyDraft) -> list[ExtractionSource]:
    return [
        ExtractionSource(id=source.id, source_url=source.source_url, text=source.text, kind=source.kind)
        for source in draft.sources
    ]


def _source_texts(draft: PolicyDraft) -> dict[UUID, SourceText]:
    return {
        source.id: SourceText(
            id=source.id, source_url=source.source_url, text=source.text, kind=source.kind
        )
        for source in draft.sources
    }


def _domain_criterion(
    criterion_id: UUID,
    title: str,
    expression: Any,
    spans: Any,
    review_finding: str,
) -> CriterionInput:
    return CriterionInput(
        id=criterion_id,
        title=title,
        review_finding=review_finding,
        spans=tuple(_domain_span(span) for span in spans),
        expression=expression if isinstance(expression, dict) else None,
    )


def _domain_span(span: Any) -> SourceSpanCheck:
    return SourceSpanCheck(
        source_id=span.source_id,
        source_url=span.source_url,
        source_version=span.source_version,
        page=span.page,
        section=span.section,
        quote=span.quote,
        text_hash=span.text_hash,
    )


def _collect_validation(
    draft: PolicyDraft,
    criteria: list[CriterionInput],
    *,
    extra: Any = (),
) -> tuple[list[CriterionRecord], list[FindingRecord]]:
    """调用领域校验并整理成可直接落库的记录；路由自身不做判断。"""

    sources = _source_texts(draft)
    verdicts = validate_criteria(criteria, sources, draft.version)
    ast_problems = {
        criterion.id: validate_expression(criterion.expression or {}) for criterion in criteria
    }

    records: list[CriterionRecord] = []
    findings: list[FindingRecord] = []
    for verdict, criterion in zip(verdicts, criteria):
        problems = ast_problems[criterion.id]
        wire_spans = [span.to_wire() for span in criterion.spans]
        records.append(
            CriterionRecord(
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
                FindingRecord(
                    criterion_id=criterion.id,
                    code=problem.code,
                    message=problem.message,
                    spans=wire_spans,
                )
            )
        for finding in verdict.findings:
            findings.append(
                FindingRecord(
                    criterion_id=criterion.id,
                    code=finding.code,
                    message=finding.message,
                    spans=[span.to_wire() for span in finding.source_spans],
                )
            )

    # 跨条款的「合取为空」只能在这里查：单条 AST 各自合法，合起来却永远不成立。
    # 发现作为政策级 finding 落库（criterion_id 为空），不需要改契约或建表。
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
            FindingRecord(
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
            FindingRecord(
                criterion_id=None,
                code=finding.code,
                message=finding.message,
                spans=[span.to_wire() for span in finding.source_spans],
            )
        )
    return records, findings


def _multipart_problem(message: str) -> ApiProblem:
    text = message.lower()
    if "too many files" in text:
        return ApiProblem(422, "TOO_MANY_FILES", f"一次最多上传 {MAX_FILES} 个文件。")
    if "too many fields" in text:
        return ApiProblem(
            422,
            "UNEXPECTED_FIELD",
            "除了 metadata 之外不接受其他表单字段。",
        )
    if "exceeded maximum size" in text:
        return ApiProblem(413, "FILE_TOO_LARGE", f"单个文件或字段不能超过 {_MAX_PART_KB} KB。")
    return ApiProblem(400, "INVALID_MULTIPART", "multipart 请求格式不合法，请检查后重试。")


def _parse_metadata(raw: Any) -> PolicyImportMetadata:
    if not isinstance(raw, str) or not raw.strip():
        raise ApiProblem(
            422,
            "VALIDATION_ERROR",
            "缺少 metadata 表单字段。",
            {"fieldErrors": [{"field": "metadata", "message": "必填，且需为 JSON 对象"}]},
        )
    if len(raw.encode("utf-8")) > MAX_METADATA_BYTES:
        raise ApiProblem(413, "FILE_TOO_LARGE", "metadata 字段超过 64 KiB 上限。")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ApiProblem(400, "INVALID_METADATA", "metadata 不是合法的 JSON。") from exc
    if not isinstance(payload, dict):
        raise ApiProblem(400, "INVALID_METADATA", "metadata 必须是 JSON 对象。")

    try:
        metadata = PolicyImportMetadata.model_validate(payload)
    except ValidationError as exc:
        raise ApiProblem(
            422,
            "VALIDATION_ERROR",
            "metadata 字段不合法，请按契约修正后重试。",
            {"fieldErrors": _field_errors(exc)},
        ) from exc

    _check_ordering(metadata)
    validate_official_url(metadata.source_url, "sourceUrl")
    validate_official_url(metadata.official_entry_url, "officialEntryUrl")
    return metadata


def _display_name(name: str) -> str:
    """只回显字段名本身，去掉控制字符并限长，避免把任意请求内容塞进响应。"""
    cleaned = "".join(ch for ch in name if ch.isprintable())[:40]
    return cleaned or "(空字段名)"


def _field_errors(exc: ValidationError) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    for error in exc.errors():
        location = [str(part) for part in error.get("loc", ())]
        field = ".".join(location) or "metadata"
        errors.append({"field": field, "message": str(error.get("msg", "取值不合法"))})
    return errors


def _check_ordering(metadata: PolicyImportMetadata) -> None:
    problems: list[dict[str, str]] = []
    if metadata.published_at > metadata.effective_from:
        problems.append({"field": "effectiveFrom", "message": "生效日期不能早于发布日期"})
    if metadata.effective_to is not None and metadata.effective_to < metadata.effective_from:
        problems.append({"field": "effectiveTo", "message": "失效日期不能早于生效日期"})
    window = metadata.application_window
    if window is not None and window.closes_at <= window.opens_at:
        problems.append(
            {"field": "applicationWindow.closesAt", "message": "申报截止时间必须晚于开始时间"}
        )
    if problems:
        raise ApiProblem(
            422,
            "VALIDATION_ERROR",
            "日期或申报窗口的先后顺序不合法。",
            {"fieldErrors": problems},
        )


def _store_and_persist(
    object_store,
    repository,
    metadata: PolicyImportMetadata,
    prepared: list[PreparedSource],
    raw_files: list[bytes],
    trace_id: str,
    started_at: datetime,
) -> PolicyDraft:
    if len(prepared) != len(raw_files):
        raise ApiProblem(500, "INTERNAL_ERROR", "文件分类结果与上传内容不一致。")

    storage_keys: list[str] = []
    try:
        for source, data in zip(prepared, raw_files):
            suffix = ".pdf" if source.mime_type == "application/pdf" else ".txt"
            storage_keys.append(object_store.put(data, suffix=suffix))
        return repository.create_policy_version(
            metadata=metadata,
            sources=prepared,
            storage_keys=storage_keys,
            trace_id=trace_id,
            started_at=started_at,
        )
    except BaseException:
        for key in storage_keys:
            object_store.delete(key)
        raise
