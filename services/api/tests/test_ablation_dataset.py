"""消融对照的输入装载：条数、分层与「原文不可用」的判定。

这些数字是报告里的分母。分母一旦悄悄变了（多一条标签、少一个 case），报告里的
百分比就全错，所以逐个钉住。
"""

from __future__ import annotations

import pytest

from app.ablation.dataset import (
    DatasetError,
    load_dataset,
    load_labels,
    normalize_for_match,
)

@pytest.fixture(scope="module")
def dataset():
    return load_dataset()


def test_case_and_label_counts(dataset):
    assert len(dataset.cases) == 32
    assert len(dataset.scored_labels()) == 146
    assert len(dataset.excluded) == 2
    assert len(dataset.excluded_labels()) == 2
    assert len(dataset.labels_by_split("dev")) == 120
    assert len(dataset.labels_by_split("holdout")) == 26


def test_label_distribution(dataset):
    counted: dict[str, int] = {}
    for label in dataset.scored_labels():
        counted[label.result] = counted.get(label.result, 0) + 1
    assert counted == {"satisfied": 71, "insufficient_evidence": 64, "not_satisfied": 11}


def test_excluded_case_is_the_expired_version(dataset):
    """2008 版在接口里解析不到（只解析到 2016 版），因此不进分母。"""
    assert {case.case_id for case in dataset.excluded} == {"C031", "C033"}
    for case in dataset.excluded:
        assert case.document == "国科发火〔2008〕172号"


def test_only_one_criterion_has_no_usable_source_text(dataset):
    assert dataset.unverifiable_criteria() == frozenset({("CN-TAX-RD-2023-007", "RD-001")})


def test_every_scored_criterion_has_an_adjudicated_expression(dataset):
    for case in dataset.cases:
        for code in case.criterion_codes:
            criterion = dataset.criterion(case, code)
            assert criterion.expression, f"{case.case_id}/{code} 没有仲裁表达式"
            assert criterion.spans, f"{case.case_id}/{code} 没有原文片段"


def test_source_text_rejects_excerpt_files(tmp_path):
    """节选文件（几百字）不能当政策正文：基线拿它判案就是拿残页当全文。"""

    data = tmp_path
    (data / "policies" / "sources").mkdir(parents=True)
    (data / "evaluation").mkdir()
    (data / "policies" / "sources" / "V1.txt").write_text("太短了。" * 10, encoding="utf-8")
    _write(tmp_path, "policies/source_register.csv",
           "policy_id,title,type,issuer,region,source_url,version,published_at,"
           "effective_from,effective_to,application_open,application_close,status\n"
           "P1,标题,tax,国家税务总局,CN,https://www.gov.cn/a,V1,2026-01-01,2026-01-01,,,,published\n")
    _write(tmp_path, "policies/criteria_review.csv",
           "policy_id,criterion_id,source_version,source_span,source_page_or_section,"
           "adjudicated_expression,review_status\n")
    _write(tmp_path, "fixtures/enterprises.csv",
           "enterprise_id,name_synthetic,region,industry,fact_key,fact_value,unit,period,"
           "source,verified\n")
    _write(tmp_path, "evaluation/labels.csv",
           "case_id,enterprise_id,policy_id,policy_version,criterion_id,"
           "adjudicated_result,split\n")

    from app.ablation.dataset import load_policies

    policies = load_policies(data)
    from app.ablation.dataset import Dataset

    broken = Dataset(
        data_dir=data,
        policies=policies,
        criteria={},
        enterprises={},
        cases=(),
        excluded=(),
    )
    with pytest.raises(DatasetError, match="节选文件"):
        broken.source_text(policies[("P1", "V1")])


def test_manifest_covers_the_four_input_files(dataset):
    manifest = dataset.manifest()
    assert set(manifest) == {
        "evaluation/labels.csv",
        "policies/criteria_review.csv",
        "fixtures/enterprises.csv",
        "policies/source_register.csv",
    }
    assert all(len(value) == 64 for value in manifest.values())


def test_normalize_for_match_handles_fullwidth_and_spaces():
    assert normalize_for_match("（一） 研发费用") == normalize_for_match("(一)研发费用")


def test_labels_reject_unknown_policy(tmp_path):
    _write(tmp_path, "evaluation/labels.csv",
           "case_id,enterprise_id,policy_id,policy_version,criterion_id,"
           "adjudicated_result,split\n"
           "C1,E1,P-UNKNOWN,V1,X1,satisfied,dev\n")
    with pytest.raises(DatasetError, match="政策版本不在"):
        load_labels(tmp_path, policies={}, criteria={}, enterprises={})


def _write(root, name, text: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
