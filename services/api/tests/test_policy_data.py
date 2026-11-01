"""M3 政策标注数据的闸门：把 ``data/policies/criteria_review.csv`` 直接跑一遍校验。

这份 CSV 是 M3 的交付物，不是 M1 的测试夹具；放在这里是为了让「标的条款能不能真的
导入并发布」在 CI 里可见，而不是等人工导入才发现。检查两类东西：

1. 表达式本身：``validate_expression`` 的结构与语义校验；
2. 表达式与引文是否自洽：``validate_criterion`` 的引文层校验（阈值、方向、日期、
   行业排除、逐字存在性）。

**任何未通过的条款都是硬失败**，除非它的 ``criterion_id`` 明确列在 ``KNOWN_FAILURES``
里——只有那样才 ``xfail``，并在报告里注明原因。早先对所有失败一律 ``xfail``，等于把
「标的条款过不了校验」写成绿色：数据坏了 CI 照样绿，发布时才会在 ``admin_policies``
的 422 上炸。已知未通过必须逐条署名，修好后从表里删掉，表应当一直为空。

逐字存在性检查需要来源正文。M3 若在 ``data/policies/sources/<policy_id>.txt`` 放了
正文快照，这里就用它做真正的逐字比对；没有快照时退化为用引文片段自身充当来源文本，
也就是说那一项检查此时不做（而不是假装通过）。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from urllib.parse import quote
from uuid import UUID, uuid4

import pytest

from app.domain.citations import (
    CriterionInput,
    SourceSpanCheck,
    SourceText,
    quote_hash,
    validate_criterion,
)
from app.domain.rules import validate_expression

ROOT = Path(__file__).resolve().parents[3]
CSV_PATH = ROOT / "data/policies/criteria_review.csv"
SOURCES_DIR = ROOT / "data/policies/sources"

#: 已知未通过、暂时不挡 CI 的条款：``criterion_id`` → 为什么现在修不了、谁去修。
#: 空表是常态。往里加东西等于承认「这一条现在发不出去」，要写清原因。
KNOWN_FAILURES: dict[str, str] = {}


def _rows() -> list[dict[str, str]]:
    """已仲裁的行。

    冲突条目也要进闸门：它们可能没有表达式（``adjudicated_expression`` 为空，靠
    ``review_finding=conflict`` 表达结论），以前只按表达式非空筛选，等于把冲突条目
    整条跳过——数据坏了 CI 照样绿，正是这个文件想避免的那类「静默放行」。
    """
    if not CSV_PATH.exists():
        return []
    text = CSV_PATH.read_text(encoding="utf-8-sig")
    return [
        row
        for row in csv.DictReader(text.splitlines())
        if (row.get("adjudicated_expression") or "").strip()
        or (row.get("review_finding") or "").strip() == "conflict"
    ]


#: 契约里 ``reviewFinding`` 只有这两个取值；写错时按「标注没做完」处理，不当成 none。
REVIEW_FINDINGS = frozenset({"none", "conflict"})


def _fragments(row: dict[str, str]) -> list[str]:
    """``source_span`` 用 ``|`` 分隔多段连续原文；没有 ``|`` 就是单段。"""
    raw = (row.get("source_span") or "").strip()
    return [part.strip() for part in raw.split("|") if part.strip()]


def _aligned(row: dict[str, str], column: str, count: int) -> tuple[list[str] | None, bool]:
    """把 ``|`` 分隔的列按段对齐到 ``count`` 段引文；留空表示整列未填。"""
    raw = (row.get(column) or "").strip()
    if not raw:
        return [""] * count, True
    parts = [part.strip() for part in raw.split("|")]
    if len(parts) != count:
        return None, False
    return parts, True


def _documents(row: dict[str, str], fragments: list[str]) -> tuple[list[str], list[str]]:
    """每段引文出自哪份文档；返回 ``(文档名列表, 问题代码列表)``。

    ``source_document`` 与 ``source_span`` 按 ``|`` 逐段对齐，留空表示该段出自政策本体。
    一条条款的两处引文可以来自不同文件（条款冲突本来就常出在两份规范性文件之间），
    以前一律按政策本体的正文去查，第二份文件的引文必然报 ``SPAN_QUOTE_NOT_FOUND``。
    """
    parts, ok = _aligned(row, "source_document", len(fragments))
    if not ok:
        return [], ["SOURCE_DOCUMENT_MISALIGNED"]
    return [part or row["policy_id"] for part in parts or []], []


def _source_for(name: str, fragments: list[str], sources_dir: Path) -> tuple[SourceText, bool]:
    """返回来源文本与「是否拿到真实正文」。

    没有 ``sources/<name>.txt`` 时退化为用引文片段自身充当来源文本，并在返回值里如实
    标出没拿到正文，好让失败信息写明「逐字检查未做」，而不是假装查过。
    """
    snapshot = sources_dir / f"{name}.txt"
    if snapshot.exists():
        return (
            SourceText(
                id=uuid4(),
                source_url=f"https://example.invalid/{quote(name, safe='')}",
                text=snapshot.read_text(encoding="utf-8-sig"),
            ),
            True,
        )
    return SourceText(id=uuid4(), source_url="https://example.invalid/fragments", text="".join(fragments)), False


def _problems_for(row: dict[str, str], sources_dir: Path | None = None) -> tuple[list[str], bool]:
    """这条数据过不了闸门的原因（空列表表示能过），以及是否拿到了正文快照。

    失败原因用代码表示（``CITATION_THRESHOLD_MISMATCH`` 之类），这样回归报告里能直接
    看出坏在哪一项，而不只是「某条断言失败」。``sources_dir`` 只给闸门自身的用例换
    一个来源目录用，正式跑一律读 ``data/policies/sources``。
    """
    sources_dir = sources_dir or SOURCES_DIR
    review_finding = (row.get("review_finding") or "").strip() or "none"
    raw_expression = (row.get("adjudicated_expression") or "").strip()
    expression = json.loads(raw_expression) if raw_expression else None

    problems: list[str] = []
    if review_finding not in REVIEW_FINDINGS:
        problems.append("REVIEW_FINDING_INVALID")
    if expression is not None:
        problems.extend(problem.code for problem in validate_expression(expression))

    fragments = _fragments(row)
    if not fragments:
        problems.append("SOURCE_SPAN_MISSING")
        return sorted(set(problems)), False

    documents, alignment = _documents(row, fragments)
    if alignment:
        problems.extend(alignment)
        return sorted(set(problems)), False

    # 章节通常只有一段、适用于本条所有引文；能按 ``|`` 逐段对齐时才逐段用。
    sections, _ = _aligned(row, "source_page_or_section", len(fragments))
    whole_section = (row.get("source_page_or_section") or "").strip() or None
    version = row["source_version"]
    sources: dict[UUID, SourceText] = {}
    spans = []
    has_snapshot = True
    for index, (fragment, document) in enumerate(zip(fragments, documents)):
        source, snapshot_available = _source_for(document, fragments, sources_dir)
        has_snapshot = has_snapshot and snapshot_available
        sources[source.id] = source
        spans.append(
            SourceSpanCheck(
                source_id=source.id,
                source_url=source.source_url,
                source_version=version,
                page=None,
                section=(sections[index].strip() or None) if sections else whole_section,
                quote=fragment,
                text_hash=quote_hash(fragment),
            )
        )

    verdict = validate_criterion(
        CriterionInput(
            id=row["criterion_id"],
            title=row["criterion_id"],
            review_finding=review_finding,
            spans=tuple(spans),
            expression=expression,
        ),
        sources,
        version=version,
    )
    if not verdict.passed:
        problems.extend(sorted({finding.code for finding in verdict.findings}))
    return sorted(set(problems)), has_snapshot


ROWS = _rows()
PARAMS = [pytest.param(row, id=row["criterion_id"]) for row in ROWS] or [
    pytest.param(
        None,
        marks=pytest.mark.skip(reason="criteria_review.csv 还没有已仲裁表达式（#9 合并后生效）"),
    )
]


@pytest.mark.parametrize("row", PARAMS)
def test_policy_data_passes_citation_checks(row):
    assert row is not None
    criterion_id = row["criterion_id"]
    problems, has_snapshot = _problems_for(row)
    # 没有正文快照时逐字检查是跳过的，在失败信息里注明，别让它读起来像已经查过。
    note = "" if has_snapshot else "（无正文快照，逐字检查未做）"

    if not problems:
        assert criterion_id not in KNOWN_FAILURES, (
            f"{criterion_id} 已在 KNOWN_FAILURES 里登记为已知未通过，但现在能过闸门——"
            f"请把它从表里删掉，别让陈旧的豁免掩盖下一次回归。"
        )
        return

    if criterion_id in KNOWN_FAILURES:
        pytest.xfail(f"{criterion_id} 已知未通过：{problems}；原因：{KNOWN_FAILURES[criterion_id]}")
    pytest.fail(f"{criterion_id} 未通过闸门：{problems}{note}")


def _conflict_row(spans: list[str], documents: list[str], *, expression: str = "") -> dict[str, str]:
    return {
        "criterion_id": "SELFTEST-CONFLICT",
        "policy_id": "selftest-policy",
        "source_version": "1.0.0",
        "source_page_or_section": "第一条|第二条",
        "source_span": "|".join(spans),
        "source_document": "|".join(documents),
        "review_finding": "conflict",
        "adjudicated_expression": expression,
    }


@pytest.fixture
def two_documents(tmp_path):
    """两份互不相同的来源正文，用来验证「两处引文各出自一份文件」。"""
    (tmp_path / "甲文.txt").write_text("甲文：其他费用不得超过 10%。", encoding="utf-8")
    (tmp_path / "乙文.txt").write_text("乙文：其他费用不得超过 20%。", encoding="utf-8")
    return tmp_path


def test_a_conflict_row_over_two_documents_passes(two_documents):
    """冲突条目的两处引文常出在两份规范性文件之间，各按自己的正文逐字核对。"""
    row = _conflict_row(["其他费用不得超过 10%", "其他费用不得超过 20%"], ["甲文", "乙文"])
    problems, has_snapshot = _problems_for(row, sources_dir=two_documents)
    assert problems == []
    assert has_snapshot


def test_conflict_needs_two_spans_even_without_an_expression(two_documents):
    """以前 ``review_finding`` 写死成 none，单条引文的冲突条目会静静通过。"""
    row = _conflict_row(["其他费用不得超过 10%"], ["甲文"])
    problems, _ = _problems_for(row, sources_dir=two_documents)
    assert "CONFLICT_NEEDS_TWO_SPANS" in problems


def test_two_identical_quotes_are_not_conflict_evidence(two_documents):
    quote_text = "其他费用不得超过 10%"
    row = _conflict_row([quote_text, quote_text], ["甲文", "乙文"])
    problems, _ = _problems_for(row, sources_dir=two_documents)
    assert "CONFLICT_SPANS_IDENTICAL" in problems


def test_a_span_pointing_at_a_document_without_the_quote_is_reported(two_documents):
    """文档标错了（引文不在那份文件里）必须报出来，而不是退回政策本体凑合。"""
    row = _conflict_row(["其他费用不得超过 10%", "其他费用不得超过 20%"], ["甲文", "甲文"])
    problems, _ = _problems_for(row, sources_dir=two_documents)
    assert "SPAN_QUOTE_NOT_FOUND" in problems


def test_a_misaligned_source_document_column_is_reported(two_documents):
    row = _conflict_row(["其他费用不得超过 10%", "其他费用不得超过 20%"], ["甲文"])
    problems, _ = _problems_for(row, sources_dir=two_documents)
    assert "SOURCE_DOCUMENT_MISALIGNED" in problems


def test_an_unknown_review_finding_is_not_treated_as_none(two_documents):
    row = _conflict_row(["其他费用不得超过 10%"], ["甲文"])
    row["review_finding"] = "confict"
    problems, _ = _problems_for(row, sources_dir=two_documents)
    assert "REVIEW_FINDING_INVALID" in problems


def test_the_gate_reports_a_deliberately_broken_row():
    """闸门本身也要有测试：把一条坏数据喂进去，必须报出具体代码，而不是静静通过。

    没有这条，``_problems_for`` 哪天写成恒返回空列表（例如异常被吞掉），上面那批
    参数化用例会全绿——那正是这个文件原本的毛病（全 xfail 的另一种写法）。
    """

    broken = {
        "criterion_id": "SELFTEST-001",
        "policy_id": "selftest-not-a-real-policy",
        "source_version": "1.0.0",
        "source_page_or_section": "第一条",
        "source_span": "上年度营业收入总额在1000万元以上",
        "adjudicated_expression": json.dumps(
            {
                "op": "compare",
                "factKey": "revenue",
                "period": "assessment",
                "comparator": "lte",
                "value": "1000000",
                "unit": "CNY",
            },
            ensure_ascii=False,
        ),
    }

    problems, _ = _problems_for(broken)
    assert "CITATION_THRESHOLD_MISMATCH" in problems


def test_the_dataset_is_not_silently_empty():
    """CSV 存在但没有一条已仲裁表达式时，提醒一次；#9 合并前是常态。"""
    if not ROWS:
        pytest.skip("criteria_review.csv 尚未提供表达式")
    assert len(ROWS) >= 1
