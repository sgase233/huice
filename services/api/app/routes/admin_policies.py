"""后台政策接口：API-04 导入、API-15 列表、API-16 详情。

导入是「先校验、再落库」：文件与元数据全部通过后，才写对象存储并开启单个数据库
事务；任一步失败都会清理已写入的对象，避免留下孤儿文件或半条记录。
"""

from __future__ import annotations

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

from ..agents.ingestion import (
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_METADATA_BYTES,
    MAX_REQUEST_BYTES,
    PreparedSource,
    prepare_sources,
    validate_official_url,
)
from ..errors import ApiProblem, trace_id_of
from ..schemas import DraftStatus, PolicyDraft, PolicyDraftList, PolicyImportMetadata
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
