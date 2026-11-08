"""数据集（CSV）与库行的逐条映射：对得上、对不上、以及「文件稳定但数据无关」。

``M2`` 在 PR #39 上指出：整文件哈希只能证明 CSV 没变，证明不了它导入的就是库里那份；
两套互不相关但各自稳定的数据跑两次，``verify`` 照样通过。这里用纯函数覆盖对齐规则。
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from app.evaluation.dataset_map import (
    ALIGNMENT_RULES,
    align_dataset,
    load_dataset_rows,
    read_dataset_rows,
)
from app.evaluation.snapshot import SnapshotError, expression_fingerprint, sha256_hex

DOCUMENT = "财政部 税务总局公告2023年第12号"
ENTRY_GATE = {
    "op": "compare",
    "factKey": "taxableIncome",
    "period": "assessment",
    "comparator": "lte",
    "value": "3000000.00",
    "unit": "CNY",
}


def _manifest(**overrides):
    manifest = {
        "policies": [
            {
                "policyId": "p1",
                "policyVersionId": "v1",
                "version": "v1.0",
                "title": "小型微利企业所得税优惠",
                "documentNumber": DOCUMENT,
                "policyType": "tax",
                "reviewStatus": "published",
                "importedAt": "2026-11-01T00:00:00+00:00",
                "revision": "r1",
                "criteria": [
                    {
                        "criterionId": "c1",
                        "title": "年度应纳税所得额不超过 300 万元",
                        "expressionFingerprint": expression_fingerprint(ENTRY_GATE),
                        "reviewFinding": "none",
                        "validationStatus": "passed",
                    }
                ],
                "criteriaSetFingerprint": "s1",
            }
        ],
        "enterprises": [
            {
                "enterpriseId": "e1",
                "nameSynthetic": "合成企业A",
                "period": {"startDate": "2026-01-01", "endDate": "2026-12-31"},
                "factVersion": "fv",
                "facts": [
                    {
                        "key": "taxableIncome",
                        "periodStart": "2026-01-01",
                        "periodEnd": "2026-12-31",
                        "unit": "CNY",
                        "source": "synthetic_material",
                        "verified": True,
                        "valueHash": sha256_hex(Decimal("2800000.00")),
                    }
                ],
            }
        ],
    }
    manifest.update(overrides)
    return manifest


def _datasets(criteria=None, enterprises=None, labels=None):
    return {
        "criteria_review.csv": criteria
        if criteria is not None
        else [
            {
                "policy_id": "CN-TAX-SME-2023-012",
                "criterion_id": "SME-002",
                "source_version": DOCUMENT,
                "adjudicated_expression": json.dumps(ENTRY_GATE, ensure_ascii=False),
            }
        ],
        "enterprises.csv": enterprises
        if enterprises is not None
        else [
            {
                "enterprise_id": "E-ENT-001",
                "name_synthetic": "合成企业A",
                "fact_key": "taxableIncome",
                "fact_value": "2800000.00",
                "unit": "CNY",
                "period": "2026-01-01/2026-12-31",
                "source": "synthetic_material",
                "verified": "true",
            }
        ],
        "labels.csv": labels
        if labels is not None
        else [
            {
                "case_id": "C001",
                "enterprise_id": "E-ENT-001",
                "policy_version": DOCUMENT,
                "criterion_id": "SME-002",
            }
        ],
    }


def test_matching_files_map_to_the_rows_in_the_database() -> None:
    mapping = align_dataset(_datasets(), _manifest())

    assert mapping["problems"] == []
    assert mapping["rules"] == ALIGNMENT_RULES
    assert mapping["policies"] == [
        {
            "identifier": DOCUMENT,
            "policyVersionId": "v1",
            "criteria": [{"criterionCode": "SME-002", "criterionId": "c1"}],
        }
    ]
    assert mapping["enterprises"] == [
        {"code": "E-ENT-001", "nameSynthetic": "合成企业A", "enterpriseId": "e1"}
    ]
    assert mapping["caseCount"] == 1


def test_a_different_value_in_the_csv_is_reported() -> None:
    # 文件哈希完全不同也没用：这条 CSV 说的 300 万，库里存的是别的数
    enterprises = _datasets()["enterprises.csv"]
    enterprises[0]["fact_value"] = "3100000.00"

    problems = align_dataset(_datasets(enterprises=enterprises), _manifest())["problems"]

    assert any("taxableIncome" in item and "对不上" in item for item in problems)


def test_the_verified_flag_counts_as_input() -> None:
    # verified 变了不影响事实值，但会影响「够不够证据」的判断，必须算输入
    enterprises = _datasets()["enterprises.csv"]
    enterprises[0]["verified"] = "false"

    problems = align_dataset(_datasets(enterprises=enterprises), _manifest())["problems"]

    assert any("taxableIncome" in item for item in problems)


def test_a_changed_expression_is_reported() -> None:
    criteria = _datasets()["criteria_review.csv"]
    criteria[0]["adjudicated_expression"] = json.dumps(
        {**ENTRY_GATE, "value": "5000000.00"}, ensure_ascii=False
    )

    problems = align_dataset(_datasets(criteria=criteria), _manifest())["problems"]

    assert any("SME-002" in item and "相同表达式" in item for item in problems)


def test_a_criterion_in_the_database_without_a_csv_row_is_reported() -> None:
    manifest = _manifest()
    manifest["policies"][0]["criteria"].append(
        {
            "criterionId": "c2",
            "title": "从业人数不超过 300 人",
            "expressionFingerprint": "e" * 64,
            "reviewFinding": "none",
            "validationStatus": "passed",
        }
    )

    problems = align_dataset(_datasets(), manifest)["problems"]

    assert any("未被 criteria_review.csv 覆盖" in item for item in problems)


def test_a_fact_in_the_database_without_a_csv_row_is_reported() -> None:
    manifest = _manifest()
    manifest["enterprises"][0]["facts"].append(
        {
            "key": "revenue",
            "periodStart": "2026-01-01",
            "periodEnd": "2026-12-31",
            "unit": "CNY",
            "source": "synthetic_material",
            "verified": False,
            "valueHash": "0" * 64,
        }
    )

    problems = align_dataset(_datasets(), manifest)["problems"]

    assert any("未被 enterprises.csv 覆盖" in item for item in problems)


def test_an_unknown_enterprise_name_is_reported() -> None:
    enterprises = _datasets()["enterprises.csv"]
    enterprises[0]["name_synthetic"] = "另一家合成企业"

    problems = align_dataset(_datasets(enterprises=enterprises), _manifest())["problems"]

    assert any("没有同名企业" in item for item in problems)


def test_labels_pointing_at_an_unknown_criterion_are_reported() -> None:
    labels = _datasets()["labels.csv"]
    labels[0]["criterion_id"] = "SME-999"

    problems = align_dataset(_datasets(labels=labels), _manifest())["problems"]

    assert any("C001" in item and "SME-999" in item for item in problems)


def test_two_policy_versions_sharing_a_document_number_are_ambiguous() -> None:
    manifest = _manifest()
    twin = json.loads(json.dumps(manifest["policies"][0]))
    twin.update({"policyId": "p2", "policyVersionId": "v2", "version": "v1.1"})
    manifest["policies"].append(twin)

    problems = align_dataset(_datasets(), manifest)["problems"]

    assert any("多个政策版本" in item for item in problems)
    # 歧义时不猜：映射里不会给文号硬配一个版本
    assert align_dataset(_datasets(), manifest)["policies"] == []


def test_a_policy_version_without_a_document_number_falls_back_to_its_title() -> None:
    manifest = _manifest()
    manifest["policies"][0]["documentNumber"] = None
    criteria = _datasets()["criteria_review.csv"]
    criteria[0]["source_version"] = "小型微利企业所得税优惠"
    labels = _datasets()["labels.csv"]
    labels[0]["policy_version"] = "小型微利企业所得税优惠"

    mapping = align_dataset(_datasets(criteria=criteria, labels=labels), manifest)

    assert mapping["problems"] == []
    assert mapping["policies"][0]["identifier"] == "小型微利企业所得税优惠"


def test_an_empty_dataset_cannot_claim_the_mapping_was_done() -> None:
    mapping = align_dataset({}, _manifest())

    assert mapping["files"] == []
    assert mapping["policies"] == []
    assert mapping["caseCount"] == 0


def test_an_empty_expression_is_rejected_instead_of_guessed() -> None:
    criteria = _datasets()["criteria_review.csv"]
    criteria[0]["adjudicated_expression"] = ""

    with pytest.raises(SnapshotError):
        align_dataset(_datasets(criteria=criteria), _manifest())


def test_reading_a_csv_reports_missing_columns(tmp_path) -> None:
    path = tmp_path / "labels.csv"
    path.write_text("case_id,enterprise_id\nC001,E-ENT-001\n", encoding="utf-8")

    with pytest.raises(SnapshotError) as error:
        read_dataset_rows(path)

    assert "policy_version" in str(error.value)


def test_only_known_files_are_loaded(tmp_path) -> None:
    (tmp_path / "labels.csv").write_text(
        "case_id,enterprise_id,policy_version,criterion_id\nC001,E-ENT-001,文号,SME-002\n",
        encoding="utf-8",
    )
    (tmp_path / "material_review_sheet.csv").write_text("row_no\n1\n", encoding="utf-8")

    rows = load_dataset_rows(
        [tmp_path / "labels.csv", tmp_path / "material_review_sheet.csv"]
    )

    # 材料复核表不是评测输入，不会被拿来做映射
    assert set(rows) == {"labels.csv"}


def test_an_empty_value_matches_a_null_in_the_database() -> None:
    """CSV 空串与库里的 NULL 是同一个「未知」，不能一个记 null、一个记哈希。"""

    manifest = _manifest()
    manifest["enterprises"][0]["facts"][0]["valueHash"] = None
    enterprises = _datasets()["enterprises.csv"]
    enterprises[0]["fact_value"] = ""

    mapping = align_dataset(_datasets(enterprises=enterprises), manifest)

    assert mapping["problems"] == []


def test_an_unknown_value_is_not_the_same_as_zero() -> None:
    manifest = _manifest()
    manifest["enterprises"][0]["facts"][0]["valueHash"] = None
    enterprises = _datasets()["enterprises.csv"]
    enterprises[0]["fact_value"] = "0"

    problems = align_dataset(_datasets(enterprises=enterprises), manifest)["problems"]

    assert any("taxableIncome" in item for item in problems)


def test_a_citation_from_another_document_maps_by_content() -> None:
    """``source_version`` 记的是引文所在文件（如《财税〔2015〕119号》），照样能对上。

    数据侧用它表达「这条引文引自哪份文件」，不总是本政策的文号；映射按表达式指纹
    认归属，键落回库里的文号，``labels.csv`` 才找得到这条条件。
    """

    criteria = _datasets()["criteria_review.csv"]
    criteria[0]["source_version"] = "财税〔2015〕119号"

    mapping = align_dataset(_datasets(criteria=criteria), _manifest())

    assert mapping["problems"] == []
    assert mapping["policies"] == [
        {
            "identifier": DOCUMENT,
            "policyVersionId": "v1",
            "criteria": [{"criterionCode": "SME-002", "criterionId": "c1"}],
        }
    ]
    # 标注用例按文号找条件，键若停在「财税〔2015〕119号」这里就对不上了
    assert mapping["caseCount"] == 1


def test_a_cited_document_in_two_versions_is_ambiguous() -> None:
    """同一表达式在两个政策版本里都有：不按内容猜，报歧义。"""

    manifest = _manifest()
    twin = json.loads(json.dumps(manifest["policies"][0]))
    twin.update(
        {
            "policyId": "p2",
            "policyVersionId": "v2",
            "version": "v2.0",
            "documentNumber": "另一份文号",
            "title": "另一份政策",
        }
    )
    manifest["policies"].append(twin)
    criteria = _datasets()["criteria_review.csv"]
    criteria[0]["source_version"] = "财税〔2015〕119号"

    mapping = align_dataset(_datasets(criteria=criteria), manifest)

    assert any("无法判定对应哪一条" in item for item in mapping["problems"])
    assert mapping["policies"] == []


def test_every_required_file_must_be_loaded() -> None:
    datasets = _datasets()
    del datasets["labels.csv"]

    mapping = align_dataset(datasets, _manifest())

    assert any("labels.csv" in item and "没有加载" in item for item in mapping["problems"])


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("labels.csv", "没有可用的标注用例"),
        ("criteria_review.csv", "没有任何条件被对齐"),
        ("enterprises.csv", "没有任何企业对得上"),
    ],
)
def test_an_empty_table_is_not_a_completed_mapping(name: str, message: str) -> None:
    datasets = _datasets()
    datasets[name] = []

    mapping = align_dataset(datasets, _manifest())

    assert any(message in item for item in mapping["problems"])
