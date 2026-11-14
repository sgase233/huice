"""``infra/deploy/fix-draft-effective-dates.py``：只改草稿行的有效期占位。

M3 在 PR #69 收口里要求：SMART/GREEN 的申报窗口不是政策有效期，云端这两行草稿要改成
``effective_from = published_at``、``effective_to = NULL``。这里锁住三件事：只动草稿、
不动已发布行（`xmin` 指纹一致）、命中已发布行时整批拒绝。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from app.schemas import PolicyImportMetadata
from support import metadata_payload

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "infra" / "deploy" / "fix-draft-effective-dates.py"
DATA = REPO_ROOT / "infra" / "deploy" / "draft-effective-dates.json"

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("clean_tables")]

SMART_URL = "https://www.sz.gov.cn/szzt2010/wgkzl/jcgk/jcygk/zdzcjc/content/post_12201125.html"


def _load():
    spec = importlib.util.spec_from_file_location("fix_draft_effective_dates", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("fix_draft_effective_dates", module)
    spec.loader.exec_module(module)
    return module


fixer = _load()


def _draft(repo, *, version: str, source_url: str, effective_from: str, effective_to: str | None):
    metadata = PolicyImportMetadata.model_validate(
        metadata_payload(
            version=version,
            sourceUrl=source_url,
            publishedAt="2025-05-28",
            effectiveFrom=effective_from,
            effectiveTo=effective_to,
        )
    )
    return repo.create_policy_version(
        metadata=metadata,
        sources=[],
        storage_keys=[],
        trace_id=str(uuid4()),
    )


def _write(tmp_path: Path, entries: list[dict]) -> Path:
    path = tmp_path / "data.json"
    path.write_text(json.dumps({"version": 1, "entries": entries}, ensure_ascii=False), encoding="utf-8")
    return path


def test_the_shipped_data_names_the_two_versions() -> None:
    entries = fixer.load_entries(DATA)

    assert [entry["version"] for entry in entries] == ["深工信规〔2025〕4号", "深工信规〔2024〕12号"]


def test_it_rewrites_only_the_draft_rows(database_url_schema: str, repo, tmp_path, monkeypatch) -> None:
    draft = _draft(
        repo,
        version="深工信规〔2025〕4号",
        source_url=SMART_URL,
        effective_from="2025-06-13",
        effective_to="2025-07-07",
    )
    path = _write(tmp_path, [{"sourceUrl": SMART_URL, "version": "深工信规〔2025〕4号"}])
    monkeypatch.setenv("DATABASE_URL", database_url_schema)

    assert fixer.run(path, apply=True) == 0

    with psycopg.connect(database_url_schema) as conn:
        row = conn.execute(
            "SELECT effective_from, effective_to, published_at FROM policy_version "
            "WHERE version = %s",
            ("深工信规〔2025〕4号",),
        ).fetchone()
    assert row[0] == row[2], "effective_from 应等于 published_at"
    assert row[1] is None


def test_a_published_row_is_refused_and_nothing_is_written(
    database_url_schema: str, repo, tmp_path, monkeypatch
) -> None:
    draft = _draft(
        repo,
        version="深工信规〔2025〕4号",
        source_url=SMART_URL,
        effective_from="2025-06-13",
        effective_to="2025-07-07",
    )
    with psycopg.connect(database_url_schema) as conn:
        conn.execute(
            "UPDATE policy_version SET review_status = 'published', reviewed_at = now() "
            "WHERE version = %s",
            ("深工信规〔2025〕4号",),
        )
        conn.commit()

    path = _write(tmp_path, [{"sourceUrl": SMART_URL, "version": "深工信规〔2025〕4号"}])
    monkeypatch.setenv("DATABASE_URL", database_url_schema)

    assert fixer.run(path, apply=True) == 4

    with psycopg.connect(database_url_schema) as conn:
        row = conn.execute(
            "SELECT effective_from FROM policy_version WHERE version = %s",
            ("深工信规〔2025〕4号",),
        ).fetchone()
    assert str(row[0]) == "2025-06-13"


def test_a_dry_run_reports_without_writing(database_url_schema: str, repo, tmp_path, monkeypatch) -> None:
    draft = _draft(
        repo,
        version="深工信规〔2025〕4号",
        source_url=SMART_URL,
        effective_from="2025-06-13",
        effective_to="2025-07-07",
    )
    path = _write(tmp_path, [{"sourceUrl": SMART_URL, "version": "深工信规〔2025〕4号"}])
    monkeypatch.setenv("DATABASE_URL", database_url_schema)

    assert fixer.run(path, apply=False) == 0

    with psycopg.connect(database_url_schema) as conn:
        row = conn.execute(
            "SELECT effective_from FROM policy_version WHERE version = %s",
            ("深工信规〔2025〕4号",),
        ).fetchone()
    assert str(row[0]) == "2025-06-13"


def test_it_bumps_the_revision_so_stale_etags_cannot_undo_it(
    database_url_schema: str, repo, tmp_path, monkeypatch
) -> None:
    """改了行内容就要换 revision：否则后台拿着旧 ETag 的 If-Match 会把改写覆盖回去。"""

    _draft(
        repo,
        version="深工信规〔2025〕4号",
        source_url=SMART_URL,
        effective_from="2025-06-13",
        effective_to="2025-07-07",
    )
    with psycopg.connect(database_url_schema) as conn:
        before = conn.execute(
            "SELECT revision FROM policy_version WHERE version = %s", ("深工信规〔2025〕4号",)
        ).fetchone()[0]

    path = _write(tmp_path, [{"sourceUrl": SMART_URL, "version": "深工信规〔2025〕4号"}])
    monkeypatch.setenv("DATABASE_URL", database_url_schema)
    assert fixer.run(path, apply=True) == 0

    with psycopg.connect(database_url_schema) as conn:
        after = conn.execute(
            "SELECT revision FROM policy_version WHERE version = %s", ("深工信规〔2025〕4号",)
        ).fetchone()[0]
    assert after != before


def test_a_stale_revision_is_refused_by_the_update(database_url_schema: str, repo) -> None:
    """读完之后被改过（revision 变了）：那一行命中 0 行，调用方整批回滚。"""

    _draft(
        repo,
        version="深工信规〔2025〕4号",
        source_url=SMART_URL,
        effective_from="2025-06-13",
        effective_to="2025-07-07",
    )
    conn = psycopg.connect(database_url_schema)
    try:
        row = conn.execute(
            "SELECT id, review_status, published_at, effective_from, effective_to, revision "
            "FROM policy_version WHERE version = %s",
            ("深工信规〔2025〕4号",),
        ).fetchone()
        # 模拟审核员在读取之后另存过：revision 变了
        conn.execute(
            "UPDATE policy_version SET revision = gen_random_uuid() WHERE id = %s", (row[0],)
        )
        conn.commit()

        assert fixer.update_row(conn, row) is False
        conn.rollback()
    finally:
        conn.close()

    with psycopg.connect(database_url_schema) as conn:
        effective = conn.execute(
            "SELECT effective_from FROM policy_version WHERE version = %s",
            ("深工信规〔2025〕4号",),
        ).fetchone()[0]
    assert str(effective) == "2025-06-13"


def test_a_second_run_is_a_no_op(database_url_schema: str, repo, tmp_path, monkeypatch, capsys) -> None:
    """幂等：第一次改写后，第二次不重复 UPDATE、不重复 bump revision。"""

    _draft(
        repo,
        version="深工信规〔2025〕4号",
        source_url=SMART_URL,
        effective_from="2025-06-13",
        effective_to="2025-07-07",
    )
    path = _write(tmp_path, [{"sourceUrl": SMART_URL, "version": "深工信规〔2025〕4号"}])
    monkeypatch.setenv("DATABASE_URL", database_url_schema)

    assert fixer.run(path, apply=True) == 0
    with psycopg.connect(database_url_schema) as conn:
        before = conn.execute(
            "SELECT revision FROM policy_version WHERE version = %s", ("深工信规〔2025〕4号",)
        ).fetchone()[0]

    capsys.readouterr()
    assert fixer.run(path, apply=True) == 0
    output = capsys.readouterr().out
    assert "已是目标值，跳过" in output

    with psycopg.connect(database_url_schema) as conn:
        after = conn.execute(
            "SELECT revision FROM policy_version WHERE version = %s", ("深工信规〔2025〕4号",)
        ).fetchone()[0]
    assert after == before
