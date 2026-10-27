"""数据库用例共用的小工具：直接查库、造导入请求与已发布版本。"""

from __future__ import annotations

import hashlib
from uuid import uuid4

import psycopg

from app.agents.ingestion import PreparedSource
from app.integrations.storage import PolicyRepository
from app.schemas import PolicyImportMetadata
from support import metadata_payload


def query_one(dsn: str, sql: str, params=()):
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def query_all(dsn: str, sql: str, params=()):
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def source(text: str = "政策正文", kind: str = "body") -> PreparedSource:
    return PreparedSource(
        kind=kind,
        file_name=f"{kind}.txt",
        mime_type="text/plain",
        text=text,
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
        byte_size=len(text.encode()) or 1,
    )


def create(repo: PolicyRepository, *, version: str = "v1.0", policy_id=None, **overrides):
    payload = metadata_payload(version=version, **overrides)
    if policy_id is not None:
        payload["policyId"] = str(policy_id)
    metadata = PolicyImportMetadata.model_validate(payload)
    sources = [source("正文"), source("附件", kind="attachment")]
    return repo.create_policy_version(
        metadata=metadata,
        sources=sources,
        storage_keys=["local/body.txt", "local/annex.txt"],
        trace_id=str(uuid4()),
    )
