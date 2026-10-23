"""无数据库测试用的假仓储、假对象存储与请求构造工具。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.errors import ApiProblem
from app.schemas import PolicyDraft, PolicyDraftList

ADMIN_TOKEN = "local-dev-admin-token-0001"
REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_PATH = REPO_ROOT / "contracts" / "openapi.yaml"

OFFICIAL_SOURCE_URL = "https://www.sz.gov.cn/zwgk/zfxxgkml/zcfg/content/post_1.html"
OFFICIAL_ENTRY_URL = "https://www.sz.gov.cn/"


class FakeObjectStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def put(self, data: bytes, *, suffix: str = "") -> str:
        key = f"local/{uuid4().hex}{suffix}"
        self.objects[key] = data
        return key

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)
        self.deleted.append(key)


class FakeRepository:
    """内存版仓储，只复述入参，不模拟 SQL 行为。"""

    def __init__(self) -> None:
        self.drafts: list[PolicyDraft] = []
        self.created: list[dict] = []
        self.list_calls: list[dict] = []
        self.get_calls: list[tuple] = []
        self.error: Exception | None = None

    def create_policy_version(
        self,
        *,
        metadata,
        sources,
        storage_keys,
        trace_id,
        started_at=None,
    ) -> PolicyDraft:
        if self.error is not None:
            raise self.error
        draft = build_draft(metadata, sources)
        self.created.append(
            {
                "metadata": metadata,
                "sources": list(sources),
                "storage_keys": list(storage_keys),
                "trace_id": trace_id,
                "started_at": started_at,
            }
        )
        self.drafts.append(draft)
        return draft

    def list_admin_policies(self, *, status, policy_type, cursor, limit) -> PolicyDraftList:
        self.list_calls.append(
            {"status": status, "policy_type": policy_type, "cursor": cursor, "limit": limit}
        )
        items = [
            draft
            for draft in self.drafts
            if (status is None or draft.status == status)
            and (policy_type is None or draft.policy_type == policy_type)
        ]
        return PolicyDraftList(items=items[:limit], next_cursor=None)

    def get_admin_policy(self, policy_id, version) -> PolicyDraft:
        self.get_calls.append((policy_id, version))
        for draft in reversed(self.drafts):
            if draft.id == policy_id and (version is None or draft.version == version):
                return draft
        raise ApiProblem(404, "POLICY_NOT_FOUND", "没有找到对应的政策草稿。")


def build_draft(metadata, sources) -> PolicyDraft:
    now = datetime.now(timezone.utc)
    return PolicyDraft.model_validate(
        {
            "id": str(uuid4()),
            "version": metadata.version,
            "title": metadata.title,
            "type": metadata.policy_type,
            "issuer": metadata.issuer,
            "region": metadata.region,
            "documentNumber": metadata.document_number,
            "applicableEntities": list(metadata.applicable_entities),
            "publishedAt": metadata.published_at.isoformat(),
            "effectiveFrom": metadata.effective_from.isoformat(),
            "effectiveTo": metadata.effective_to.isoformat() if metadata.effective_to else None,
            "applicationWindow": {
                "opensAt": metadata.application_window.opens_at.isoformat(),
                "closesAt": metadata.application_window.closes_at.isoformat(),
            }
            if metadata.application_window
            else None,
            "sourceUrl": metadata.source_url,
            "officialEntryUrl": metadata.official_entry_url,
            "status": "pending_parse",
            "revision": str(uuid4()),
            "sources": [
                {
                    "id": str(uuid4()),
                    "kind": source.kind,
                    "sourceUrl": metadata.source_url,
                    "title": source.file_name,
                    "fetchedAt": now.isoformat(),
                    "contentHash": source.content_hash,
                    "text": source.text,
                }
                for source in sources
            ],
            "criteria": [],
            "validationFindings": [],
        }
    )


def metadata_payload(**overrides) -> dict:
    payload = {
        "version": "v1.0",
        "title": "深圳市中小企业数字化转型扶持计划",
        "type": "grant",
        "issuer": "深圳市工业和信息化局",
        "region": "440300",
        "documentNumber": None,
        "applicableEntities": ["中小企业"],
        "publishedAt": "2026-09-01",
        "effectiveFrom": "2026-10-01",
        "effectiveTo": None,
        "applicationWindow": None,
        "sourceUrl": OFFICIAL_SOURCE_URL,
        "officialEntryUrl": OFFICIAL_ENTRY_URL,
    }
    payload.update(overrides)
    return payload


def multipart(metadata, files=None, fields=None) -> list[tuple[str, tuple]]:
    """构造 multipart 请求体；metadata 传字符串可测试坏 JSON。"""
    parts: list[tuple[str, tuple]] = []
    if metadata is not None:
        body = metadata if isinstance(metadata, str) else json.dumps(metadata, ensure_ascii=False)
        parts.append(("metadata", (None, body, "application/json")))
    for name, content, content_type in files or []:
        parts.append(("files", (name, content, content_type)))
    for name, value in fields or []:
        parts.append((name, (None, value, "text/plain")))
    return parts
