"""评测输入快照：规范化哈希、清单内容与一致性核对（``EVALUATION_FREEZE`` §5.2）。

两类用例：

* 纯函数——哈希与差异比较的口径（不依赖数据库，默认就跑）；
* ``db`` 标记——清单里的「事实版本」必须等于引擎落库的那个值；配置哈希**不能**
  把口令/令牌/连接串带进清单；``verify`` 在事实被改动后必须非零退出。

数据库用例仅在设置 ``HUICE_TEST_DATABASE_URL`` 时运行。
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from app.agents.profile import normalize_profile
from app.evaluation import __main__ as cli
from app.evaluation.snapshot import (
    CONFIG_FINGERPRINT_KEYS,
    SnapshotError,
    config_fingerprint,
    diff_snapshots,
    expression_fingerprint,
    sha256_hex,
)
from app.integrations.storage import CriterionRecord
from app.schemas import EnterpriseInput
from db_support import create

YEAR = {"startDate": "2026-01-01", "endDate": "2026-12-31"}

INCOME_GATE = {
    "op": "compare",
    "factKey": "taxableIncome",
    "period": "assessment",
    "comparator": "gte",
    "value": "3000000.00",
    "unit": "CNY",
}

INDUSTRY_GATE = {
    "op": "industry_exclusion",
    "factKey": "industry",
    "period": "assessment",
    "excluded": ["F51", "F52"],
}


# ------------------------------------------------------------------ 纯函数


def test_expression_fingerprint_ignores_key_order() -> None:
    left = {"op": "compare", "factKey": "taxableIncome", "value": "1.00"}
    right = {"value": "1.00", "factKey": "taxableIncome", "op": "compare"}

    assert expression_fingerprint(left) == expression_fingerprint(right)


def test_expression_fingerprint_changes_when_the_threshold_changes() -> None:
    stricter = {**INCOME_GATE, "value": "5000000.00"}

    assert expression_fingerprint(INCOME_GATE) != expression_fingerprint(stricter)


def test_expression_fingerprint_rejects_non_object() -> None:
    with pytest.raises(SnapshotError):
        expression_fingerprint(["not", "an", "object"])  # type: ignore[arg-type]


def test_decimal_spelling_does_not_change_the_hash() -> None:
    # 3.10 与 3.1 是同一个数；尾零不同不该换哈希，否则「值没变」会误报成「值变了」
    assert sha256_hex(Decimal("3.10")) == sha256_hex(Decimal("3.1"))


def test_a_date_hashes_like_its_iso_string() -> None:
    # 日期事实在库里是 date 列、在清单里是 ISO 字符串：两边要算出同一个哈希，
    # 否则同一份数据经不同路径会「看起来变了」。
    assert sha256_hex(date(2026, 1, 1)) == sha256_hex("2026-01-01")


def test_unknown_and_empty_are_different_values() -> None:
    # 未知事实（值列为空）与空字符串是两回事，不能算出同一个哈希
    assert sha256_hex(None) != sha256_hex("")


def test_hashing_a_naive_timestamp_does_not_fail() -> None:
    """清单的时间戳可能来自手工传入（无时区），不能因此整份生成失败。"""

    assert sha256_hex(datetime(2026, 1, 1, 8, 0)) == sha256_hex(
        datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
    )


def test_config_fingerprint_ignores_secrets() -> None:
    base = {
        "APP_ENV": "demo",
        "DEMO_ACCESS_MODE": "restricted",
        "MODEL_MODE": "deepseek",
        "DEEPSEEK_MODEL": "deepseek-chat",
        "OBJECT_STORE_MODE": "obs",
    }
    with_secrets = {
        **base,
        "ADMIN_TOKEN": "x" * 40,
        "DEMO_SESSION_SECRET": "y" * 40,
        "DEEPSEEK_API_KEY": "sk-not-a-real-key",
        "DATABASE_URL": "postgresql://u:p@127.0.0.1:5432/huice",
        "REDIS_URL": "redis://127.0.0.1:6379/0",
        "OBS_AK": "ak",
        "OBS_SK": "sk",
    }

    assert config_fingerprint(base) == config_fingerprint(with_secrets)


def test_config_fingerprint_tracks_the_model_mode() -> None:
    assert config_fingerprint({"MODEL_MODE": "fixture"}) != config_fingerprint(
        {"MODEL_MODE": "deepseek"}
    )


def test_config_fingerprint_refuses_a_secret_name_in_the_whitelist(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.evaluation.snapshot.CONFIG_FINGERPRINT_KEYS", ("MODEL_MODE", "DEEPSEEK_API_KEY")
    )

    with pytest.raises(SnapshotError) as error:
        config_fingerprint({"MODEL_MODE": "fixture", "DEEPSEEK_API_KEY": "secret-value"})

    # 只报变量名，不回显值
    assert "DEEPSEEK_API_KEY" in str(error.value)
    assert "secret-value" not in str(error.value)


def test_the_whitelist_carries_no_secret_names() -> None:
    for name in CONFIG_FINGERPRINT_KEYS:
        assert not any(
            fragment in name for fragment in ("TOKEN", "KEY", "SECRET", "PASSWORD", "URL")
        ), name


def _manifest(**overrides):
    manifest = {
        "manifestVersion": "1",
        "collectedAt": "2026-11-05T10:00:00+00:00",
        "appVersion": "1.0.0-rc.6",
        "ruleVersion": "rule:criteria@1",
        "codeCommit": "abc123",
        "dataset": {"labels.csv": "a" * 64},
        "configKeys": list(CONFIG_FINGERPRINT_KEYS),
        "configFingerprint": "f" * 64,
        "policies": [
            {
                "policyId": "p1",
                "policyVersionId": "v1",
                "version": "v1.0",
                "criteria": [{"criterionId": "c1", "expressionFingerprint": "e" * 64}],
                "criteriaSetFingerprint": "s" * 64,
            }
        ],
        "enterprises": [
            {
                "enterpriseId": "e1",
                "factVersion": "v" * 64,
                "facts": [
                    {
                        "key": "taxableIncome",
                        "periodStart": "2026-01-01",
                        "periodEnd": "2026-12-31",
                        "valueHash": "h" * 64,
                    }
                ],
            }
        ],
    }
    manifest.update(overrides)
    return manifest


def test_diff_reports_nothing_for_the_same_manifest() -> None:
    assert diff_snapshots(_manifest(), _manifest()) == []


def test_diff_ignores_the_collection_time() -> None:
    later = _manifest(collectedAt="2026-11-06T10:00:00+00:00")

    assert diff_snapshots(_manifest(), later) == []


def test_diff_flags_a_changed_fact_value() -> None:
    changed = json.loads(json.dumps(_manifest()))
    changed["enterprises"][0]["facts"][0]["valueHash"] = "changed"

    problems = diff_snapshots(_manifest(), changed)

    assert any("taxableIncome" in item and "值变了" in item for item in problems)


def test_diff_flags_config_and_dataset_changes() -> None:
    changed = _manifest(configFingerprint="0" * 64, dataset={"labels.csv": "b" * 64})

    problems = diff_snapshots(_manifest(), changed)

    assert any("配置哈希" in item for item in problems)
    assert any("labels.csv" in item for item in problems)


def test_diff_flags_missing_rows() -> None:
    problems = diff_snapshots(_manifest(), _manifest(policies=[], enterprises=[]))

    assert any("政策版本缺失" in item for item in problems)
    assert any("企业缺失" in item for item in problems)


def test_diff_ignores_dataset_files_added_after_signing() -> None:
    # 签认之后往目录里加了别的 CSV：不影响已签认的输入，不该拦下评测
    added = _manifest(dataset={"labels.csv": "a" * 64, "later_added.csv": "c" * 64})

    assert diff_snapshots(_manifest(), added) == []


# ------------------------------------------------------------------ 命令行


def test_the_cli_names_the_variable_it_needs(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(SystemExit) as error:
        cli.main(["inputs", "--out", "-"])

    assert "DATABASE_URL" in str(error.value.code)


def test_a_manifest_without_a_version_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "wrong.json"
    path.write_text("{}", encoding="utf-8")

    with pytest.raises(SystemExit) as error:
        cli.main(["verify", str(path)])

    assert "manifestVersion" in str(error.value.code)


# ------------------------------------------------------------------ 数据库


def _publish_policy(repo, expressions, version: str = "v1.0"):
    draft = create(repo, version=version)
    body = draft.sources[0]
    spans = [
        {
            "sourceId": str(body.id),
            "sourceUrl": body.source_url,
            "sourceVersion": version,
            "page": None,
            "section": "合成演示条款",
            "quote": "此处仅为接口结构演示，不是真实政策。",
            "textHash": body.content_hash,
        }
    ]
    criteria = [
        CriterionRecord(
            id=uuid4(),
            title=f"演示条件 {index + 1}",
            expression=expression,
            spans=spans,
            review_finding="none",
            validation_status="passed",
        )
        for index, expression in enumerate(expressions)
    ]
    saved = repo.save_criteria(
        policy_id=draft.id,
        version=version,
        expected_revision=draft.revision,
        criteria=criteria,
        findings=[],
        status="pending_review",
        trace_id=str(uuid4()),
    )
    return repo.publish(
        policy_id=draft.id,
        version=version,
        expected_revision=saved.revision,
        trace_id=str(uuid4()),
    )


def _make_enterprise(repo, *, income: str | None = "3500000.00"):
    payload = EnterpriseInput.model_validate(
        {
            "nameSynthetic": "合成企业A",
            "period": YEAR,
            "facts": [
                {"key": "region", "value": "440300", "unit": "code", "period": YEAR, "source": "user_input"},
                {"key": "industry", "value": "C39", "unit": "code", "period": YEAR, "source": "user_input"},
                {"key": "taxableIncome", "value": income, "unit": "CNY", "period": YEAR, "source": "user_input"},
            ],
        }
    )
    return repo.create_enterprise(profile=normalize_profile(payload), trace_id=str(uuid4()))


@pytest.mark.db
@pytest.mark.usefixtures("clean_tables")
def test_the_manifest_records_the_engine_fact_version(
    repo, database_url_schema, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url_schema)
    enterprise = _make_enterprise(repo)
    published = _publish_policy(repo, [INCOME_GATE, INDUSTRY_GATE])
    out = tmp_path / "manifest.json"

    assert cli.main(["inputs", "--out", str(out), "--no-config"]) == 0
    manifest = json.loads(out.read_text(encoding="utf-8"))

    entry = next(
        item for item in manifest["enterprises"] if item["enterpriseId"] == str(enterprise.id)
    )
    # 与 assessment_run.fact_version 同一个算法，才能与运行记录直接对齐
    assert entry["factVersion"] == enterprise.fact_version

    policy = next(item for item in manifest["policies"] if item["policyId"] == str(published.id))
    ids = [item["criterionId"] for item in policy["criteria"]]
    assert ids == sorted(ids)
    assert len(ids) == 2
    assert policy["criteriaSetFingerprint"]


@pytest.mark.db
@pytest.mark.usefixtures("clean_tables")
def test_the_manifest_never_carries_secret_values(
    repo, database_url_schema, monkeypatch, tmp_path: Path
) -> None:
    _make_enterprise(repo)
    markers = {
        "ADMIN_TOKEN": "admin-token-marker-do-not-print",
        "DEEPSEEK_API_KEY": "deepseek-key-marker-do-not-print",
        "REDIS_URL": "redis://marker-do-not-print:6379/0",
        "OBS_AK": "obs-ak-marker-do-not-print",
    }
    for name, value in markers.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("DATABASE_URL", database_url_schema)
    out = tmp_path / "manifest.json"

    assert cli.main(["inputs", "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")

    for name, value in markers.items():
        assert value not in text, name
    assert database_url_schema not in text


@pytest.mark.db
@pytest.mark.usefixtures("clean_tables")
def test_verify_passes_then_fails_when_a_fact_changes(
    repo, database_url_schema, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url_schema)
    enterprise = _make_enterprise(repo)
    _publish_policy(repo, [INCOME_GATE])
    sealed = tmp_path / "sealed.json"

    assert (
        cli.main(
            [
                "inputs",
                "--out",
                str(sealed),
                "--no-config",
                "--collected-at",
                "2026-11-05T10:00:00+00:00",
            ]
        )
        == 0
    )
    assert cli.main(["verify", str(sealed), "--no-config"]) == 0

    # 签认之后有人动了事实值：核对必须发现，且不改动已签认的清单
    with psycopg.connect(database_url_schema, autocommit=True) as conn:
        conn.execute(
            "UPDATE enterprise_fact SET value_amount = value_amount + 1 WHERE key = 'taxableIncome'"
        )
    sealed_bytes = sealed.read_bytes()

    assert cli.main(["verify", str(sealed), "--no-config"]) == 1
    assert sealed.read_bytes() == sealed_bytes
    assert str(enterprise.id) in sealed.read_text(encoding="utf-8")


@pytest.mark.db
@pytest.mark.usefixtures("clean_tables")
def test_cli_hashes_the_dataset_files_it_is_given(
    database_url_schema, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url_schema)
    data = tmp_path / "evaluation"
    data.mkdir()
    (data / "labels.csv").write_bytes(b"case_id,criterion_id\n1,2\n")
    (data / "notes.txt").write_bytes(b"not a csv")
    out = tmp_path / "manifest.json"

    assert cli.main(["inputs", "--out", str(out), "--no-config", "--dataset-dir", str(data)]) == 0
    manifest = json.loads(out.read_text(encoding="utf-8"))

    assert manifest["dataset"] == {
        "labels.csv": hashlib.sha256(b"case_id,criterion_id\n1,2\n").hexdigest()
    }
    assert manifest["collectedAt"].endswith("+00:00")


def test_a_missing_dataset_directory_is_an_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://example/never-used")

    with pytest.raises(SystemExit) as error:
        cli.main(["inputs", "--out", "-", "--dataset-dir", str(tmp_path / "nope")])

    assert "数据集目录" in str(error.value.code)
