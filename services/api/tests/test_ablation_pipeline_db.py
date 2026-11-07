"""现方案的真库路径：灌政策与企业、逐 case 走 ``assess()``、折成与基线同形的记录。

不连真库就验不了这条链路——条款的 span、发布校验、快照解析全在 SQL 里。
只在设置 ``HUICE_TEST_DATABASE_URL`` 时运行（脚本自己建临时 schema 并删掉）。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.ablation import pipeline
from app.ablation.dataset import load_dataset
from app.domain.versioning import SHANGHAI

pytestmark = pytest.mark.db

AS_OF = datetime(2026, 11, 6, 12, 0, tzinfo=SHANGHAI)


@pytest.fixture(scope="module")
def dataset():
    return load_dataset()


@pytest.fixture(scope="module")
def seeded(dataset, database_url):
    with pipeline.temporary_repository(
        database_url, "huice_ablation_test", cursor_secret="t" * 64
    ) as repository:
        seed = pipeline.seed(dataset, repository)
        yield seed, repository


def test_seed_covers_exactly_the_scored_cases(dataset, seeded):
    seed, _ = seeded
    used = {(case.policy_code, case.document) for case in dataset.cases}
    assert set(seed.policies) == used
    assert set(seed.enterprises) == {case.enterprise for case in dataset.cases}


def test_expired_version_is_not_seeded(dataset, seeded):
    seed, _ = seeded
    assert ("CN-TAX-GXJS", "国科发火〔2008〕172号") not in seed.policies
    assert "GXJS-V1-001" not in seed.policies[("CN-TAX-GXJS", "国科发火〔2016〕32号")].criteria


def test_every_seeded_policy_carries_all_label_criteria(dataset, seeded):
    seed, _ = seeded
    for case in dataset.cases:
        seeded_codes = set(seed.policies[(case.policy_code, case.document)].criteria)
        assert set(case.criterion_codes) <= seeded_codes


def test_run_case_returns_one_record_per_label(dataset, seeded):
    seed, repository = seeded
    case = dataset.case_for("C001")
    payload = pipeline.run_case(dataset, seed, repository, case, as_of=AS_OF, llm=None)
    codes = [item["criterionId"] for item in payload["results"]]
    assert sorted(codes) == sorted(case.criterion_codes)
    for item in payload["results"]:
        assert item["result"] in {"satisfied", "not_satisfied", "insufficient_evidence", "conflict"}
        assert item["reason"]


def test_expired_policy_is_flagged_the_way_the_read_path_does(dataset, seeded):
    seed, repository = seeded
    expired = dataset.case_for("C013")  # 专精特新 2025：适用期与申报窗口都已过
    payload = pipeline.run_case(dataset, seed, repository, expired, as_of=AS_OF, llm=None)
    assert payload["policyExpired"] is True
    assert "policy_expired" in payload["staleReasons"]

    current = dataset.case_for("C001")  # 小微企业税费优惠：2027-12-31 才到期
    payload = pipeline.run_case(dataset, seed, repository, current, as_of=AS_OF, llm=None)
    assert payload["policyExpired"] is False
