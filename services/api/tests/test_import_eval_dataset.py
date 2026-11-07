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
        "source_page_or_section": "第一条",
    }
    sources = [
        ("正文", "正文里没有这句话", "https://www.gov.cn/a"),
        ("财税〔2015〕119号", "某句只在别的文件里出现的话", None),
    ]

    plan = importer._criterion_plan(crit, sources)

    assert plan.spans == []
    assert plan.problem is not None and "待补" in plan.problem


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


def _plan(*codes: str):
    return SimpleNamespace(
        document="测试文号", criteria=[SimpleNamespace(code=code) for code in codes]
    )


#: 计划与库里都一致的一组来源链接。
_URLS = frozenset({("正文", "https://www.gov.cn/x")})


def _state(
    importer: ModuleType,
    *,
    status: str,
    criteria=(),
    hashes=frozenset({"h"}),
    urls=_URLS,
):
    return importer.VersionState(
        policy_id=uuid4(),
        version="v1",
        review_status=status,
        revision=uuid4(),
        criteria=tuple(criteria),
        source_hashes=hashes,
        source_urls=urls,
    )


def test_库里没有这个文号就新建(importer: ModuleType) -> None:
    assert importer._decide([], _plan("A"), frozenset({"h"}), _URLS)[0] == "create"


def test_已完整发布的版本重跑直接跳过(importer: ModuleType) -> None:
    state = _state(importer, status="published", criteria=[("A", "passed")])

    action, reason = importer._decide([state], _plan("A"), frozenset({"h"}), _URLS)

    assert action == "skip"
    assert "全部 passed" in reason


def test_已发布但缺条件算冲突而不是跳过(importer: ModuleType) -> None:
    """旧写法只按文号 SELECT 1 就跳过，缺了条件也照样算成功——这正是 M2 指出的问题。"""

    state = _state(importer, status="published", criteria=[("A", "passed")])

    action, reason = importer._decide([state], _plan("A", "B"), frozenset({"h"}), _URLS)

    assert action == "conflict"
    assert "缺条件 B" in reason


def test_已发布但条件没通过算冲突(importer: ModuleType) -> None:
    state = _state(importer, status="published", criteria=[("A", "failed")])

    action, reason = importer._decide([state], _plan("A"), frozenset({"h"}), _URLS)

    assert action == "conflict"
    assert "不是 passed" in reason


def test_已发布但正文变过算冲突(importer: ModuleType) -> None:
    state = _state(importer, status="published", criteria=[("A", "passed")])

    action, reason = importer._decide([state], _plan("A"), frozenset({"别的正文"}), _URLS)

    assert action == "conflict"
    assert "来源与本轮计划不一致" in reason


def test_已发布但附件还指着主政策链接算冲突(importer: ModuleType) -> None:
    """旧导入器写进去的附件链接与正文相同，内容哈希却一样——只比哈希会误判成已导好。"""

    state = _state(
        importer,
        status="published",
        criteria=[("A", "passed")],
        urls=frozenset({("正文", "https://www.gov.cn/x"), ("附件", "https://www.gov.cn/x")}),
    )
    planned = frozenset(
        {("正文", "https://www.gov.cn/x"), ("附件", "https://www.chinatax.gov.cn/y")}
    )

    action, reason = importer._decide([state], _plan("A"), frozenset({"h"}), planned)

    assert action == "conflict"
    assert "来源链接" in reason


def test_建版后中断的草稿走续跑(importer: ModuleType) -> None:
    state = _state(importer, status="pending_parse")

    assert importer._decide([state], _plan("A"), frozenset({"h"}), _URLS)[0] == "resume"


def test_存条款后中断的草稿也走续跑(importer: ModuleType) -> None:
    state = _state(importer, status="pending_review", criteria=[("A", "failed")])

    assert importer._decide([state], _plan("A"), frozenset({"h"}), _URLS)[0] == "resume"


def test_pending_parse_却有条件记录算冲突(importer: ModuleType) -> None:
    state = _state(importer, status="pending_parse", criteria=[("A", "passed")])

    action, reason = importer._decide([state], _plan("A"), frozenset({"h"}), _URLS)

    assert action == "conflict"
    assert "库被改过" in reason


def test_同一个文号有多个版本时不去猜该续跑哪一个(importer: ModuleType) -> None:
    states = [
        _state(importer, status="pending_parse"),
        _state(importer, status="published", criteria=[("A", "passed")]),
    ]

    action, reason = importer._decide(states, _plan("A"), frozenset({"h"}), _URLS)

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
    return SimpleNamespace(
        code="P",
        document="测试文号",
        metadata=SimpleNamespace(
            document_number="测试文号", version="v1", source_url="https://www.gov.cn/x"
        ),
        body_name="正文",
        body_text=text,
        attachments=[],
        notes=[],
        criteria=[
            SimpleNamespace(
                code="A",
                problem=None,
                expression={"op": "manual_review"},
                review_finding="none",
                spans=[
                    SimpleNamespace(source_name="正文", page=None, section="一", quote=text)
                ],
            )
        ],
    )


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
    return _FakeRepository([digest]), _fake_plan(text)


def test_中断后的草稿会被续跑并发布(importer: ModuleType, monkeypatch) -> None:
    digest = hashlib.sha256("正文内容".encode()).hexdigest()
    state = _state(importer, status="pending_parse", hashes=frozenset({digest}))
    repo, plan = _wire_apply(importer, monkeypatch, state)

    assert importer._apply([plan], "postgresql://unused/none", repository=repo) == 0
    assert "create" not in repo.calls, "草稿已存在却又建了一个新版本"
    assert repo.calls == ["get_version_for_edit", "save_criteria", "publish"]


def test_已完整发布的版本重跑一次都不写(importer: ModuleType, monkeypatch) -> None:
    state = importer.VersionState(
        policy_id=uuid4(),
        version="v1",
        review_status="published",
        revision=uuid4(),
        criteria=(("A", "passed"),),
        source_hashes=frozenset({hashlib.sha256("正文内容".encode()).hexdigest()}),
        source_urls=frozenset({("正文", "https://www.gov.cn/x")}),
    )
    repo, plan = _wire_apply(importer, monkeypatch, state)

    assert importer._apply([plan], "postgresql://unused/none", repository=repo) == 0
    assert repo.calls == []


def test_对不上的版本重跑返回非零且不写(importer: ModuleType, monkeypatch) -> None:
    state = importer.VersionState(
        policy_id=uuid4(),
        version="v1",
        review_status="published",
        revision=uuid4(),
        criteria=(("A", "passed"),),
        source_hashes=frozenset({"另一份正文的哈希"}),
        source_urls=frozenset({("正文", "https://www.gov.cn/x")}),
    )
    repo, plan = _wire_apply(importer, monkeypatch, state)

    assert importer._apply([plan], "postgresql://unused/none", repository=repo) == 1
    assert repo.calls == []
