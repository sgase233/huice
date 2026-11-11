"""后台政策接口：API-04 导入、API-15 列表、API-16 详情。

导入是「先校验、再落库」：文件与元数据全部通过后，才写对象存储并开启单个数据库
事务；任一步失败都会清理已写入的对象，避免留下孤儿文件或半条记录。
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
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
from ..identity import (
    require_csrf,
    require_reviewer,
    require_reviewer_or_bearer,
)
from ..security import GatedRoute

logger = logging.getLogger(__name__)

#: 门禁与公开接口一致（restricted 下先过外层 demo cookie）；「谁能写」不再由路由器级
#: Bearer 依赖一人拍板：读操作接受审核员会话或只读运维 Bearer，写操作只接受审核员会话。
router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    route_class=GatedRoute,
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
    _require_reviewer_write(request)
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
    require_reviewer_or_bearer(request)
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
    require_reviewer_or_bearer(request)
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
    _require_reviewer_write(request)
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
        return _normalize_replayed_usage(replayed)

    sources = _extraction_sources(draft)
    attempt_started = time.monotonic()
    try:
        outcome = await run_in_threadpool(
            extract_criteria,
            version=draft.version,
            sources=sources,
            llm=request.app.state.llm,
            model_version=body.model_version,
        )
    except ApiProblem as problem:
        # 调用已经发生：结果与用量/失败留痕都要独立于后面的草稿保存事务。
        await _record_failed_extraction(
            request,
            policy_id=policyId,
            draft=draft,
            problem=problem,
            elapsed_ms=int((time.monotonic() - attempt_started) * 1000),
        )
        raise

    usage = outcome.usage
    extraction_model: str | None = None
    if usage is not None:
        # 轨迹里记「这次抽取用了什么」：模型名，或规则抽取的版本号。
        extraction_model = usage.model_version or usage.mode
        if usage.mode == "deepseek":
            # 只有真的调了模型才记一行用量；rule/fixture 路径的 tokens 恒为 0，
            # 记成 model_call 会让人误以为每次抽取都有模型开销。
            # 上游 2xx 却没给用量时记「未知」，不把占位的 0 当成真实零用量。
            known = outcome.usage_known
            logger.info(
                "model_call purpose=extraction outcome=ok usage=%s model=%s prompt=%s "
                "completion=%s elapsed_ms=%d policy_id=%s version=%s",
                "known" if known else "unknown",
                usage.model_version,
                usage.input_tokens if known else "unknown",
                usage.output_tokens if known else "unknown",
                usage.elapsed_ms,
                policyId,
                draft.version,
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
        extraction_model=extraction_model,
        extraction_elapsed_ms=usage.elapsed_ms if usage is not None else 0,
        extraction_input_chars=sum(len(source.text) for source in sources),
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
        # 记录来源：rc.7 起未知一定带 tokensUnknown。重放时靠它区分「rc.7 写下的
        # 已知」与「rc.6 写下的兜底 0」，两者在线路上长得一样。
        usage_semantics="explicit",
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
    _require_reviewer_write(request)
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
    _require_reviewer_write(request)
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


def _require_reviewer_write(request: Request) -> None:
    """后台写操作：审核员会话 + CSRF/Origin。Bearer 在 E1 后只读，写请求一律 403。"""

    identity = require_reviewer(request)
    require_csrf(request, identity)


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


def _normalize_replayed_usage(replayed: dict[str, Any]) -> ExtractionResult:
    """按**记录自己的来源**判断用量语义，把可信不了的用量补标为未知。

    `idempotency_record` 只追加，重放原样返回存储的响应。rc.6 写下的记录里，上游
    `usage` 缺失的两个计数是**各自** `or 0` 兜底的：`120/0` 只说明 prompt 给了、
    completion 没给，`0/45` 反之，`0/0` 两个都缺。所以只看数值判不出用量是否完整——
    「双 0 才可疑」会漏掉部分缺失，「有非零就当已知」同样会漏。

    判据是迁移 007 的 `usage_semantics`：

    * `explicit`（rc.7 起写入）：未知一定带 `tokensUnknown: true`，没有标记即完整
      已知，原样返回；
    * 其余（`legacy`，即 007 之前写入，含回退到 rc.6 期间写入）：`mode=deepseek`
      的用量可能部分或全部是兜底，且无法从数值反推哪一个是补的，一律按「未确认」
      补 `tokensUnknown: true`，数值原样保留供人核对；`fixture`/`saved` 没有发起
      调用，回到的 0 是真实的，不补标。

    只改本次响应视图，不写回记录（该表只追加）。
    """

    result = ExtractionResult.model_validate(replayed["response"])
    usage = result.usage
    if (
        replayed.get("usage_semantics") == "explicit"
        or usage.tokens_unknown
        or usage.mode != "deepseek"
    ):
        return result
    return result.model_copy(
        update={"usage": usage.model_copy(update={"tokens_unknown": True})}
    )


def _fingerprint(policy_id: UUID, body: ExtractPolicyInput) -> str:
    payload = json.dumps(
        {"policyId": str(policy_id), **body.model_dump(by_alias=True, mode="json")},
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


async def _record_failed_extraction(
    request: Request,
    *,
    policy_id: UUID,
    draft: PolicyDraft,
    problem: ApiProblem,
    elapsed_ms: int,
) -> None:
    """抽取失败时留两处痕：一行调用日志 + 一条独立事务的失败轨迹。

    两者都不许替换原来的错误——接口仍按原状态码（429/503）返回，轨迹写不进去
    只记一条告警。这里记的是「调用发生过」这一事实，与后面草稿保存是否成功无关。
    """

    retryable = bool(getattr(problem, "retryable", False))
    usage_unknown = bool(getattr(problem, "usage_unknown", True))
    llm = request.app.state.llm
    model = getattr(llm, "model_name", None) or getattr(llm, "mode", "unknown")

    logger.info(
        "model_call purpose=extraction outcome=error usage=%s model=%s prompt=%s completion=%s "
        "elapsed_ms=%d policy_id=%s version=%s code=%s",
        "unknown" if usage_unknown else "none",
        model,
        "unknown" if usage_unknown else 0,
        "unknown" if usage_unknown else 0,
        elapsed_ms,
        policy_id,
        draft.version,
        problem.code,
    )

    repository = request.app.state.repository
    try:
        await run_in_threadpool(
            repository.record_extraction_failure,
            policy_id=policy_id,
            version=draft.version,
            trace_id=trace_id_of(request),
            status="retryable_error" if retryable else "fatal_error",
            error_code=problem.code,
            model_or_rule_version=model,
            elapsed_ms=elapsed_ms,
            usage_unknown=usage_unknown,
        )
    except Exception:  # noqa: BLE001 - 留痕失败不能盖掉原来的 429/503
        logger.warning(
            "抽取失败步骤未能写入，仅保留调用日志 policy_id=%s version=%s code=%s",
            policy_id,
            draft.version,
            problem.code,
        )


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
