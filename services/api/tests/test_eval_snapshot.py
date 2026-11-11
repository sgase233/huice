"""评测输入快照：规范化哈希、清单内容与一致性核对（``EVALUATION_FREEZE`` §5.2）。

两类用例：

* 纯函数——哈希与差异比较的口径（不依赖数据库，默认就跑）；
* ``db`` 标记——清单里的「事实版本」必须等于引擎落库的那个值；配置哈希**不能**
  把口令/令牌/连接串带进清单；``verify`` 在事实被改动后必须非零退出。

数据库用例仅在设置 ``HUICE_TEST_DATABASE_URL`` 时运行。
"""

from __future__ import annotations

import csv
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
from app.evaluation import snapshot as snapshot_module
from app.evaluation.snapshot import (
    CONFIG_FINGERPRINT_KEYS,
    MANIFEST_VERSION,
    SnapshotError,
    collect_input_snapshot,
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
    """一份字段齐全的清单：每个记进清单的字段都要在差异比较里有位置。"""

    manifest = {
        "manifestVersion": MANIFEST_VERSION,
        "collectedAt": "2026-11-05T10:00:00+00:00",
        "appVersion": "1.0.0-rc.7",
        "ruleVersion": "rule:criteria@1",
        "codeCommit": "abc123",
        # 部署文件指纹：与手填的 codeCommit 互相独立，可复算（见 test_eval_code_fingerprint）
        "codeFingerprint": "1" * 64,
        "codeFiles": 41,
        "codeScope": [
            "services/api/app",
            "contracts/openapi.yaml",
            "services/api/requirements.lock",
        ],
        # 只记录、不参与比对：服务重启是正常操作，比它会让每次核对都失败
        "processStartedAt": "2026-11-05T09:00:00+00:00",
        # 四份输入本来就不在一个目录里（仓库里分别是 evaluation/policies/fixtures），
        # 清单记的是签认路径，不是文件名
        "dataset": [
            {"path": "data/evaluation/labels.csv", "sha256": "a" * 64},
            {"path": "data/policies/criteria_review.csv", "sha256": "b" * 64},
            {"path": "data/fixtures/enterprises.csv", "sha256": "c" * 64},
            {"path": "data/policies/source_register.csv", "sha256": "d" * 64},
        ],
        "datasetMapping": {
            "rules": "content-alignment@3",
            "files": [
                "data/evaluation/labels.csv",
                "data/policies/criteria_review.csv",
                "data/fixtures/enterprises.csv",
                "data/policies/source_register.csv",
            ],
            "policies": [
                {
                    "identifier": "财政部 税务总局公告2023年第12号",
                    "policyVersionId": "v1",
                    "criteria": [{"criterionCode": "SME-002", "criterionId": "c1"}],
                }
            ],
            "enterprises": [
                {"code": "E-ENT-001", "nameSynthetic": "合成企业A", "enterpriseId": "e1"}
            ],
            "caseCount": 1,
            "problems": [],
        },
        "configKeys": list(CONFIG_FINGERPRINT_KEYS),
        "configFingerprint": "f" * 64,
        "policies": [
            {
                "policyId": "p1",
                "policyVersionId": "v1",
                "version": "v1.0",
                "title": "小型微利企业所得税优惠",
                "documentNumber": "财政部 税务总局公告2023年第12号",
                "policyType": "tax",
                "reviewStatus": "published",
                "importedAt": "2026-11-01T00:00:00+00:00",
                "revision": "r" * 32,
                "criteria": [
                    {
                        "criterionId": "c1",
                        "title": "年度应纳税所得额不超过 300 万元",
                        "expressionFingerprint": "e" * 64,
                        "reviewFinding": "none",
                        "validationStatus": "passed",
                    }
                ],
                "criteriaSetFingerprint": "s" * 64,
            }
        ],
        "enterprises": [
            {
                "enterpriseId": "e1",
                "nameSynthetic": "合成企业A",
                "period": {"startDate": "2026-01-01", "endDate": "2026-12-31"},
                "factVersion": "v" * 64,
                "facts": [
                    {
                        "key": "taxableIncome",
                        "periodStart": "2026-01-01",
                        "periodEnd": "2026-12-31",
                        "unit": "CNY",
                        "source": "user_input",
                        "verified": True,
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
    changed = _manifest(
        configFingerprint="0" * 64,
        dataset=[
            {"path": "data/evaluation/labels.csv", "sha256": "b" * 64},
            {"path": "data/policies/criteria_review.csv", "sha256": "b" * 64},
            {"path": "data/fixtures/enterprises.csv", "sha256": "c" * 64},
        ],
    )

    problems = diff_snapshots(_manifest(), changed)

    assert any("配置哈希" in item for item in problems)
    # 报的是签认路径，不是裸文件名：同名文件在不同目录里也得能分辨
    assert any("data/evaluation/labels.csv" in item for item in problems)


def test_diff_flags_missing_rows() -> None:
    problems = diff_snapshots(_manifest(), _manifest(policies=[], enterprises=[]))

    assert any("政策版本缺失" in item for item in problems)
    assert any("企业缺失" in item for item in problems)


def test_diff_flags_a_manifest_written_in_an_older_format() -> None:
    # 清单结构升到 2 后，1 的清单不能再当作「内容一致」放行
    older = _manifest(manifestVersion="1")

    problems = diff_snapshots(_manifest(), older)

    assert any("清单版本不同" in item for item in problems)


def test_diff_ignores_dataset_files_added_after_signing() -> None:
    # 签认之后往目录里加了别的 CSV：不影响已签认的输入，不该拦下评测
    added = _manifest(
        dataset=[
            {"path": "data/evaluation/labels.csv", "sha256": "a" * 64},
            {"path": "data/policies/criteria_review.csv", "sha256": "b" * 64},
            {"path": "data/fixtures/enterprises.csv", "sha256": "c" * 64},
            {"path": "data/policies/source_register.csv", "sha256": "d" * 64},
            {"path": "data/evaluation/later_added.csv", "sha256": "e" * 64},
        ]
    )

    assert diff_snapshots(_manifest(), added) == []


def test_diff_flags_a_signed_dataset_path_that_is_gone_and_one_that_appeared() -> None:
    """路径变了要报出来：少了「签认的那一份」，多了「不是签认的那一份」。"""

    gone = _manifest(
        dataset=[
            {"path": "data/policies/criteria_review.csv", "sha256": "b" * 64},
            {"path": "data/fixtures/enterprises.csv", "sha256": "c" * 64},
        ]
    )
    problems = diff_snapshots(_manifest(), gone)

    assert any("数据集文件缺失：data/evaluation/labels.csv" in item for item in problems)


def _leaf_paths(node, prefix=()):
    if isinstance(node, dict) and node:
        for key, value in node.items():
            yield from _leaf_paths(value, prefix + (key,))
    elif isinstance(node, list) and node:
        for index, value in enumerate(node):
            yield from _leaf_paths(value, prefix + (index,))
    else:
        yield prefix


def _changed_copy(manifest, path):
    clone = json.loads(json.dumps(manifest))
    node = clone
    for step in path[:-1]:
        node = node[step]
    value = node[path[-1]]
    if isinstance(value, bool):
        node[path[-1]] = not value
    elif isinstance(value, int):
        node[path[-1]] = value + 1
    elif isinstance(value, str):
        node[path[-1]] = value + "-changed"
    else:
        node[path[-1]] = "changed"
    return clone


def test_every_recorded_field_takes_part_in_the_comparison() -> None:
    """清单里记下的每个字段都必须参与比对。

    M2 复审指出 ``reviewFinding`` 记了却不比：同一条条件从 ``none`` 变成 ``conflict``
    时规则引擎会直接给出冲突结论，而 ``verify`` 说「一致」。这条对每个叶子字段做单点
    变异，以后再加字段忘了比较也会在这里转红。
    """

    base = _manifest()
    checked = 0
    for path in _leaf_paths(base):
        if path[0] in ("collectedAt", "processStartedAt"):
            # 采集时间天然不同；进程启动时间是签认期的交叉证据，重启后就变，
            # 拿它比会让「签认后重启过」被判成不一致
            continue
        changed = _changed_copy(base, path)
        assert diff_snapshots(base, changed), f"字段 {path} 变了，verify 却没报差异"
        checked += 1
    assert checked > 30, "清单结构被改小了？至少要覆盖到目前记下的这些字段"


def test_diff_flags_a_changed_review_finding() -> None:
    changed = json.loads(json.dumps(_manifest()))
    changed["policies"][0]["criteria"][0]["reviewFinding"] = "conflict"

    problems = diff_snapshots(_manifest(), changed)

    assert any("复核结论" in item for item in problems)


def test_diff_flags_a_changed_policy_review_status_and_revision() -> None:
    changed = json.loads(json.dumps(_manifest()))
    changed["policies"][0]["reviewStatus"] = "pending_review"
    changed["policies"][0]["revision"] = "other-revision"

    problems = diff_snapshots(_manifest(), changed)

    assert any("复核状态" in item for item in problems)
    assert any("revision" in item for item in problems)


def test_diff_refuses_a_manifest_with_unresolved_dataset_mapping() -> None:
    """两边清单完全相同也不算通过：映射有未解决项就不是可签认的输入。"""

    mapping = json.loads(json.dumps(_manifest()["datasetMapping"]))
    mapping["problems"] = ["企业 E-ENT-001：库里没有同名企业"]
    manifest = _manifest(datasetMapping=mapping)

    problems = diff_snapshots(manifest, manifest)

    assert any("数据集映射未解决" in item for item in problems)


def test_diff_refuses_a_mapping_that_never_looked_at_any_file() -> None:
    mapping = json.loads(json.dumps(_manifest()["datasetMapping"]))
    mapping.update({"files": [], "policies": [], "enterprises": [], "caseCount": 0})
    manifest = _manifest(datasetMapping=mapping)

    problems = diff_snapshots(manifest, manifest)

    assert any("没有核对" in item for item in problems)


# ------------------------------------------------------------------ 命令行


def test_the_cli_names_the_variable_it_needs(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(SystemExit) as error:
        cli.main(["inputs", "--out", "-", "--no-dataset"])

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

    assert cli.main(["inputs", "--out", str(out), "--no-config", "--no-dataset"]) == 0
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

    assert cli.main(["inputs", "--out", str(out), "--no-dataset"]) == 0
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
                "--no-dataset",
            ]
        )
        == 0
    )
    assert cli.main(["verify", str(sealed), "--no-config", "--no-dataset"]) == 0

    # 签认之后有人动了事实值：核对必须发现，且不改动已签认的清单
    with psycopg.connect(database_url_schema, autocommit=True) as conn:
        conn.execute(
            "UPDATE enterprise_fact SET value_amount = value_amount + 1 WHERE key = 'taxableIncome'"
        )
    sealed_bytes = sealed.read_bytes()

    assert cli.main(["verify", str(sealed), "--no-config", "--no-dataset"]) == 1
    assert sealed.read_bytes() == sealed_bytes
    assert str(enterprise.id) in sealed.read_text(encoding="utf-8")


@pytest.mark.db
@pytest.mark.usefixtures("clean_tables")
def test_verify_says_what_the_fingerprint_proves_and_what_it_does_not(
    repo, database_url_schema, monkeypatch, tmp_path: Path, capsys
) -> None:
    """指纹一致只能说「磁盘代码一致」，不能说「运行代码身份已证明」（M2 在 #39）。

    ``codeCommit`` 为 ``null`` 原先只打一句「代码身份未证明」；现在清单有了可复算的
    指纹，那句话要拆成三件事说清楚：磁盘代码一致、对应哪个提交未证明、进程里跑的
    是不是它未证明。
    """

    monkeypatch.setenv("DATABASE_URL", database_url_schema)
    _make_enterprise(repo)
    sealed = tmp_path / "sealed.json"
    assert cli.main(["inputs", "--out", str(sealed), "--no-config", "--no-dataset"]) == 0
    capsys.readouterr()

    manifest = json.loads(sealed.read_text(encoding="utf-8"))
    assert isinstance(manifest["codeFingerprint"], str) and len(manifest["codeFingerprint"]) == 64
    assert manifest["codeFiles"] >= 1
    # 没给 --process-pid：只记录为空，不能编一个时间
    assert manifest["processStartedAt"] is None

    assert cli.main(["verify", str(sealed), "--no-config", "--no-dataset"]) == 0
    captured = capsys.readouterr()
    assert "一致" in captured.out
    assert "磁盘代码与签认一致" in captured.err
    # 反向断言：不能因为指纹一致就笼统说成「已证明」
    assert "仍未证明" in captured.err
    assert "已证明" not in captured.err
    # 签认时没记进程启动时间，这条交叉证据要明说缺了
    assert "进程启动时间" in captured.err

    # 记了提交号就不再是「没记」，而是逐字比对：签认时是 null，现在有值 → 不一致
    assert cli.main(
        ["verify", str(sealed), "--no-config", "--commit", "abc1234", "--no-dataset"]
    ) == 1


@pytest.mark.db
@pytest.mark.usefixtures("clean_tables")
def test_a_manifest_signed_before_the_fingerprint_still_verifies_with_a_note(
    repo, database_url_schema, monkeypatch, tmp_path: Path, capsys
) -> None:
    """旧清单（没有 ``codeFingerprint``）仍能核对库，但必须明说磁盘身份没证明。"""

    monkeypatch.setenv("DATABASE_URL", database_url_schema)
    _make_enterprise(repo)
    sealed = tmp_path / "sealed.json"
    assert cli.main(["inputs", "--out", str(sealed), "--no-config", "--no-dataset"]) == 0

    manifest = json.loads(sealed.read_text(encoding="utf-8"))
    for field in ("codeFingerprint", "codeFiles", "codeScope", "processStartedAt"):
        manifest.pop(field)
    sealed.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    capsys.readouterr()

    assert cli.main(["verify", str(sealed), "--no-config", "--no-dataset"]) == 0
    captured = capsys.readouterr()
    assert "一致" in captured.out
    assert "没有 codeFingerprint" in captured.err
    assert "磁盘代码身份未证明" in captured.err


@pytest.mark.db
@pytest.mark.usefixtures("clean_tables")
def test_cli_hashes_only_the_dataset_files_it_is_given(
    database_url_schema, monkeypatch, tmp_path: Path
) -> None:
    """只哈希 ``--dataset-file`` 列出的文件：同目录里的材料复核表**不进**签认清单。

    M3 复核指出：按目录扫 ``*.csv`` 会把 ``material_review_sheet.csv`` 这类非评测输入
    也算进去，材料复核一改 ``verify`` 就误报。所以这里故意在目录里放一份复核表，
    断言它没被记录。
    """

    monkeypatch.setenv("DATABASE_URL", database_url_schema)
    data = tmp_path / "evaluation"
    _write_dataset(data, title="某某扶持计划")
    (data / "material_review_sheet.csv").write_bytes(b"row,reviewer\n1,m3\n")
    (data / "notes.txt").write_bytes(b"not a csv")
    listed = ["labels.csv", "criteria_review.csv", "enterprises.csv", "source_register.csv"]
    args: list[str] = []
    for name in listed:
        args += ["--dataset-file", name]
    out = tmp_path / "manifest.json"

    assert (
        cli.main(["inputs", "--out", str(out), "--no-config", "--dataset-dir", str(data), *args])
        == 0
    )
    manifest = json.loads(out.read_text(encoding="utf-8"))

    # 每份输入连路径一起记：只留文件名的话，换目录或同名文件就分不清了
    recorded = {item["path"]: item["sha256"] for item in manifest["dataset"]}
    expected = {
        (data / name).resolve().as_posix(): hashlib.sha256((data / name).read_bytes()).hexdigest()
        for name in listed
    }
    assert recorded == expected
    # 临时目录在项目外：记的是绝对路径；文件名仍要能认出是哪一份
    assert all(Path(path).is_absolute() for path in recorded)
    assert "material_review_sheet" not in " ".join(recorded)
    assert manifest["collectedAt"].endswith("+00:00")


def test_a_bare_directory_scan_is_refused(tmp_path: Path, monkeypatch) -> None:
    """只给 ``--dataset-dir`` 会扫全部 CSV，正是 M3 指出的误报来源：直接拒绝。"""

    monkeypatch.setenv("DATABASE_URL", "postgresql://example/never-used")
    data = tmp_path / "evaluation"
    data.mkdir()
    (data / "labels.csv").write_bytes(b"a\n")

    with pytest.raises(SystemExit) as error:
        cli.main(["inputs", "--out", "-", "--dataset-dir", str(data)])

    assert "--dataset-file" in str(error.value.code)


def test_a_missing_dataset_file_is_named(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://example/never-used")
    data = tmp_path / "evaluation"
    data.mkdir()

    with pytest.raises(SystemExit) as error:
        cli.main(
            [
                "inputs",
                "--out",
                "-",
                "--dataset-dir",
                str(data),
                "--dataset-file",
                "criteria_review.csv",
            ]
        )

    assert "criteria_review.csv" in str(error.value.code)


def test_a_missing_dataset_directory_is_an_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://example/never-used")

    with pytest.raises(SystemExit) as error:
        cli.main(["inputs", "--out", "-", "--dataset-dir", str(tmp_path / "nope")])

    assert "数据集目录" in str(error.value.code)


def test_inputs_refuses_to_run_without_any_dataset_file(tmp_path: Path, monkeypatch) -> None:
    """既不给 CSV 也不声明 --no-dataset：不生成「没核对数据集却像是签认了」的清单。"""

    monkeypatch.setenv("DATABASE_URL", "postgresql://example/never-used")

    with pytest.raises(SystemExit) as error:
        cli.main(["inputs", "--out", "-"])

    message = str(error.value.code)
    for name in (
        "labels.csv",
        "criteria_review.csv",
        "enterprises.csv",
        "source_register.csv",
    ):
        assert name in message
    assert "--no-dataset" in message


def test_inputs_names_the_dataset_files_that_are_missing(tmp_path: Path, monkeypatch) -> None:
    """只给一份：点名还缺哪两份，而不是「部分对应也算过」。"""

    monkeypatch.setenv("DATABASE_URL", "postgresql://example/never-used")
    data = tmp_path / "evaluation"
    data.mkdir()
    (data / "labels.csv").write_text(
        "case_id,enterprise_id,policy_version,criterion_id\nC001,E-ENT-001,某文号,SME-002\n",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as error:
        cli.main(["inputs", "--out", "-", "--dataset-dir", str(data), "--dataset-file", "labels.csv"])

    message = str(error.value.code)
    assert "criteria_review.csv" in message
    assert "enterprises.csv" in message


def test_no_dataset_flag_cannot_be_combined_with_dataset_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://example/never-used")

    with pytest.raises(SystemExit) as error:
        cli.main(["inputs", "--out", "-", "--no-dataset", "--dataset-file", "labels.csv"])

    assert "--no-dataset" in str(error.value.code)


# ------------------------------------------------- 一致视图与数据集映射（真库）


@pytest.mark.db
@pytest.mark.usefixtures("clean_tables")
def test_the_collection_runs_in_one_repeatable_read_read_only_transaction(
    repo, database_url_schema, monkeypatch
) -> None:
    """四条查询必须在同一个只读、可重复读的事务里跑（M2 复审 P2）。"""

    _make_enterprise(repo)
    seen: list[tuple[str, str]] = []
    original = snapshot_module._rows

    def spy(conn, sql):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT current_setting('transaction_isolation'),"
                " current_setting('transaction_read_only')"
            )
            seen.append(cur.fetchone())
        return original(conn, sql)

    monkeypatch.setattr(snapshot_module, "_rows", spy)

    with psycopg.connect(database_url_schema) as conn:
        collect_input_snapshot(conn)

    assert seen, "一次查询都没跑？"
    assert set(seen) == {("repeatable read", "on")}


@pytest.mark.db
@pytest.mark.usefixtures("clean_tables")
def test_a_write_that_lands_midway_is_not_mixed_into_the_snapshot(
    repo, database_url_schema, monkeypatch
) -> None:
    """另一连接在两次查询之间提交的写入，不能出现在这次清单里。"""

    enterprise = _make_enterprise(repo)
    original = snapshot_module._rows
    inserted = {"done": False}

    def rows_with_interleaved_write(conn, sql):
        rows = original(conn, sql)
        if not inserted["done"]:
            inserted["done"] = True
            with psycopg.connect(database_url_schema, autocommit=True) as other:
                other.execute(
                    "INSERT INTO enterprise_fact (enterprise_id, key, period_start,"
                    " period_end, unit, source, verified, value_amount)"
                    " VALUES (%s, 'revenue', '2026-01-01', '2026-12-31', 'CNY',"
                    " 'user_input', false, 1000)",
                    (enterprise.id,),
                )
        return rows

    monkeypatch.setattr(snapshot_module, "_rows", rows_with_interleaved_write)

    with psycopg.connect(database_url_schema) as conn:
        manifest = collect_input_snapshot(conn)

    entry = next(
        item for item in manifest["enterprises"] if item["enterpriseId"] == str(enterprise.id)
    )
    assert all(fact["key"] != "revenue" for fact in entry["facts"]), "中途提交的写入混进了清单"

    # 换一次采集就能看到它：证明上面不是「永远看不到」，而是同一视图内看不到
    with psycopg.connect(database_url_schema) as conn:
        later = collect_input_snapshot(conn)
    later_entry = next(
        item for item in later["enterprises"] if item["enterpriseId"] == str(enterprise.id)
    )
    assert any(fact["key"] == "revenue" for fact in later_entry["facts"])


def _write_dataset(directory: Path, *, income: str = "3500000.00", title: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "criteria_review.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["policy_id", "criterion_id", "source_version", "adjudicated_expression"],
        )
        writer.writeheader()
        for code, expression in (("G1", INCOME_GATE), ("G2", INDUSTRY_GATE)):
            writer.writerow(
                {
                    "policy_id": "DEMO",
                    "criterion_id": code,
                    "source_version": title,
                    "adjudicated_expression": json.dumps(expression, ensure_ascii=False),
                }
            )
    with (directory / "enterprises.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "enterprise_id",
                "name_synthetic",
                "fact_key",
                "fact_value",
                "unit",
                "period",
                "source",
                "verified",
            ],
        )
        writer.writeheader()
        for key, value, unit in (
            ("region", "440300", "code"),
            ("industry", "C39", "code"),
            ("taxableIncome", income, "CNY"),
        ):
            writer.writerow(
                {
                    "enterprise_id": "E1",
                    "name_synthetic": "合成企业A",
                    "fact_key": key,
                    "fact_value": value,
                    "unit": unit,
                    "period": "2026-01-01/2026-12-31",
                    "source": "user_input",
                    "verified": "false",
                }
            )
    with (directory / "labels.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "case_id",
                "enterprise_id",
                "policy_id",
                "policy_version",
                "criterion_id",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "case_id": "C001",
                "enterprise_id": "E1",
                "policy_id": "DEMO",
                "policy_version": title,
                "criterion_id": "G1",
            }
        )
    with (directory / "source_register.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["policy_id", "version", "title"])
        writer.writeheader()
        # 库里这份政策没有文号，版本标识就是标题
        writer.writerow({"policy_id": "DEMO", "version": "", "title": title})


@pytest.mark.db
@pytest.mark.usefixtures("clean_tables")
def test_the_cli_maps_dataset_rows_onto_database_rows(
    repo, database_url_schema, monkeypatch, tmp_path: Path
) -> None:
    """CSV 行能逐条对到库里；改掉 CSV 里的值后 verify 必须非零退出。"""

    monkeypatch.setenv("DATABASE_URL", database_url_schema)
    _make_enterprise(repo)
    _publish_policy(repo, [INCOME_GATE, INDUSTRY_GATE])
    title = "深圳市中小企业数字化转型扶持计划"
    data = tmp_path / "data"
    _write_dataset(data, title=title)
    sealed = tmp_path / "sealed.json"

    args = _dataset_args(data)

    assert cli.main(["inputs", "--out", str(sealed), *args]) == 0
    manifest = json.loads(sealed.read_text(encoding="utf-8"))
    mapping = manifest["datasetMapping"]
    assert mapping["problems"] == [], mapping["problems"]
    assert [item["criterionCode"] for item in mapping["policies"][0]["criteria"]] == ["G1", "G2"]
    assert mapping["enterprises"][0]["code"] == "E1"
    assert mapping["caseCount"] == 1

    # 核对时不再点名文件：签认清单记着路径，自己会去读那一份
    assert cli.main(["verify", str(sealed), "--no-config"]) == 0

    # 签认的那份文件被改了：哈希变了，逐条映射也对不上了
    _write_dataset(data, income="3600000.00", title=title)

    assert cli.main(["verify", str(sealed), "--no-config"]) == 1


def _keep_only_header(path: Path) -> None:
    """把一份 CSV 清成「只有表头」：表在、行没了。"""

    path.write_bytes(path.read_bytes().splitlines(keepends=True)[0])


def _dataset_args(directory: Path) -> list[str]:
    args = ["--no-config"]
    for name in (
        "labels.csv",
        "criteria_review.csv",
        "enterprises.csv",
        "source_register.csv",
    ):
        args += ["--dataset-file", name]
    return args + ["--dataset-dir", str(directory)]


@pytest.mark.db
@pytest.mark.usefixtures("clean_tables")
def test_a_database_only_manifest_must_be_verified_in_that_mode(
    repo, database_url_schema, monkeypatch, tmp_path: Path
) -> None:
    """只比数据库的清单，`verify` 不带 `--no-dataset` 就拒绝——免得被读成「数据集核对通过」。"""

    monkeypatch.setenv("DATABASE_URL", database_url_schema)
    _make_enterprise(repo)
    sealed = tmp_path / "sealed.json"
    assert cli.main(["inputs", "--out", str(sealed), "--no-config", "--no-dataset"]) == 0

    with pytest.raises(SystemExit) as error:
        cli.main(["verify", str(sealed), "--no-config"])

    assert "--no-dataset" in str(error.value.code)


@pytest.mark.db
@pytest.mark.usefixtures("clean_tables")
def test_a_dataset_manifest_cannot_be_verified_as_database_only(
    repo, database_url_schema, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url_schema)
    _make_enterprise(repo)
    _publish_policy(repo, [INCOME_GATE, INDUSTRY_GATE])
    data = tmp_path / "data"
    _write_dataset(data, title="深圳市中小企业数字化转型扶持计划")
    sealed = tmp_path / "sealed.json"
    args = _dataset_args(data)
    assert cli.main(["inputs", "--out", str(sealed), *args]) == 0

    with pytest.raises(SystemExit) as error:
        cli.main(["verify", str(sealed), "--no-config", "--no-dataset"])

    assert "--no-dataset" in str(error.value.code)


@pytest.mark.db
@pytest.mark.usefixtures("clean_tables")
@pytest.mark.parametrize(
    "empty", ["labels.csv", "criteria_review.csv", "enterprises.csv", "source_register.csv"]
)
def test_verify_fails_when_one_table_has_no_rows(
    empty, repo, database_url_schema, monkeypatch, tmp_path: Path
) -> None:
    """空表不算「对上了」：签认时带问题、核对时必须非零退出（M2 复审：缺条件/企业/用例）。"""

    monkeypatch.setenv("DATABASE_URL", database_url_schema)
    _make_enterprise(repo)
    _publish_policy(repo, [INCOME_GATE, INDUSTRY_GATE])
    data = tmp_path / "data"
    _write_dataset(data, title="深圳市中小企业数字化转型扶持计划")
    _keep_only_header(data / empty)
    sealed = tmp_path / "sealed.json"
    args = _dataset_args(data)

    assert cli.main(["inputs", "--out", str(sealed), *args]) == 0
    manifest = json.loads(sealed.read_text(encoding="utf-8"))
    assert manifest["datasetMapping"]["problems"], f"{empty} 清空后应留下未解决问题"

    assert cli.main(["verify", str(sealed), "--no-config"]) == 1


# ------------------------------------------------- 签认路径（真库 + 仓库布局）


def _write_split_dataset(root: Path, *, title: str) -> list[str]:
    """按仓库真实布局写四份输入：标注在 ``data/evaluation``、条件与登记表在
    ``data/policies``、企业在 ``data/fixtures``——只记文件名的话，这四份根本没法重现。"""

    staging = root / "staging"
    _write_dataset(staging, title=title)
    layout = {
        "labels.csv": root / "data" / "evaluation" / "labels.csv",
        "criteria_review.csv": root / "data" / "policies" / "criteria_review.csv",
        "source_register.csv": root / "data" / "policies" / "source_register.csv",
        "enterprises.csv": root / "data" / "fixtures" / "enterprises.csv",
    }
    for name, target in layout.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((staging / name).read_bytes())
    return sorted(path.relative_to(root).as_posix() for path in layout.values())


def _dataset_args_for(paths: list[str]) -> list[str]:
    args: list[str] = []
    for path in paths:
        args += ["--dataset-file", path]
    return args


@pytest.mark.db
@pytest.mark.usefixtures("clean_tables")
def test_the_repository_layout_survives_verifying_from_another_directory(
    repo, database_url_schema, monkeypatch, tmp_path: Path
) -> None:
    """四份输入分散在三个目录：清单记相对项目根的路径，换个工作目录也读得到（M2 P1）。"""

    monkeypatch.setenv("DATABASE_URL", database_url_schema)
    _make_enterprise(repo)
    _publish_policy(repo, [INCOME_GATE, INDUSTRY_GATE])
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setattr(cli, "PROJECT_ROOT", root)
    monkeypatch.chdir(root)
    signed = _write_split_dataset(root, title="深圳市中小企业数字化转型扶持计划")

    sealed = root / "sealed.json"
    args = ["inputs", "--out", str(sealed), "--no-config"]
    assert cli.main([*args, *_dataset_args_for(signed)]) == 0

    manifest = json.loads(sealed.read_text(encoding="utf-8"))
    assert [item["path"] for item in manifest["dataset"]] == signed
    assert manifest["datasetMapping"]["files"] == signed
    assert manifest["datasetMapping"]["problems"] == []

    # 换个目录核对：路径以项目根为基准，不能变成「相对当前工作目录」
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert cli.main(["verify", str(sealed), "--no-config"]) == 0

    # 签认的那份文件改了：报的是签认路径。列要齐——缺列会先在读文件那一步停下，
    # 这条用例要验的是「按签认路径读到了被改的那份」，不是缺列报错
    (root / "data" / "evaluation" / "labels.csv").write_text(
        "case_id,enterprise_id,policy_id,policy_version,criterion_id\n"
        "C999,E1,DEMO,别的计划,G1\n",
        encoding="utf-8",
    )
    assert cli.main(["verify", str(sealed), "--no-config"]) == 1


def test_verify_refuses_to_reselect_the_dataset_files_on_the_command_line(
    tmp_path: Path, monkeypatch
) -> None:
    """后传的文件不能顶替签认对象：``verify`` 只按清单里的路径读（M2 P1）。"""

    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setattr(cli, "PROJECT_ROOT", root)
    monkeypatch.setenv("DATABASE_URL", "postgresql://example/never-used")
    signed = _write_split_dataset(root, title="某计划")
    sealed = tmp_path / "sealed.json"
    sealed.write_text(
        json.dumps(
            {
                "manifestVersion": MANIFEST_VERSION,
                "dataset": [{"path": path, "sha256": "0" * 64} for path in signed],
                "datasetMapping": {"files": signed, "problems": []},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as error:
        cli.main(["verify", str(sealed), "--dataset-dir", str(root / "data")])

    message = str(error.value.code)
    assert "签认清单决定" in message
    assert "data/evaluation/labels.csv" in message


def test_verify_names_a_signed_dataset_file_that_is_gone(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setattr(cli, "PROJECT_ROOT", root)
    monkeypatch.setenv("DATABASE_URL", "postgresql://example/never-used")
    signed = _write_split_dataset(root, title="某计划")
    (root / "data" / "policies" / "criteria_review.csv").unlink()
    sealed = tmp_path / "sealed.json"
    sealed.write_text(
        json.dumps(
            {
                "manifestVersion": MANIFEST_VERSION,
                "dataset": [{"path": path, "sha256": "0" * 64} for path in signed],
                "datasetMapping": {"files": signed, "problems": []},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as error:
        cli.main(["verify", str(sealed), "--no-config"])

    message = str(error.value.code)
    assert "data/policies/criteria_review.csv" in message
    assert "不存在" in message


def test_verify_rejects_process_pid_that_it_never_used(capsys, monkeypatch) -> None:
    """``verify`` 曾收下 ``--process-pid`` 却从不读它（M2 2026-11-11 复审）。

    留着这个空参数等于默许「用 verify 证明进程身份」。进程启动时间只由
    ``inputs --process-pid`` 记进清单，``verify`` 不接受这个参数。
    """

    monkeypatch.setenv("DATABASE_URL", "postgresql://example/never-used")

    with pytest.raises(SystemExit) as error:
        cli.main(["verify", "sealed.json", "--process-pid", "1"])

    assert error.value.code == 2
    assert "process-pid" in capsys.readouterr().err


def test_inputs_still_records_the_process_pid() -> None:
    args = cli.build_parser().parse_args(["inputs", "--process-pid", "42"])
    assert args.process_pid == 42


def test_verify_builds_a_manifest_without_any_process_pid() -> None:
    """``verify`` 的命名空间里现在没有 ``process_pid``：拼清单不能因此抛 AttributeError。"""

    args = cli.build_parser().parse_args(["verify", "sealed.json"])
    inspection = cli._inspect_code()
    assert cli._process_started(args, inspection) is None


def test_the_same_file_cannot_be_signed_twice(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://example/never-used")
    data = tmp_path / "evaluation"
    _write_dataset(data, title="某计划")

    with pytest.raises(SystemExit) as error:
        cli.main(
            [
                "inputs",
                "--out",
                "-",
                "--dataset-dir",
                str(data),
                "--dataset-file",
                "labels.csv",
                "--dataset-file",
                "./labels.csv",
            ]
        )

    assert "签认了两次" in str(error.value.code)


def test_two_files_with_the_same_name_in_different_directories_are_refused(
    tmp_path: Path, monkeypatch
) -> None:
    """不同目录的同名文件一起签：映射按文件名区分四份输入，后一个会顶替前一个。"""

    monkeypatch.setenv("DATABASE_URL", "postgresql://example/never-used")
    first = tmp_path / "first"
    second = tmp_path / "second"
    for directory in (first, second):
        _write_dataset(directory, title="某计划")

    with pytest.raises(SystemExit) as error:
        cli.main(
            [
                "inputs",
                "--out",
                "-",
                "--dataset-file",
                str(first / "labels.csv"),
                "--dataset-file",
                str(second / "labels.csv"),
            ]
        )

    message = str(error.value.code)
    assert "同名" in message
    assert "first" in message and "second" in message
