"""消融对照的输入装载：条数、分层与「原文不可用」的判定。

这些数字是报告里的分母。分母一旦悄悄变了（多一条标签、少一个 case），报告里的
百分比就全错，所以逐个钉住。
"""

from __future__ import annotations

import pytest

from app.ablation.dataset import (
    Criterion,
    Dataset,
    DatasetError,
    _aligned_documents,
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
    """现状：只有 RD-001 的引文在本仓可用原文里找不到。

    它目前仍带着 ``source_version = 财税〔2015〕119号``，而那一份在 sources/ 下只有
    220 字节的节选（只含「其他相关费用 10%」），负面清单不在里面。

    归属改成 2023 年第7号之后（``source_document`` 保持空，引文在
    ``CN-TAX-RD-2023-007.txt`` 第四条里逐字可查），这个集合会变成空集。改数据的那次
    提交必须同步改本节与下面两条 metrics 的分母（140 → 146），不能只改 CSV。
    """

    assert dataset.unverifiable_criteria() == frozenset({("CN-TAX-RD-2023-007", "RD-001")})


def test_出处列必须与引文段数逐段对齐():
    """段数对不上就报错：不能猜哪一段属于哪份文件，猜错了结论看起来还是对的。"""

    assert _aligned_documents({"source_span": "甲|乙", "source_document": ""}, "某条") == ()
    assert _aligned_documents(
        {"source_span": "甲|乙", "source_document": "甲文|乙文"}, "某条"
    ) == ("甲文", "乙文")

    with pytest.raises(DatasetError, match="无法逐段对齐"):
        _aligned_documents({"source_span": "甲|乙", "source_document": "甲文"}, "某条")


def _dataset_with(tmp_path, criterion):
    sources = tmp_path / "policies" / "sources"
    sources.mkdir(parents=True)
    (sources / "甲文.txt").write_text("甲段原文", encoding="utf-8")
    (sources / "乙文.txt").write_text("乙段原文", encoding="utf-8")
    return Dataset(
        data_dir=tmp_path,
        policies={},
        criteria={("P", criterion.code): criterion},
        enterprises={},
        cases=(),
        excluded=(),
    )


def _two_span_criterion(documents):
    return Criterion(
        code="X-001",
        policy_code="P",
        source_span="甲段原文|乙段原文",
        section="一",
        expression={"op": "manual_review"},
        documents=documents,
    )


def test_每段引文对各自的出处校验(tmp_path):
    """两段引文分别来自两份文件时，两段都找得到才算可验证。"""

    dataset = _dataset_with(tmp_path, _two_span_criterion(("甲文", "乙文")))

    assert dataset.unverifiable_criteria() == frozenset()


def test_引文不在它标注的那份文件里就算不可验证(tmp_path):
    """第二段标成甲文（实际在乙文里）必须被判定不可验证，不能拿第一段的正文糊弄。"""

    dataset = _dataset_with(tmp_path, _two_span_criterion(("甲文", "甲文")))

    assert dataset.unverifiable_criteria() == frozenset({("P", "X-001")})


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
