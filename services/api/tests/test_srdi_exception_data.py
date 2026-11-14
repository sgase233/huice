"""SRDI 例外操作的数据文件：形状与哈希固定，防止执行前被误改。"""

from __future__ import annotations

import importlib.util
import hashlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DEPLOY = REPO_ROOT / "infra" / "deploy"
ADD_SCRIPT = DEPLOY / "add-published-policy-source.py"
FIX_SCRIPT = DEPLOY / "fix-published-effective-dates.py"

EXPECTED_TEXT_SHA = "a91369d4d829a104f9ea4500560c0b4162e925db811e338f8000bf3e3c82e240"
EXPECTED_TEXT_SIZE = 11017
EXPECTED_ORIGINAL_SHA = "047835c14bc377021da92948d61aa85892e20fd2fb4458998121cff72f825b53"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, module)
    spec.loader.exec_module(module)
    return module


def test_the_detail_source_text_and_hashes_are_fixed() -> None:
    add_source = _load("add_published_policy_source_data", ADD_SCRIPT)
    entries = add_source.load_entries(DEPLOY / "srdi-detail-source.json")
    assert len(entries) == 1
    source = entries[0]["source"]
    assert source["contentHash"] == EXPECTED_TEXT_SHA
    assert source["byteSize"] == EXPECTED_TEXT_SIZE
    assert source["originalSha256"] == EXPECTED_ORIGINAL_SHA
    assert source["originalByteSize"] == 49372
    assert source["fileName"] == "post_12435148.html.txt"
    assert source["kind"] == "attachment"


def test_the_effective_dates_are_the_m3_target_values() -> None:
    fix_dates = _load("fix_published_effective_dates_data", FIX_SCRIPT)
    entries = fix_dates.load_entries(DEPLOY / "srdi-effective-dates.json")
    assert len(entries) == 1
    entry = entries[0]
    assert entry["expectedOld"]["effectiveFrom"] == "2025-10-15"
    assert entry["expectedOld"]["effectiveTo"] == "2025-11-10"
    assert entry["new"] == {"effectiveFrom": "2025-10-14", "effectiveTo": "2030-10-13"}


def test_the_provenance_draft_is_still_pending_m3() -> None:
    import json

    payload = json.loads(
        (DEPLOY / "date-provenance-backfill-srdi.json").read_text(encoding="utf-8")
    )
    assert payload["reviewStatus"] == "draft-pending-m3", (
        "M3 复核前不能改成批准值；脚本会拒绝 --apply，但数据文件也不该先改"
    )
    assert payload["entries"][0]["provenance"]["effectiveFrom"]["source"]["ordinal"] == 0, (
        "占位 0 表示必须先按来源补录干跑替换；直接运行会被 load_data 拒绝"
    )
    text = (DEPLOY / "policy-sources" / "SRDI-attachment-detail.txt").read_text(encoding="utf-8")
    assert hashlib.sha256(text.encode()).hexdigest() == EXPECTED_TEXT_SHA
