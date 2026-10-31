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
from uuid import uuid4

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
    if not CSV_PATH.exists():
        return []
    text = CSV_PATH.read_text(encoding="utf-8-sig")
    return [
        row
        for row in csv.DictReader(text.splitlines())
        if (row.get("adjudicated_expression") or "").strip()
    ]


def _fragments(row: dict[str, str]) -> list[str]:
    """``source_span`` 用 ``|`` 分隔多段连续原文；没有 ``|`` 就是单段。"""
    raw = (row.get("source_span") or "").strip()
    return [part.strip() for part in raw.split("|") if part.strip()]


def _source_text(policy_id: str, fragments: list[str]) -> tuple[SourceText, bool]:
    """返回来源文本与「是否拿到真实正文」。"""
    snapshot = SOURCES_DIR / f"{policy_id}.txt"
    if snapshot.exists():
        body = snapshot.read_text(encoding="utf-8-sig")
        return SourceText(id=uuid4(), source_url="https://example.invalid/snapshot", text=body), True
    return SourceText(id=uuid4(), source_url="https://example.invalid/fragments", text="".join(fragments)), False


def _problems_for(row: dict[str, str]) -> tuple[list[str], bool]:
    """这条数据过不了闸门的原因（空列表表示能过），以及是否拿到了正文快照。

    失败原因用代码表示（``CITATION_THRESHOLD_MISMATCH`` 之类），这样回归报告里能直接
    看出坏在哪一项，而不只是「某条断言失败」。
    """
    problems = [problem.code for problem in validate_expression(json.loads(row["adjudicated_expression"]))]

    fragments = _fragments(row)
    if not fragments:
        problems.append("SOURCE_SPAN_MISSING")
        return problems, False

    source, has_snapshot = _source_text(row["policy_id"], fragments)
    version = row["source_version"]
    spans = tuple(
        SourceSpanCheck(
            source_id=source.id,
            source_url=source.source_url,
            source_version=version,
            page=None,
            section=row.get("source_page_or_section") or None,
            quote=fragment,
            text_hash=quote_hash(fragment),
        )
        for fragment in fragments
    )
    verdict = validate_criterion(
        CriterionInput(
            id=row["criterion_id"],
            title=row["criterion_id"],
            review_finding="none",
            spans=spans,
            expression=json.loads(row["adjudicated_expression"]),
        ),
        {source.id: source},
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
