"""``infra/scripts/import_eval_dataset.py`` 的干跑口径（不连库、不写库）。

这份脚本把 ``data/`` 里已仲裁的数据灌进服务库，写库之前必须先证明「这些条款按产品
自己的引用校验规则都过得了」——否则线上会留下一个发布不了的政策版本。这里锁住三件事：

* 数据集能被拆成 7 个政策版本、26 条条件，且每条引文都能在某个来源正文里逐字定位；
* 每个版本整版 ``passed``（一条 failed 就挡住发布，这是 ``publish`` 的硬闸门）；
* 同一政策的两个版本（国科发火 2008/2016）各自带走自己的条件，互不串台。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "infra" / "scripts" / "import_eval_dataset.py"


@pytest.fixture(scope="module")
def importer() -> ModuleType:
    spec = importlib.util.spec_from_file_location("import_eval_dataset", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses 解析注解时要回查 sys.modules[cls.__module__]，不登记就抛 AttributeError。
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


def test_每个政策版本都能整版发布(importer: ModuleType) -> None:
    plans, problems = importer.build_plans()

    assert problems == []
    assert len(plans) == 7
    assert sum(len(plan.criteria) for plan in plans) == 26

    for plan in plans:
        blocked = [crit.code for crit in plan.criteria if crit.problem is not None]
        assert blocked == [], f"{plan.document} 有引文定位不了：{blocked}"

        status, issues = importer._validate(plan, importer._draft_for(plan))
        assert set(status) == {crit.code for crit in plan.criteria}
        assert {code for code, value in status.items() if value != "passed"} == set(), (
            f"{plan.document} 校验未通过：{issues}"
        )


def test_同一政策的两个版本各带各的条件(importer: ModuleType) -> None:
    plans, _ = importer.build_plans()
    by_document = {plan.document: plan for plan in plans}

    assert [crit.code for crit in by_document["国科发火〔2008〕172号"].criteria] == ["GXJS-V1-001"]
    assert [crit.code for crit in by_document["国科发火〔2016〕32号"].criteria] == ["GXJS-V2-001"]


def test_跨文件引用的出处被作为附带来源导入(importer: ModuleType) -> None:
    plans, _ = importer.build_plans()
    by_document = {plan.document: plan for plan in plans}

    # HT-007 引自《财税〔2015〕119号》，不在深科技创新规〔2021〕5号的正文里。
    attached = [name for name, _ in by_document["深科技创新规〔2021〕5号"].attachments]
    assert attached == ["财税〔2015〕119号"]
