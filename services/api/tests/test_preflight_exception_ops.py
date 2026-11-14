"""``infra/deploy/preflight-exception-ops.py``：只读快照不写库、数对得上。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from app.schemas import PolicyImportMetadata
from support import metadata_payload

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "infra" / "deploy" / "preflight-exception-ops.py"

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("clean_tables")]


def _load():
    spec = importlib.util.spec_from_file_location("preflight_exception_ops", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("preflight_exception_ops", module)
    spec.loader.exec_module(module)
    return module


preflight = _load()


def _version(repo, *, version: str, source_url: str, effective_from: str = "2025-10-15"):
    metadata = PolicyImportMetadata.model_validate(
        metadata_payload(
            version=version,
            sourceUrl=source_url,
            publishedAt="2025-10-13",
            effectiveFrom=effective_from,
            effectiveTo="2025-11-10",
        )
    )
    return repo.create_policy_version(
        metadata=metadata, sources=[], storage_keys=[], trace_id=str(uuid4())
    )


def test_it_counts_published_and_finds_both_targets(database_url_schema, repo) -> None:
    _version(repo, version="v1.0", source_url=preflight.TEST_VERSION_URL)
    _version(repo, version=preflight.SRDI_VERSION, source_url=preflight.SRDI_URL)
    _version(repo, version="draft", source_url="https://example.gov.cn/draft.html")

    with psycopg.connect(database_url_schema) as conn:
        conn.execute(
            "UPDATE policy_version SET review_status = 'published', reviewed_at = now() "
            "WHERE version IN ('v1.0', %s)",
            (preflight.SRDI_VERSION,),
        )
        conn.commit()

    with psycopg.connect(database_url_schema) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        snapshot = preflight.collect(conn)

    assert snapshot["policyVersions"]["total"] == 3
    assert snapshot["policyVersions"]["byStatus"] == {"published": 2, "pending_parse": 1}
    assert snapshot["targets"]["testVersion"]["matched"] == 1
    assert snapshot["targets"]["srdi"]["matched"] == 1
    assert snapshot["targets"]["srdi"]["effectiveFrom"] == "2025-10-15"
    assert snapshot["guards"]["policy_version_guard_published"] == "O"
    assert snapshot["guards"]["policy_source_guard_published"] == "O"
    assert snapshot["publishedFingerprintExcludingTargets"] is None
