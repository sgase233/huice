"""``infra/scripts/import_eval_dataset.py`` 的干跑口径（不连库、不写库）。

这份脚本把 ``data/`` 里已仲裁的数据灌进服务库，写库之前必须先证明「这些条款按产品
自己的引用校验规则都过得了」——否则线上会留下一个发布不了的政策版本。这里锁住六件事：

* 数据集能被拆成 7 个政策版本、26 条条件，且每条引文都能在某个来源正文里逐字定位；
* 出处链接齐备时，每个版本整版 ``passed``（一条 failed 就挡住发布，这是 ``publish`` 的硬闸门）；
* 同一政策的两个版本（国科发火 2008/2016）各自带走自己的条件，互不串台；
* 预检不过时 ``--apply`` 也一行都不写；布尔事实的空值是未知，不是 ``false``；
* 重跑按库里状态分流：完整发布跳过、中断留下的草稿续跑、对不上的一律非零退出；
* 每条引文按**它自己那份来源**的官方链接写入；出处的链接没核实就不写这份附件，
  引用它的条件被判 ``blocked``——绝不借用主政策链接（#36 复审）。
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType, SimpleNamespace
from uuid import uuid4

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


#: 跨文件出处的官方链接映射。数据侧落在 ``data/policies/reference_sources.csv``；
#: 这里用临时文件顶替，免得测试依赖 M3 那份还没建的文件。
REFERENCE_CSV = (
    "document,source_url\n"
    "财税〔2015〕119号,https://www.chinatax.gov.cn/cs149.html\n"
)


def _with_reference(importer: ModuleType, monkeypatch, tmp_path) -> str:
    path = tmp_path / "reference_sources.csv"
    path.write_text(REFERENCE_CSV, encoding="utf-8")
    monkeypatch.setattr(importer, "REFERENCE_SOURCES", path)
    return "https://www.chinatax.gov.cn/cs149.html"


def test_每个政策版本都能整版发布(importer: ModuleType, monkeypatch, tmp_path) -> None:
    _with_reference(importer, monkeypatch, tmp_path)
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


def test_导入清单覆盖实际读取的文件(importer: ModuleType, monkeypatch, tmp_path) -> None:
    """只算 CSV 会给输入签假名：正文与出处映射变了，清单也必须变。"""

    _with_reference(importer, monkeypatch, tmp_path)
    manifest = importer.input_manifest()

    assert "data/policies/criteria_review.csv" in manifest
    assert any(key.endswith("sources/财税〔2015〕119号.txt") for key in manifest)
    assert any(key.endswith("reference_sources.csv") for key in manifest)
    assert {len(value) for value in manifest.values()} == {64}


def test_清单模式不连接数据库(importer: ModuleType, monkeypatch, tmp_path, capsys) -> None:
    def refuse(*args, **kwargs):
        raise AssertionError("--manifest 不该建库连接")

    _with_reference(importer, monkeypatch, tmp_path)
    monkeypatch.setattr(importer, "_apply", refuse)
    assert importer.main(["--manifest"]) == 0
    assert "data/policies/source_register.csv" in capsys.readouterr().out


def _mini_import_tree(
    importer: ModuleType,
    monkeypatch,
    tmp_path: Path,
    *,
    body: bool = True,
    attachment: bool = True,
    mapping: bool = True,
) -> Path:
    """一份最小的 ``data/``：一个政策版本、一条跨文件引用的已仲裁条件。"""

    data = tmp_path / "data"
    sources = data / "policies" / "sources"
    sources.mkdir(parents=True)
    (data / "evaluation").mkdir()
    (data / "fixtures").mkdir()
    if body:
        (sources / "P1.txt").write_text("正文" * 120, encoding="utf-8")
    if attachment:
        (sources / "附件〔2020〕1号.txt").write_text("另一份正文" * 60, encoding="utf-8")
    (data / "policies" / "source_register.csv").write_text(
        "policy_id,version\nP1,V1\n", encoding="utf-8"
    )
    (data / "policies" / "criteria_review.csv").write_text(
        "policy_id,criterion_id,review_status,source_version,source_document\n"
        "P1,X-001,已仲裁,V1,附件〔2020〕1号\n",
        encoding="utf-8",
    )
    (data / "evaluation" / "labels.csv").write_text(
        "policy_id,policy_version\n", encoding="utf-8"
    )
    (data / "fixtures" / "enterprises.csv").write_text(
        "enterprise_id\n", encoding="utf-8"
    )
    reference = data / "policies" / "reference_sources.csv"
    if mapping:
        reference.write_text(
            "document,source_url\n附件〔2020〕1号,https://www.chinatax.gov.cn/cs149.html\n",
            encoding="utf-8",
        )
    monkeypatch.setattr(importer, "DATA", data)
    monkeypatch.setattr(importer, "SOURCES", sources)
    monkeypatch.setattr(importer, "REFERENCE_SOURCES", reference)
    return data


def test_清单拒绝缺少声明的出处正文(importer: ModuleType, monkeypatch, tmp_path) -> None:
    """删掉附件后清单不能缩短了事：--manifest 必须非零，不能当作完整签认证据。"""

    _mini_import_tree(importer, monkeypatch, tmp_path, attachment=False)

    with pytest.raises(SystemExit, match="缺少出处正文"):
        importer.main(["--manifest"])


def test_清单拒绝缺少必需映射(importer: ModuleType, monkeypatch, tmp_path) -> None:
    _mini_import_tree(importer, monkeypatch, tmp_path, mapping=False)

    with pytest.raises(SystemExit, match="reference_sources.csv"):
        importer.input_manifest()


def test_清单拒绝缺少政策正文(importer: ModuleType, monkeypatch, tmp_path) -> None:
    _mini_import_tree(importer, monkeypatch, tmp_path, body=False)

    with pytest.raises(SystemExit, match="没有正文"):
        importer.input_manifest()


def test_清单收录必需的正文出处与映射(importer: ModuleType, monkeypatch, tmp_path) -> None:
    _mini_import_tree(importer, monkeypatch, tmp_path)

    manifest = importer.input_manifest()

    assert any(key.endswith("data/policies/sources/P1.txt") for key in manifest)
    assert any(key.endswith("data/policies/sources/附件〔2020〕1号.txt") for key in manifest)
    assert any(key.endswith("data/policies/reference_sources.csv") for key in manifest)


def test_跨文件引用的出处被作为附带来源导入(importer: ModuleType, monkeypatch, tmp_path) -> None:
    """出处必须带**它自己**的官方链接；没有链接就不写这一份，也不能借用主政策链接。"""

    url = _with_reference(importer, monkeypatch, tmp_path)
    plans, problems = importer.build_plans()
    by_document = {plan.document: plan for plan in plans}
    plan = by_document["深科技创新规〔2021〕5号"]

    # HT-007 引自《财税〔2015〕119号》，不在深科技创新规〔2021〕5号的正文里。
    assert problems == []
    assert [(source.name, source.url) for source in plan.attachments] == [
        ("财税〔2015〕119号", url)
    ]
    assert [crit.problem for crit in plan.criteria if crit.code == "HT-007"] == [None]

    ht007 = next(crit for crit in plan.criteria if crit.code == "HT-007")
    spans = importer._spans_for(plan, ht007)
    assert {str(span.source_url) for span in spans} == {url}


def test_只在_source_version_里出现的出处不导入附件(
    importer: ModuleType, monkeypatch, tmp_path
) -> None:
    """RD-001 的 ``source_version`` 写的是 119号（旧口径），``source_document`` 为空。

    那一列表达「归属哪个版本」，不是「这段引文出自哪份文件」：引文按本版正文校验，
    不能凭空多导入一份没人引用的附件。
    """

    _with_reference(importer, monkeypatch, tmp_path)
    plans, _ = importer.build_plans()
    plan = {item.document: item for item in plans}["财政部 税务总局公告2023年第7号"]

    assert plan.attachments == []
    assert next(crit for crit in plan.criteria if crit.code == "RD-001").problem is None


def test_出处没有官方链接时拦截引用它的条件而不是借用主政策链接(
    importer: ModuleType, monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(importer, "REFERENCE_SOURCES", tmp_path / "不存在的映射.csv")

    plans, _ = importer.build_plans()
    plan = {item.document: item for item in plans}["深科技创新规〔2021〕5号"]

    assert plan.attachments == [], "链接核实不了的附件不该写进库"
    blocked = {crit.code: crit.problem for crit in plan.criteria if crit.problem}
    assert set(blocked) == {"HT-007"}
    assert "待补" in blocked["HT-007"]
    assert any("财税〔2015〕119号" in note and "待补" in note for note in plan.notes)


def test_附件引文的_quote_sourceId_sourceUrl_与来源行一一对应(
    importer: ModuleType, monkeypatch, tmp_path
) -> None:
    """M2 要的「quote、sourceId、sourceUrl 与原文一一对应」落在校验层的证据：

    由 ``_draft_for`` 造出的草稿里，每条 span 的链接必须等于它 ``sourceId`` 指向的
    那一行来源；引文必须逐字出现在那一行的正文里。
    """

    url = _with_reference(importer, monkeypatch, tmp_path)
    plans, _ = importer.build_plans()
    plan = {item.document: item for item in plans}["深科技创新规〔2021〕5号"]
    by_id = {str(source.id): source for source in importer._draft_for(plan).sources}

    ht007 = next(crit for crit in plan.criteria if crit.code == "HT-007")
    spans = importer._spans_for(plan, ht007)

    assert spans, "HT-007 应当有引文"
    for span in spans:
        row = by_id[str(span.source_id)]
        assert str(span.source_url) == row.source_url == url
        assert row.kind == "attachment"
        assert span.quote in row.text


def test_引文落在待补出处上时该条件被判_blocked(importer: ModuleType) -> None:
    """判定发生在拼 span 之前：不为待补的出处生成任何 span，也就不会带走主政策链接。"""

    crit = {
        "criterion_id": "X-001",
        "adjudicated_expression": '{"op": "manual_review"}',
        "source_span": "某句只在别的文件里出现的话",
        "source_document": "财税〔2015〕119号",
        "source_page_or_section": "第一条",
    }
    sources = [
        ("正文", "正文里没有这句话", "https://www.gov.cn/a"),
        ("财税〔2015〕119号", "某句只在别的文件里出现的话", None),
    ]

    plan = importer._criterion_plan(crit, sources)

    assert plan.spans == []
    assert plan.problem is not None and "待补" in plan.problem


def test_同一句同时出现在正文与附件时按_source_document_绑定(importer: ModuleType) -> None:
    """M2 在 #51 指出的：不能按「哪份文本先包含这句」猜出处。

    正文在 ``sources`` 里排第一，旧写法对同句必然绑到正文并给出主政策链接。
    """

    crit = {
        "criterion_id": "X-002",
        "adjudicated_expression": '{"op": "manual_review"}',
        "source_span": "两边都有的一句话",
        "source_document": "财税〔2015〕119号",
        "source_page_or_section": "第一条",
    }
    sources = [
        ("正文", "正文里也有：两边都有的一句话", "https://www.gov.cn/a"),
        ("财税〔2015〕119号", "附件里写着两边都有的一句话", "https://www.chinatax.gov.cn/b"),
    ]

    plan = importer._criterion_plan(crit, sources)

    assert plan.problem is None
    assert [span.source_name for span in plan.spans] == ["财税〔2015〕119号"]


def test_声明的出处不含该句时不跨来源搜索(importer: ModuleType) -> None:
    crit = {
        "criterion_id": "X-003",
        "adjudicated_expression": '{"op": "manual_review"}',
        "source_span": "这句话只在本版正文里",
        "source_document": "财税〔2015〕119号",
        "source_page_or_section": "第一条",
    }
    sources = [
        ("正文", "这句话只在本版正文里", "https://www.gov.cn/a"),
        ("财税〔2015〕119号", "附件里没有那句话", "https://www.chinatax.gov.cn/b"),
    ]

    plan = importer._criterion_plan(crit, sources)

    assert plan.spans == []
    assert plan.problem is not None and "财税〔2015〕119号" in plan.problem


def test_声明的出处没有独立正文时拒绝而不是退回正文(importer: ModuleType) -> None:
    crit = {
        "criterion_id": "X-004",
        "adjudicated_expression": '{"op": "manual_review"}',
        "source_span": "申报通知里的一句话",
        "source_document": "2025年专精特新申报通知",
        "source_page_or_section": "第一条",
    }
    sources = [("正文", "正文里没有这句话", "https://www.gov.cn/a")]

    plan = importer._criterion_plan(crit, sources)

    assert plan.spans == []
    assert plan.problem is not None and "没有独立正文" in plan.problem


def test_source_document_段数与引文段数不一致就拒绝(importer: ModuleType) -> None:
    crit = {
        "criterion_id": "X-005",
        "adjudicated_expression": '{"op": "manual_review"}',
        "source_span": "第一句|第二句",
        "source_document": "财税〔2015〕119号",
        "source_page_or_section": "第一条",
    }
    sources = [
        ("正文", "第一句 第二句", "https://www.gov.cn/a"),
        ("财税〔2015〕119号", "第一句 第二句", "https://www.chinatax.gov.cn/b"),
    ]

    plan = importer._criterion_plan(crit, sources)

    assert plan.spans == []
    assert plan.problem is not None and "无法逐段对应" in plan.problem


def test_整列留空时只在本版正文里找(importer: ModuleType) -> None:
    """``source_document`` 为空 → 只认正文；附件里有也不算找到。"""

    crit = {
        "criterion_id": "X-006",
        "adjudicated_expression": '{"op": "manual_review"}',
        "source_span": "只在附件里的一句话",
        "source_page_or_section": "第一条",
    }
    sources = [
        ("正文", "正文里没有这句话", "https://www.gov.cn/a"),
        ("财税〔2015〕119号", "只在附件里的一句话", "https://www.chinatax.gov.cn/b"),
    ]

    plan = importer._criterion_plan(crit, sources)

    assert plan.spans == []
    assert plan.problem is not None and "正文" in plan.problem


def test_出处写政策自己的文号时绑定正文(importer: ModuleType) -> None:
    crit = {
        "criterion_id": "X-007",
        "adjudicated_expression": '{"op": "manual_review"}',
        "source_span": "本版正文里的句子",
        "source_document": "国科发火〔2008〕172号",
        "source_page_or_section": "第一条",
    }
    sources = [("国科发火〔2008〕172号", "本版正文里的句子", "https://www.gov.cn/a")]

    plan = importer._criterion_plan(
        crit, sources, document="国科发火〔2008〕172号"
    )

    assert plan.problem is None
    assert [span.source_name for span in plan.spans] == ["国科发火〔2008〕172号"]


def test_六个企业与全部事实都能按产品规则规范化(importer: ModuleType) -> None:
    payloads = importer._enterprise_payloads()

    assert [code for code, _ in payloads] == [f"E-ENT-{i:03d}" for i in range(1, 7)]
    assert sum(len(payload["facts"]) for _, payload in payloads) == 187
    assert importer.check_enterprises() == []


def test_预检未通过时_apply_不会连接数据库(importer: ModuleType, monkeypatch) -> None:
    """预检失败必须在任何写入之前被拦下：--apply 不能绕过校验把其余计划发出去。"""

    calls: list[list[object]] = []
    monkeypatch.setattr(importer, "_apply", lambda plans, dsn: calls.append(plans) or 0)
    # 让每个政策版本都至少有一条条件校验不通过。
    monkeypatch.setattr(
        importer,
        "_validate",
        lambda plan, draft: ({crit.code: "failed" for crit in plan.criteria}, {}),
    )

    exit_code = importer.main(["--apply", "--dsn", "postgresql://unused/none"])

    assert exit_code == 1
    assert calls == [], "预检未通过却调用了 _apply"


def test_预检未通过时干跑同样返回非零(importer: ModuleType, monkeypatch) -> None:
    monkeypatch.setattr(
        importer,
        "_validate",
        lambda plan, draft: ({crit.code: "failed" for crit in plan.criteria}, {}),
    )

    assert importer.main([]) == 1


def test_布尔事实的空值保持未知而不是_false(importer: ModuleType) -> None:
    """空值是「未知」：转成 false 等于把「没填」记成「不符合」。"""

    def boolean(value: str) -> object:
        return importer._fact_value(
            {"unit": "boolean", "fact_value": value}, "E-ENT-001/highTech", None
        )

    assert boolean("") is None
    assert boolean("  ") is None
    assert boolean("true") is True
    assert boolean("TRUE") is True
    assert boolean("false") is False


def test_布尔事实的非法写法被记进问题而不是悄悄当成_false(importer: ModuleType) -> None:
    problems: list[str] = []

    value = importer._fact_value(
        {"unit": "boolean", "fact_value": "yes"}, "E-ENT-001/highTech", problems
    )

    assert value is None
    assert problems == ["E-ENT-001/highTech：布尔事实只能写 true/false 或留空，实际是 'yes'"]


def test_写库路径不传问题列表时非法布尔直接抛错(importer: ModuleType) -> None:
    with pytest.raises(ValueError, match="布尔事实"):
        importer._fact_value(
            {"unit": "boolean", "fact_value": "1"}, "E-ENT-001/highTech", None
        )


# --------------------------------------------------------------- 重跑的状态机
#
# 写库分三步三个事务，中断点因此只有三个：建版后（pending_parse）、存条款后
# （pending_review）、发布后（published）。下面按这三种状态各钉一条。


_URL = "https://www.gov.cn/x"


def _plan(*codes: str, text: str = "正文内容", expression: dict | None = None):
    return SimpleNamespace(
        code="P",
        document="测试文号",
        metadata=SimpleNamespace(document_number="测试文号", version="v1", source_url=_URL),
        body_name="正文",
        body_text=text,
        attachments=[],
        notes=[],
        criteria=[
            SimpleNamespace(
                code=code,
                problem=None,
                expression={"op": "manual_review"} if expression is None else expression,
                review_finding="none",
                spans=[SimpleNamespace(source_name="正文", page=None, section="一", quote=text)],
            )
            for code in codes
        ],
    )


def _sig(importer: ModuleType, *codes: str, **kwargs):
    return importer.PlanSignature.of(_plan(*codes, **kwargs))


def _passed(signature):
    """库里那版条款都通过了校验时的摘要。"""

    return tuple(replace(item, status="passed") for item in signature.criteria)


def _state(
    importer: ModuleType,
    *,
    status: str,
    signature,
    criteria=None,
    hashes=None,
    urls=None,
):
    return importer.VersionState(
        policy_id=uuid4(),
        version="v1",
        review_status=status,
        revision=uuid4(),
        criteria=signature.criteria if criteria is None else tuple(criteria),
        source_hashes=signature.source_hashes if hashes is None else hashes,
        source_urls=signature.source_urls if urls is None else urls,
    )


def test_库里没有这个文号就新建(importer: ModuleType) -> None:
    assert importer._decide([], _sig(importer, "A"))[0] == "create"


def test_已完整发布的版本重跑直接跳过(importer: ModuleType) -> None:
    signature = _sig(importer, "A")
    state = _state(importer, status="published", signature=signature, criteria=_passed(signature))

    action, reason = importer._decide([state], signature)

    assert action == "skip"
    assert "全部 passed" in reason


def test_已发布但缺条件算冲突而不是跳过(importer: ModuleType) -> None:
    """旧写法只按文号 SELECT 1 就跳过，缺了条件也照样算成功——这正是 M2 指出的问题。"""

    signature = _sig(importer, "A", "B")
    state = _state(
        importer, status="published", signature=signature, criteria=_passed(signature)[:1]
    )

    action, reason = importer._decide([state], signature)

    assert action == "conflict"
    assert "缺条件 B" in reason


def test_已发布但多出一条条款算冲突(importer: ModuleType) -> None:
    signature = _sig(importer, "A")
    extra = importer.CriterionState(
        code="B", status="passed", expression="{}", review_finding="none", spans=()
    )
    state = _state(
        importer, status="published", signature=signature, criteria=_passed(signature) + (extra,)
    )

    action, reason = importer._decide([state], signature)

    assert action == "conflict"
    assert "多出条件 B" in reason


def test_重复条款算冲突(importer: ModuleType) -> None:
    signature = _sig(importer, "A")
    state = _state(
        importer,
        status="published",
        signature=signature,
        criteria=_passed(signature) * 2,
    )

    action, reason = importer._decide([state], signature)

    assert action == "conflict"
    assert "重复条款" in reason


def test_已发布但条款没通过算冲突(importer: ModuleType) -> None:
    signature = _sig(importer, "A")
    failed = tuple(replace(item, status="failed") for item in signature.criteria)
    state = _state(importer, status="published", signature=signature, criteria=failed)

    action, reason = importer._decide([state], signature)

    assert action == "conflict"
    assert "不是 passed" in reason


def test_已发布但判定表达式被改过算冲突(importer: ModuleType) -> None:
    signature = _sig(importer, "A")
    edited = (replace(_passed(signature)[0], expression='{"op":"manual_review","extra":1}'),)
    state = _state(importer, status="published", signature=signature, criteria=edited)

    action, reason = importer._decide([state], signature)

    assert action == "conflict"
    assert "判定表达式" in reason


def test_已发布但引文被改过算冲突(importer: ModuleType) -> None:
    signature = _sig(importer, "A")
    edited = (replace(_passed(signature)[0], spans=(("正文", "别的引文", None, "一", _URL),)),)
    state = _state(importer, status="published", signature=signature, criteria=edited)

    action, reason = importer._decide([state], signature)

    assert action == "conflict"
    assert "引文或出处" in reason


def test_已发布但引文指向别的来源算冲突(importer: ModuleType) -> None:
    """来源表本身没错，但 span 的 sourceId 指到了另一份文件。"""

    signature = _sig(importer, "A")
    edited = (replace(_passed(signature)[0], spans=(("附件", "正文内容", None, "一", _URL),)),)
    state = _state(importer, status="published", signature=signature, criteria=edited)

    action, reason = importer._decide([state], signature)

    assert action == "conflict"
    assert "引文或出处" in reason


def test_已发布但正文变过算冲突(importer: ModuleType) -> None:
    signature = _sig(importer, "A")
    state = _state(
        importer,
        status="published",
        signature=signature,
        criteria=_passed(signature),
        hashes=frozenset({"别的正文的哈希"}),
    )

    action, reason = importer._decide([state], signature)

    assert action == "conflict"
    assert "来源与本轮计划不一致" in reason


def test_已发布但附件还指着主政策链接算冲突(importer: ModuleType) -> None:
    """旧导入器写进去的附件链接与正文相同，内容哈希却一样——只比哈希会误判成已导好。"""

    signature = _sig(importer, "A")
    state = _state(
        importer,
        status="published",
        signature=signature,
        criteria=_passed(signature),
        urls=frozenset({("正文", _URL), ("附件", _URL)}),
    )

    action, reason = importer._decide([state], signature)

    assert action == "conflict"
    assert "来源链接" in reason


def test_建版后中断的草稿走续跑(importer: ModuleType) -> None:
    signature = _sig(importer, "A")
    state = _state(importer, status="pending_parse", signature=signature, criteria=())

    assert importer._decide([state], signature)[0] == "resume"


def test_存条款后中断的草稿也走续跑(importer: ModuleType) -> None:
    """校验状态是算出来的：草稿里即使记着 failed 也照样续跑，下一轮整版覆盖。"""

    signature = _sig(importer, "A")
    failed = tuple(replace(item, status="failed") for item in signature.criteria)
    state = _state(importer, status="pending_review", signature=signature, criteria=failed)

    assert importer._decide([state], signature)[0] == "resume"


def test_草稿里的条款被改过就拒绝续跑(importer: ModuleType) -> None:
    signature = _sig(importer, "A")
    edited = (replace(signature.criteria[0], spans=(("正文", "别的引文", None, "一", _URL),)),)
    state = _state(importer, status="pending_review", signature=signature, criteria=edited)

    action, reason = importer._decide([state], signature)

    assert action == "conflict"
    assert "草稿的条款" in reason


def test_pending_parse_却有条件记录算冲突(importer: ModuleType) -> None:
    signature = _sig(importer, "A")
    state = _state(
        importer, status="pending_parse", signature=signature, criteria=_passed(signature)
    )

    action, reason = importer._decide([state], signature)

    assert action == "conflict"
    assert "库被改过" in reason


def test_同一个文号有多个版本时不去猜该续跑哪一个(importer: ModuleType) -> None:
    signature = _sig(importer, "A")
    states = [
        _state(importer, status="pending_parse", signature=signature, criteria=()),
        _state(importer, status="published", signature=signature, criteria=_passed(signature)),
    ]

    action, reason = importer._decide(states, signature)

    assert action == "conflict"
    assert "无法判断" in reason


class _FakeRepository:
    """只记调用顺序：这里要证明的是「走了哪条路」，不是「写了什么」。"""

    def __init__(self, content_hashes: list[str]) -> None:
        self.calls: list[str] = []
        self.draft = SimpleNamespace(
            id=uuid4(),
            version="v1",
            revision=uuid4(),
            sources=[
                SimpleNamespace(id=uuid4(), content_hash=digest) for digest in content_hashes
            ],
        )

    def create_policy_version(self, **kwargs):
        self.calls.append("create")
        return self.draft

    def get_version_for_edit(self, policy_id, version):
        self.calls.append("get_version_for_edit")
        return self.draft

    def save_criteria(self, **kwargs):
        self.calls.append("save_criteria")
        return SimpleNamespace(revision=uuid4())

    def publish(self, **kwargs):
        self.calls.append("publish")


def _fake_plan(text: str = "正文内容"):
    return _plan("A", text=text)


def _wire_apply(importer: ModuleType, monkeypatch, state, text: str = "正文内容"):
    digest = hashlib.sha256(text.encode()).hexdigest()
    monkeypatch.setattr(importer, "_version_state", lambda dsn, document: [state])
    monkeypatch.setattr(importer, "_apply_enterprises", lambda repository, dsn: None)
    monkeypatch.setattr(
        importer,
        "_collect_validation",
        lambda draft, criteria: (
            [SimpleNamespace(title=item.title, validation_status="passed") for item in criteria],
            [],
        ),
    )
    return _FakeRepository([digest])


def test_中断后的草稿会被续跑并发布(importer: ModuleType, monkeypatch) -> None:
    plan = _fake_plan()
    signature = importer.PlanSignature.of(plan)
    state = _state(importer, status="pending_parse", signature=signature, criteria=())
    repo = _wire_apply(importer, monkeypatch, state)

    assert importer._apply([plan], "postgresql://unused/none", repository=repo) == 0
    assert "create" not in repo.calls, "草稿已存在却又建了一个新版本"
    assert repo.calls == ["get_version_for_edit", "save_criteria", "publish"]


def test_已完整发布的版本重跑一次都不写(importer: ModuleType, monkeypatch) -> None:
    plan = _fake_plan()
    signature = importer.PlanSignature.of(plan)
    state = _state(importer, status="published", signature=signature, criteria=_passed(signature))
    repo = _wire_apply(importer, monkeypatch, state)

    assert importer._apply([plan], "postgresql://unused/none", repository=repo) == 0
    assert repo.calls == []


def test_对不上的版本重跑返回非零且不写(importer: ModuleType, monkeypatch) -> None:
    plan = _fake_plan()
    signature = importer.PlanSignature.of(plan)
    state = _state(
        importer,
        status="published",
        signature=signature,
        criteria=_passed(signature),
        hashes=frozenset({"另一份正文的哈希"}),
    )
    repo = _wire_apply(importer, monkeypatch, state)

    assert importer._apply([plan], "postgresql://unused/none", repository=repo) == 1
    assert repo.calls == []
