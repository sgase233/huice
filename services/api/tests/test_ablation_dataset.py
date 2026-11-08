"""消融对照的输入装载：条数、分层与「原文不可用」的判定。

这些数字是报告里的分母。分母一旦悄悄变了（多一条标签、少一个 case），报告里的
百分比就全错，所以逐个钉住。
"""

from __future__ import annotations

from pathlib import Path

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


def test_manifest_covers_the_files_actually_read(dataset):
    """四份 CSV 之外还要覆盖正文与逐段出处——只算 CSV 等于给输入签了个假名。"""

    manifest = dataset.manifest()

    assert {
        "evaluation/labels.csv",
        "policies/criteria_review.csv",
        "fixtures/enterprises.csv",
        "policies/source_register.csv",
    } <= set(manifest)

    # 正文按政策代号或文号命名；HT-007 引用的 119号 是逐段声明的出处原文。
    assert "policies/sources/SZ-GRANT-SRDI-2025.txt" in manifest
    assert "policies/sources/财税〔2015〕119号.txt" in manifest
    assert all(len(value) == 64 for value in manifest.values())


def _mini_dataset(root: Path, body: str = "正文" * 120) -> Dataset:
    """一份最小的 data/：一份政策、一条条件，用来验证清单覆盖哪些文件。"""

    from app.ablation.dataset import load_policies

    _write(root, "policies/sources/P1.txt", body)
    _write(
        root,
        "policies/source_register.csv",
        "policy_id,title,type,issuer,region,source_url,version,published_at,"
        "effective_from,effective_to,application_open,application_close,status\n"
        "P1,标题,其他,某局,CN,https://www.gov.cn/a,D1,2024-01-01,2024-01-01,,,,\n",
    )
    _write(root, "policies/criteria_review.csv",
           "policy_id,criterion_id,source_span,source_page_or_section,"
           "adjudicated_expression,source_version,source_document,review_status\n")
    _write(root, "evaluation/labels.csv",
           "case_id,enterprise_id,policy_id,policy_version,criterion_id,"
           "adjudicated_result,split\n")
    _write(root, "fixtures/enterprises.csv",
           "enterprise_id,name_synthetic,region,industry,fact_key,fact_value,unit,period,"
           "source,verified\n")
    criterion = Criterion(
        code="X1",
        policy_code="P1",
        source_span="正文",
        section="第一条",
        expression={"op": "manual_review"},
        version_document="D1",
    )
    return Dataset(
        data_dir=root,
        policies=load_policies(root),
        criteria={("P1", "X1"): criterion},
        enterprises={},
        cases=(),
        excluded=(),
    )


def test_manifest_detects_a_single_changed_body_character(tmp_path):
    root = tmp_path / "data"
    before = _mini_dataset(root).manifest()
    after = _mini_dataset(root, body="正文" * 120 + "。").manifest()

    assert before["policies/sources/P1.txt"] != after["policies/sources/P1.txt"]
    assert [key for key in before if key != "policies/sources/P1.txt"] == [
        key for key in after if key != "policies/sources/P1.txt"
    ]


def test_span_source_refuses_a_declared_document_without_file(tmp_path):
    """声明了出处就必须用那一份；找不到原文是缺口，不拿政策正文顶替。"""

    root = tmp_path / "data"
    _write(root, "policies/sources/P1.txt", "正文" * 120)
    criterion = Criterion(
        code="X1",
        policy_code="P1",
        source_span="某句引文",
        section="第一条",
        expression={},
        documents=("不存在〔2020〕1号",),
        version_document="D1",
    )
    dataset = Dataset(
        data_dir=root,
        policies={},
        criteria={("P1", "X1"): criterion},
        enterprises={},
        cases=(),
        excluded=(),
    )

    with pytest.raises(DatasetError, match="没有原文"):
        dataset.span_source(criterion, 0)


def test_span_source_refuses_misaligned_documents(tmp_path):
    root = tmp_path / "data"
    _write(root, "policies/sources/P1.txt", "正文" * 120)
    criterion = Criterion(
        code="X2",
        policy_code="P1",
        source_span="第一句|第二句",
        section="第一条",
        expression={},
        documents=("甲〔2020〕1号",),
        version_document="D1",
    )
    dataset = Dataset(
        data_dir=root,
        policies={},
        criteria={("P1", "X2"): criterion},
        enterprises={},
        cases=(),
        excluded=(),
    )

    with pytest.raises(DatasetError, match="无法逐段对应"):
        dataset.span_source(criterion, 0)


def test_manifest_covers_the_version_document_fallback(tmp_path):
    """``source_document`` 为空时，引文核对实际读的是 ``version_document`` 那份原文。"""

    root = tmp_path / "data"
    _write(root, "policies/sources/D1.txt", "另一份出处" * 60)
    before = _mini_dataset(root).manifest()
    assert "policies/sources/D1.txt" in before

    _write(root, "policies/sources/D1.txt", "改过的出处" * 60)
    changed = _mini_dataset(root).manifest()
    assert changed["policies/sources/D1.txt"] != before["policies/sources/D1.txt"]

    (root / "policies" / "sources" / "D1.txt").unlink()
    removed = _mini_dataset(root).manifest()
    assert "policies/sources/D1.txt" not in removed
    assert removed != changed


def test_manifest_fails_when_a_source_file_is_missing(tmp_path):
    root = tmp_path / "data"
    dataset = _mini_dataset(root)
    (root / "policies" / "sources" / "P1.txt").unlink()

    with pytest.raises(DatasetError, match="找不到正文"):
        dataset.manifest()


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
