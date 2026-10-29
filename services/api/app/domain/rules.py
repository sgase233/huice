"""条件 AST 的校验与确定性求值（FR-01 / FR-04）。

两条纪律：

* **判断只由规则做**，不调用模型；同一组事实与同一个 AST 永远得到同一个结论。
* **未知不等于零**：事实缺失或值为 null 一律得到 ``insufficient_evidence``，绝不
  因为「看起来差不多」而判为满足。行业排除这类硬性条件一旦命中就是
  ``not_satisfied``，不会被相似度或其它软性条件抵消。

金额与人数统一用 :class:`decimal.Decimal` 计算，避免浮点误差把「等于 300 万」判成
「超过 300 万」。本模块不产生 ``conflict``：条款冲突来自人工标注的
``reviewFinding=conflict``，由评估服务直接置为冲突并禁止发布。
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

RULE_VERSION = "rule:criteria@1"

COMPARATORS = ("lt", "lte", "eq", "gte", "gt", "ne")

#: 每个事实键唯一允许的单位（契约 ``FactUnit`` 与 ``002`` 迁移的 check 一致）。
FACT_UNITS: Mapping[str, str] = {
    "region": "code",
    "industry": "code",
    "establishedDate": "date",
    "taxableIncome": "CNY",
    "rdExpense": "CNY",
    "assetsStart": "CNY",
    "assetsEnd": "CNY",
    "employeeCountStart": "person",
    "employeeCountEnd": "person",
    "rdActivity": "boolean",
    # 2026-10-28 扩展（rc.3）：地方通知用的事实。增量与占比按「预计算事实」处理，
    # 由标注方给出算好的数值，规则引擎只做 compare，不新增 delta / ratio 算子。
    "revenue": "CNY",
    "equityFinancing": "CNY",
    "rdExpenseIncrease": "CNY",
    "rdExpenseRevenueRatio": "ratio",
    "rdAddDeductionClaimed": "boolean",
    "creditRecord": "boolean",
}

NUMERIC_KEYS = frozenset(
    {
        "taxableIncome",
        "rdExpense",
        "assetsStart",
        "assetsEnd",
        "employeeCountStart",
        "employeeCountEnd",
        "revenue",
        "equityFinancing",
        "rdExpenseIncrease",
        "rdExpenseRevenueRatio",
    }
)
CODE_KEYS = frozenset({"region", "industry"})
BOOLEAN_KEYS = frozenset({"rdActivity", "rdAddDeductionClaimed", "creditRecord"})

QUARTERLY_METRICS: Mapping[str, tuple[str, str, str]] = {
    # metric -> (起始键, 结束键, 单位)
    "employeeCount": ("employeeCountStart", "employeeCountEnd", "person"),
    "assets": ("assetsStart", "assetsEnd", "CNY"),
}

_DECIMAL_RE = re.compile(r"^-?(0|[1-9][0-9]*)(\.[0-9]{1,6})?$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

MAX_DEPTH = 8
MAX_NODES = 200

SATISFIED = "satisfied"
NOT_SATISFIED = "not_satisfied"
INSUFFICIENT = "insufficient_evidence"
CONFLICT = "conflict"


# --------------------------------------------------------------------- 问题模型


@dataclass(frozen=True, slots=True)
class Problem:
    """AST 校验问题。``path`` 是 JSON Pointer 风格的位置，便于前端定位。"""

    code: str
    message: str
    path: str = ""


@dataclass(frozen=True, slots=True)
class FactSnapshot:
    """参与求值的一条事实。``value=None`` 表示「未知」。"""

    key: str
    period_start: date
    period_end: date
    unit: str
    value: Decimal | date | str | bool | None

    @property
    def ref(self) -> tuple[str, date, date]:
        return (self.key, self.period_start, self.period_end)

    @property
    def known(self) -> bool:
        return self.value is not None


class FactTable:
    """按 (key, 期间) 索引的事实表；同键同期间重复时以先出现的为准。"""

    def __init__(self, facts: Iterable[FactSnapshot]) -> None:
        self._rows: dict[tuple[str, date, date], FactSnapshot] = {}
        for fact in facts:
            self._rows.setdefault(fact.ref, fact)

    def get(self, key: str, start: date, end: date) -> FactSnapshot | None:
        return self._rows.get((key, start, end))

    def values(self) -> list[FactSnapshot]:
        return list(self._rows.values())


@dataclass(slots=True)
class Evaluation:
    """一次条件求值的结果；字段与契约 ``CriterionResult`` 对应。"""

    result: str
    reason: str
    fact_refs: list[tuple[str, date, date]]
    missing: list[tuple[str, date, date]]
    calculation: dict[str, Any] | None = None


# ------------------------------------------------------------------ 结构校验


def validate_expression(expression: Any) -> list[Problem]:
    """校验 AST 的结构与语义；空列表表示可以入库。

    ``MAX_NODES`` 是**整棵表达式**的节点总数上限，不只限制某个 and/or 的直接子项
    数：单层 200 个子项、每层再套 200 个，展开后是天文数字，只按单层限制等于没有
    限制，所以先整体计数，超限直接拒绝。
    """
    problems: list[Problem] = []
    if _exceeds_node_budget(expression):
        return [
            Problem(
                "EXPRESSION_TOO_LARGE",
                f"整棵表达式的节点总数不能超过 {MAX_NODES} 个。",
                "",
            )
        ]
    _validate_node(expression, problems, path="", depth=0)
    return problems


def _sub_expressions(node: Any) -> list[Any]:
    """一个节点直接包含的子表达式。

    AST 的嵌套形态只在这一处声明：新增带子表达式的算子时改这里，节点预算与引用收集
    都会跟着生效，不会出现「校验认得了、预算数不到」的漏洞。
    """

    if not isinstance(node, dict):
        return []
    op = node.get("op")
    if op in ("and", "or"):
        children = node.get("children")
        return children if isinstance(children, list) else []
    if op == "not":
        child = node.get("child")
        return [child] if child is not None else []
    return []


def _exceeds_node_budget(root: Any) -> bool:
    """节点数超出 ``MAX_NODES`` 立即返回，不对超大表达式做完整遍历。

    用显式栈而不是递归：``not`` 串成的长链在递归实现里会先撞上 Python 的递归上限，
    这里只关心「是否超限」，不需要精确计数。
    """

    count = 0
    stack: list[Any] = [root]
    while stack:
        node = stack.pop()
        count += 1
        if count > MAX_NODES:
            return True
        stack.extend(_sub_expressions(node))
    return False


def _validate_node(node: Any, problems: list[Problem], *, path: str, depth: int) -> None:
    if depth > MAX_DEPTH:
        problems.append(Problem("EXPRESSION_TOO_DEEP", f"条件嵌套不能超过 {MAX_DEPTH} 层。", path))
        return

    if not isinstance(node, dict):
        problems.append(Problem("EXPRESSION_SHAPE", "条件必须是对象。", path))
        return

    op = node.get("op")
    handler = {
        "and": _validate_and,
        "or": _validate_and,
        "not": _validate_not,
        "compare": _validate_compare,
        "date": _validate_date_node,
        "industry_exclusion": _validate_industry_exclusion,
        "quarterly_average": _validate_quarterly_average,
    }.get(op)
    if handler is None:
        problems.append(
            Problem("EXPRESSION_OP_UNKNOWN", "条件算子不在契约枚举内。", _join(path, "op"))
        )
        return
    handler(node, problems, path=path, depth=depth)


def _validate_and(node: dict, problems: list[Problem], *, path: str, depth: int) -> None:
    children = node.get("children")
    if not isinstance(children, list) or len(children) < 2:
        problems.append(
            Problem("EXPRESSION_CHILDREN", "and/or 至少需要两个子条件。", _join(path, "children"))
        )
        return
    # 子项数量不在这里单独限制：:func:`validate_expression` 的整树节点预算已经覆盖，
    # 单层再设一次上限只会给出「单层没超、整树超了」的误判空间。
    for index, child in enumerate(children):
        _validate_node(child, problems, path=_join(path, "children", index), depth=depth + 1)


def _validate_not(node: dict, problems: list[Problem], *, path: str, depth: int) -> None:
    if "child" not in node:
        problems.append(Problem("EXPRESSION_CHILD", "not 需要一个 child。", _join(path, "child")))
        return
    _validate_node(node["child"], problems, path=_join(path, "child"), depth=depth + 1)


def _validate_period(node: dict, problems: list[Problem], *, path: str) -> tuple[date, date] | None:
    period = node.get("period")
    if period == "assessment":
        return None
    if not isinstance(period, dict):
        problems.append(Problem("EXPRESSION_PERIOD", "period 必须是 assessment 或起止日期。", _join(path, "period")))
        return None
    start = _as_date(period.get("startDate"))
    end = _as_date(period.get("endDate"))
    if start is None or end is None:
        problems.append(Problem("EXPRESSION_PERIOD", "period 的起止日期不是合法日期。", _join(path, "period")))
        return None
    if end < start:
        problems.append(Problem("EXPRESSION_PERIOD", "period 的结束日期不能早于开始日期。", _join(path, "period")))
        return None
    return start, end


def _validate_compare(node: dict, problems: list[Problem], *, path: str, depth: int) -> None:
    key = node.get("factKey")
    if key not in FACT_UNITS:
        problems.append(Problem("EXPRESSION_FACT_KEY", "条件引用了未定义的事实键。", _join(path, "factKey")))
        return
    _validate_period(node, problems, path=path)

    comparator = node.get("comparator")
    if comparator not in COMPARATORS:
        problems.append(Problem("EXPRESSION_COMPARATOR", "比较符不在契约枚举内。", _join(path, "comparator")))
        return

    unit = node.get("unit")
    expected = FACT_UNITS[key]
    if unit != expected:
        problems.append(
            Problem(
                "EXPRESSION_UNIT",
                f"事实 {key} 的单位必须是 {expected}。",
                _join(path, "unit"),
            )
        )
        return

    value = node.get("value")
    if expected in ("CNY", "person", "ratio"):
        if not isinstance(value, str) or not _DECIMAL_RE.match(value):
            problems.append(Problem("EXPRESSION_VALUE", "数值条件的取值必须是十进制字符串。", _join(path, "value")))
            return
        # 金额键允许负数：``rdExpenseIncrease`` 是「本年减上年」的增量，下降时为负。
        if expected == "person" and value.startswith("-"):
            problems.append(Problem("EXPRESSION_VALUE", "人数不能为负数。", _join(path, "value")))
        elif expected == "ratio" and value.startswith("-"):
            problems.append(Problem("EXPRESSION_VALUE", "比例不能为负数。", _join(path, "value")))

    elif expected == "code":
        if not isinstance(value, str) or not value.strip():
            problems.append(Problem("EXPRESSION_VALUE", "代码类条件的取值必须是非空字符串。", _join(path, "value")))
            return
        if comparator not in ("eq", "ne"):
            problems.append(
                Problem("EXPRESSION_COMPARATOR", "代码类条件只允许 eq / ne。", _join(path, "comparator"))
            )
    elif expected == "boolean":
        if not isinstance(value, bool):
            problems.append(Problem("EXPRESSION_VALUE", "布尔条件的取值必须是 true / false。", _join(path, "value")))
            return
        if comparator not in ("eq", "ne"):
            problems.append(
                Problem("EXPRESSION_COMPARATOR", "布尔条件只允许 eq / ne。", _join(path, "comparator"))
            )
    elif expected == "date":  # pragma: no cover - anchoredDate 由 date 算子表达
        problems.append(Problem("EXPRESSION_OP", "日期事实请使用 date 算子。", path))


def _validate_date_node(node: dict, problems: list[Problem], *, path: str, depth: int) -> None:
    if node.get("factKey") != "establishedDate":
        problems.append(
            Problem("EXPRESSION_FACT_KEY", "date 算子只接受 establishedDate。", _join(path, "factKey"))
        )
    _validate_period(node, problems, path=path)
    if node.get("comparator") not in COMPARATORS:
        problems.append(Problem("EXPRESSION_COMPARATOR", "比较符不在契约枚举内。", _join(path, "comparator")))
    value = _as_date(node.get("value"))
    if value is None:
        problems.append(Problem("EXPRESSION_VALUE", "date 条件的取值必须是 YYYY-MM-DD。", _join(path, "value")))


def _validate_industry_exclusion(node: dict, problems: list[Problem], *, path: str, depth: int) -> None:
    if node.get("factKey") != "industry":
        problems.append(
            Problem("EXPRESSION_FACT_KEY", "industry_exclusion 只接受 industry。", _join(path, "factKey"))
        )
    _validate_period(node, problems, path=path)
    excluded = node.get("excluded")
    if not isinstance(excluded, list) or not excluded:
        problems.append(
            Problem("EXPRESSION_EXCLUDED", "排除清单至少需要一项。", _join(path, "excluded"))
        )
        return
    seen: set[str] = set()
    for index, item in enumerate(excluded):
        if not isinstance(item, str) or not item.strip():
            problems.append(
                Problem("EXPRESSION_EXCLUDED", "排除清单的每一项都必须是非空字符串。", _join(path, "excluded", index))
            )
            continue
        if item in seen:
            problems.append(
                Problem("EXPRESSION_EXCLUDED", "排除清单不能有重复项。", _join(path, "excluded", index))
            )
        seen.add(item)


def _validate_quarterly_average(node: dict, problems: list[Problem], *, path: str, depth: int) -> None:
    metric = node.get("metric")
    if metric not in QUARTERLY_METRICS:
        problems.append(Problem("EXPRESSION_METRIC", "季度平均只支持 employeeCount / assets。", _join(path, "metric")))
        return
    _, _, expected_unit = QUARTERLY_METRICS[metric]
    if node.get("unit") != expected_unit:
        problems.append(
            Problem("EXPRESSION_UNIT", f"指标 {metric} 的单位必须是 {expected_unit}。", _join(path, "unit"))
        )
    if node.get("comparator") not in COMPARATORS:
        problems.append(Problem("EXPRESSION_COMPARATOR", "比较符不在契约枚举内。", _join(path, "comparator")))
    value = node.get("value")
    if not isinstance(value, str) or not _DECIMAL_RE.match(value):
        problems.append(Problem("EXPRESSION_VALUE", "季度平均的取值必须是十进制字符串。", _join(path, "value")))

    period = _validate_period(node, problems, path=path)
    if period is not None and not _is_full_natural_year(*period):
        problems.append(
            Problem("EXPRESSION_PERIOD", "季度平均只支持完整自然年度。", _join(path, "period"))
        )


# ------------------------------------------------------------------ 事实引用


def referenced_facts(expression: Any, *, period: tuple[date, date] | None) -> list[tuple[str, date, date]]:
    """列出表达式求值所依赖的事实；顺序稳定且去重。"""
    refs: list[tuple[str, date, date]] = []
    _collect_refs(expression, period=period, out=refs)
    return refs


def _collect_refs(
    node: Any, *, period: tuple[date, date] | None, out: list[tuple[str, date, date]]
) -> None:
    if not isinstance(node, dict):
        return
    op = node.get("op")
    children = _sub_expressions(node)
    if children:
        for child in children:
            _collect_refs(child, period=period, out=out)
        return
    if op in ("compare", "date", "industry_exclusion"):
        span = _node_period(node, period)
        if span is not None:
            _push(out, (str(node.get("factKey")), span[0], span[1]))
        return
    if op == "quarterly_average":
        metric = node.get("metric")
        if metric not in QUARTERLY_METRICS:
            return
        start_key, end_key, _ = QUARTERLY_METRICS[metric]
        year = _natural_year_of(node, period)
        if year is None:
            return
        for quarter_start, quarter_end in quarters_of(year):
            _push(out, (start_key, quarter_start, quarter_end))
            _push(out, (end_key, quarter_start, quarter_end))


def _push(out: list[tuple[str, date, date]], ref: tuple[str, date, date]) -> None:
    if ref not in out:
        out.append(ref)


# --------------------------------------------------------------------- 求值


def evaluate_expression(
    expression: Any, facts: FactTable, *, period: tuple[date, date]
) -> Evaluation:
    """按四态求值。``period`` 是本次评估期间，用于 ``period=assessment`` 的条件。"""
    return _evaluate(expression, facts, period=period)


def _evaluate(node: Any, facts: FactTable, *, period: tuple[date, date]) -> Evaluation:
    if not isinstance(node, dict):
        return Evaluation(CONFLICT, "条件结构损坏，无法判断。", [], [])

    op = node.get("op")
    if op in ("and", "or"):
        return _evaluate_boolean(op, node, facts, period=period)
    if op == "not":
        inner = _evaluate(node.get("child"), facts, period=period)
        if inner.result == SATISFIED:
            return Evaluation(NOT_SATISFIED, "取反后不成立：" + inner.reason, inner.fact_refs, inner.missing, inner.calculation)
        if inner.result == NOT_SATISFIED:
            return Evaluation(SATISFIED, "取反后成立：" + inner.reason, inner.fact_refs, inner.missing, inner.calculation)
        return inner
    if op == "compare":
        return _evaluate_compare(node, facts, period=period)
    if op == "date":
        return _evaluate_date(node, facts, period=period)
    if op == "industry_exclusion":
        return _evaluate_industry_exclusion(node, facts, period=period)
    if op == "quarterly_average":
        return _evaluate_quarterly_average(node, facts, period=period)
    return Evaluation(CONFLICT, "条件算子不在契约枚举内，无法判断。", [], [])


def _evaluate_boolean(
    op: str, node: dict, facts: FactTable, *, period: tuple[date, date]
) -> Evaluation:
    results = [_evaluate(child, facts, period=period) for child in node.get("children") or []]
    fact_refs = _merge([item.fact_refs for item in results])
    missing = _merge([item.missing for item in results])

    # 冲突最先检测并向上传播，OR 成立也不能把它掩盖（实现约定 2.2）。
    for item in results:
        if item.result == CONFLICT:
            return Evaluation(CONFLICT, item.reason, fact_refs, missing)

    if op == "and":
        # 硬性条件优先：任何一个子条件已判定不成立，整体就不成立，缺事实不能掩盖它。
        for item in results:
            if item.result == NOT_SATISFIED:
                return Evaluation(NOT_SATISFIED, item.reason, fact_refs, missing)
        for item in results:
            if item.result == INSUFFICIENT:
                return Evaluation(INSUFFICIENT, item.reason, fact_refs, missing)
        return Evaluation(SATISFIED, "全部子条件成立。", fact_refs, missing)

    for item in results:
        if item.result == SATISFIED:
            return Evaluation(SATISFIED, item.reason, fact_refs, missing)
    for item in results:
        if item.result == INSUFFICIENT:
            return Evaluation(INSUFFICIENT, item.reason, fact_refs, missing)
    reason = results[0].reason if results else "没有子条件成立。"
    return Evaluation(NOT_SATISFIED, reason, fact_refs, missing)


def _evaluate_compare(node: dict, facts: FactTable, *, period: tuple[date, date]) -> Evaluation:
    key = str(node.get("factKey"))
    span = _node_period(node, period)
    if span is None:
        return Evaluation(CONFLICT, "条件缺少可用的期间。", [])
    fact = facts.get(key, *span)
    ref = (key, span[0], span[1])
    if fact is None or not fact.known:
        return Evaluation(
            INSUFFICIENT,
            f"缺少事实 {key}（{span[0].isoformat()} 至 {span[1].isoformat()}），无法判断。",
            [ref],
            [ref],
        )

    comparator = str(node.get("comparator"))
    unit = FACT_UNITS.get(key)
    if unit in ("CNY", "person", "ratio"):
        left = fact.value if isinstance(fact.value, Decimal) else _to_decimal(fact.value)
        right = _to_decimal(node.get("value"))
        if left is None or right is None:
            return Evaluation(CONFLICT, f"事实 {key} 的取值无法用十进制比较。", [ref])
        ok = _compare_numbers(left, right, comparator)
        reason = (
            f"{key}={_fmt(left)}{_unit_label(unit)} "
            f"{_comparator_text(comparator)} {_fmt(right)}{_unit_label(unit)}"
            f"{'成立' if ok else '不成立'}。"
        )
        return Evaluation(
            SATISFIED if ok else NOT_SATISFIED, reason, [ref], [],
            {"factRef": _ref_json(ref), "value": _fmt(left)},
        )

    if unit == "code":
        ok = _compare_scalar(str(fact.value), str(node.get("value")), comparator)
        reason = f"{key}={fact.value} {'满足' if ok else '不满足'} {node.get('value')}。"
        return Evaluation(SATISFIED if ok else NOT_SATISFIED, reason, [ref], [])

    if unit == "boolean":
        ok = _compare_scalar(bool(fact.value), bool(node.get("value")), comparator)
        reason = f"{key}={fact.value} {'满足' if ok else '不满足'} {node.get('value')}。"
        return Evaluation(SATISFIED if ok else NOT_SATISFIED, reason, [ref], [])

    return Evaluation(CONFLICT, f"事实 {key} 的单位无法比较。", [ref])


def _evaluate_date(node: dict, facts: FactTable, *, period: tuple[date, date]) -> Evaluation:
    span = _node_period(node, period)
    if span is None:
        return Evaluation(CONFLICT, "条件缺少可用的期间。", [])
    ref = ("establishedDate", span[0], span[1])
    fact = facts.get("establishedDate", *span)
    if fact is None or not fact.known:
        return Evaluation(
            INSUFFICIENT,
            "缺少成立日期，无法判断时间条件。",
            [ref],
            [ref],
        )
    left = fact.value if isinstance(fact.value, date) else _as_date(str(fact.value))
    right = _as_date(node.get("value"))
    if left is None or right is None:
        return Evaluation(CONFLICT, "日期取值无法解析。", [ref])
    comparator = str(node.get("comparator"))
    ok = _compare_scalar(left.isoformat(), right.isoformat(), comparator)
    reason = f"成立日期 {left.isoformat()} {'满足' if ok else '不满足'} {comparator} {right.isoformat()}。"
    return Evaluation(SATISFIED if ok else NOT_SATISFIED, reason, [ref], [])


def _evaluate_industry_exclusion(
    node: dict, facts: FactTable, *, period: tuple[date, date]
) -> Evaluation:
    span = _node_period(node, period)
    if span is None:
        return Evaluation(CONFLICT, "条件缺少可用的期间。", [])
    ref = ("industry", span[0], span[1])
    fact = facts.get("industry", *span)
    if fact is None or not fact.known:
        return Evaluation(INSUFFICIENT, "缺少行业信息，无法判断是否属于排除行业。", [ref], [ref])
    excluded = [str(item) for item in node.get("excluded") or []]
    if str(fact.value) in excluded:
        return Evaluation(
            NOT_SATISFIED,
            f"行业 {fact.value} 属于排除清单，不满足该条款。",
            [ref],
            [],
        )
    return Evaluation(SATISFIED, f"行业 {fact.value} 不在排除清单内。", [ref], [])


def _evaluate_quarterly_average(
    node: dict, facts: FactTable, *, period: tuple[date, date]
) -> Evaluation:
    metric = str(node.get("metric"))
    if metric not in QUARTERLY_METRICS:
        return Evaluation(CONFLICT, "季度平均指标不在枚举内。", [])
    start_key, end_key, unit = QUARTERLY_METRICS[metric]

    year = _natural_year_of(node, period)
    if year is None:
        return Evaluation(
            INSUFFICIENT,
            "季度平均要求完整自然年度的期间，当前期间不满足。",
            [],
            [],
        )

    refs: list[tuple[str, date, date]] = []
    missing: list[tuple[str, date, date]] = []
    inputs: list[dict[str, Any]] = []
    mids: list[Decimal] = []
    for quarter_start, quarter_end in quarters_of(year):
        start_fact = facts.get(start_key, quarter_start, quarter_end)
        end_fact = facts.get(end_key, quarter_start, quarter_end)
        start_value = _numeric_or_none(start_fact)
        end_value = _numeric_or_none(end_fact)
        start_ref = (start_key, quarter_start, quarter_end)
        end_ref = (end_key, quarter_start, quarter_end)
        refs.extend([start_ref, end_ref])
        if start_value is None:
            missing.append(start_ref)
        if end_value is None:
            missing.append(end_ref)
        if start_value is None or end_value is None:
            continue
        # 口径来自实现约定 2.2：每个季度先取 (期初+期末)/2，再对四个季度取平均。
        midpoint = ((start_value + end_value) / Decimal(2)).quantize(Decimal("0.000001"))
        mids.append(midpoint)
        inputs.append(
            {
                "quarter": f"{quarter_start.isoformat()}/{quarter_end.isoformat()}",
                "start": _fmt(start_value),
                "end": _fmt(end_value),
                "midpoint": _fmt(midpoint),
            }
        )

    if missing or len(mids) != 4:
        return Evaluation(
            INSUFFICIENT,
            f"季度平均需要 {year} 年 4 个季度的期初与期末共 8 个事实，仍缺 {len(missing)} 个。",
            refs,
            missing,
        )

    average = (sum(mids) / Decimal(len(mids))).quantize(Decimal("0.000001"))
    right = _to_decimal(node.get("value"))
    if right is None:
        return Evaluation(CONFLICT, "季度平均的比较值无法解析。", refs)
    comparator = str(node.get("comparator"))
    ok = _compare_numbers(average, right, comparator)
    reason = (
        f"{year} 年季度平均 {_fmt(average)}{_unit_label(unit)} "
        f"{_comparator_text(comparator)} {_fmt(right)}{_unit_label(unit)}"
        f"{'成立' if ok else '不成立'}。"
    )
    return Evaluation(
        SATISFIED if ok else NOT_SATISFIED,
        reason,
        refs,
        [],
        {
            "metric": metric,
            "inputs": inputs,
            "average": _fmt(average),
            "threshold": _fmt(right),
            "comparator": comparator,
        },
    )


def aggregate(results: Iterable[Evaluation]) -> str:
    """把多条条件的结果合并成政策级四态（实现约定 2.4）。

    优先级：``conflict``（条款冲突需人工复核）> ``not_satisfied``（硬性条件已不成立）
    > ``insufficient_evidence``（还缺事实）> ``satisfied``。
    """

    seen = {item.result for item in results}
    if CONFLICT in seen:
        return CONFLICT
    if NOT_SATISFIED in seen:
        return NOT_SATISFIED
    if INSUFFICIENT in seen:
        return INSUFFICIENT
    return SATISFIED


# --------------------------------------------------------------- 政策级求值


@dataclass(frozen=True, slots=True)
class CriterionRule:
    """参与求值的一条已审核条件；``review_finding=conflict`` 时固定返回冲突。"""

    id: Any
    title: str
    expression: Mapping[str, Any]
    review_finding: str = "none"


POLICY_UNSATISFIABLE = "POLICY_UNSATISFIABLE"

#: 会挡住发布（API-07）的政策级 finding。抽取阶段的 ``EXTRACTION_*`` 是过程提示
#: （``status=needs_review``），人工补录条款后本就消失，因此不在其列。
POLICY_BLOCKING_CODES = frozenset({POLICY_UNSATISFIABLE})


@dataclass(frozen=True, slots=True)
class PolicyContradiction:
    """两条条款合起来永远不成立——政策对任何企业都只能是 ``not_satisfied``。"""

    code: str
    message: str
    criterion_ids: tuple[Any, ...]


def policy_contradictions(criteria: Iterable[CriterionRule]) -> list[PolicyContradiction]:
    """找出「合取为空」的条款对（跨条款，单条 AST 校验看不出来）。

    政策级一律按 AND 合并（见 :func:`aggregate`），所以两条顶层原子条件若在同一
    事实、同一期间上要求互斥的取值，这条政策对任何企业都不可能是 ``satisfied``：
    管理员看到的是「不达标」，实际原因是标注把原文的「或」写成了两条并列的「且」。
    M3 的 SRDI 曾把「营收≥1000 万」与「营收<1000 万」并列，正是这一类。

    只看顶层 ``compare`` 节点。``or`` / ``not`` / ``quarterly_average`` 等节点内部
    的限制不在此列——OR 分支本来就允许互斥，报出来只会是误报。期间只在写法完全
    相同时合并（同为 ``assessment``，或相同的显式起止日期）：``assessment`` 展开成
    哪一段要等运行时才知道，草稿阶段不能拿它去和写死的日期比。

    单位不一致、取值不是合法十进制、比较符不在枚举内的组合一律跳过，交给单条校验
    去报，这里不重复报错也不猜。
    """

    groups: dict[tuple[str, Any], list[tuple[CriterionRule, Mapping[str, Any]]]] = {}
    for rule in criteria:
        node = rule.expression
        if not isinstance(node, Mapping) or node.get("op") != "compare":
            continue
        key = node.get("factKey")
        period = _period_identity(node.get("period"))
        if not isinstance(key, str) or key not in FACT_UNITS or period is None:
            continue
        groups.setdefault((key, period), []).append((rule, node))

    contradictions: list[PolicyContradiction] = []
    for (key, _period), members in groups.items():
        for left_index, (left_rule, left) in enumerate(members):
            for right_rule, right in members[left_index + 1 :]:
                if left_rule.id == right_rule.id:
                    continue
                reason = _disjoint_reason(left, right)
                if reason is None:
                    continue
                contradictions.append(
                    PolicyContradiction(
                        code=POLICY_UNSATISFIABLE,
                        message=(
                            f"条款「{left_rule.title}」（{left_rule.id}）与「{right_rule.title}」"
                            f"（{right_rule.id}）对同一事实 {key}、同一期间的要求互斥（{reason}），"
                            "政策级按「且」合并后永远无法满足。"
                            "若原文是「或」，请把两个分支写进同一条条件的 or 节点。"
                        ),
                        criterion_ids=(left_rule.id, right_rule.id),
                    )
                )
    return contradictions


def _period_identity(raw: Any) -> Any:
    """期间的可比标识；``None`` 表示这条条件不参与跨条款比较（写法不完整）。"""

    if raw == "assessment":
        return "assessment"
    if isinstance(raw, Mapping):
        start, end = raw.get("startDate"), raw.get("endDate")
        if isinstance(start, str) and isinstance(end, str):
            return (start, end)
    return None


def _disjoint_reason(left: Mapping[str, Any], right: Mapping[str, Any]) -> str | None:
    """两条同事实、同期间、同单位的 ``compare`` 是否互斥；互斥时返回人类可读的说明。"""

    if left.get("unit") != right.get("unit"):
        return None
    left_op, right_op = left.get("comparator"), right.get("comparator")
    if left_op not in COMPARATORS or right_op not in COMPARATORS:
        return None
    left_value, right_value = left.get("value"), right.get("value")

    if left.get("unit") in ("CNY", "person", "ratio"):
        left_number, right_number = _decimal_of(left_value), _decimal_of(right_value)
        if left_number is None or right_number is None:
            return None
        return _numeric_disjoint(left_op, left_number, right_op, right_number)

    # code / boolean：只有 eq 与 ne，互斥只可能是「同一个取值又要又不要」或「两个不同的等值」。
    if left_op == "eq" and right_op == "eq" and left_value != right_value:
        return f"eq {left_value} 与 eq {right_value}"
    if {left_op, right_op} == {"eq", "ne"}:
        equal_value = left_value if left_op == "eq" else right_value
        unequal_value = right_value if left_op == "eq" else left_value
        if equal_value == unequal_value:
            return f"eq {equal_value} 与 ne {unequal_value}"
    return None


def _decimal_of(value: Any) -> Decimal | None:
    if not isinstance(value, str) or not _DECIMAL_RE.match(value):
        return None
    try:
        return Decimal(value)
    except InvalidOperation:  # pragma: no cover - 正则已排除
        return None


def _numeric_bounds(
    comparator: str, value: Decimal
) -> tuple[Decimal | None, bool, Decimal | None, bool] | None:
    """把比较符化成区间 ``(下界, 下界是否可取, 上界, 上界是否可取)``；``ne`` 不是区间。"""

    if comparator == "gt":
        return value, False, None, False
    if comparator == "gte":
        return value, True, None, False
    if comparator == "lt":
        return None, False, value, False
    if comparator == "lte":
        return None, False, value, True
    if comparator == "eq":
        return value, True, value, True
    return None


def _numeric_disjoint(
    left_op: str, left_value: Decimal, right_op: str, right_value: Decimal
) -> str | None:
    left_bounds = _numeric_bounds(left_op, left_value)
    right_bounds = _numeric_bounds(right_op, right_value)
    if left_bounds is None or right_bounds is None:
        # 至少一侧是 ne：区间不是单段，只有另一侧把取值钉死在同一数值时才互斥。
        point = left_bounds if left_bounds is not None else right_bounds
        unequal = right_value if left_bounds is not None else left_value
        if point is not None and point[0] == unequal == point[2]:
            other_op = left_op if left_bounds is not None else right_op
            return f"{other_op} {point[0]} 与 ne {unequal}"
        return None

    low, low_inclusive = _tighter_low(left_bounds, right_bounds)
    high, high_inclusive = _tighter_high(left_bounds, right_bounds)
    if low is None or high is None:
        return None
    if low > high or (low == high and not (low_inclusive and high_inclusive)):
        return f"{left_op} {left_value} 与 {right_op} {right_value}"
    return None


def _tighter_low(
    left: tuple[Decimal | None, bool, Decimal | None, bool],
    right: tuple[Decimal | None, bool, Decimal | None, bool],
) -> tuple[Decimal | None, bool]:
    low, low_inclusive = left[0], left[1]
    if right[0] is None:
        return low, low_inclusive
    if low is None or right[0] > low:
        return right[0], right[1]
    if right[0] == low:
        return low, low_inclusive and right[1]
    return low, low_inclusive


def _tighter_high(
    left: tuple[Decimal | None, bool, Decimal | None, bool],
    right: tuple[Decimal | None, bool, Decimal | None, bool],
) -> tuple[Decimal | None, bool]:
    high, high_inclusive = left[2], left[3]
    if right[2] is None:
        return high, high_inclusive
    if high is None or right[2] < high:
        return right[2], right[3]
    if right[2] == high:
        return high, high_inclusive and right[3]
    return high, high_inclusive


@dataclass(slots=True)
class CriterionEvaluation:
    criterion_id: Any
    result: str
    reason: str
    fact_refs: list[tuple[str, date, date]]
    missing: list[tuple[str, date, date]]
    calculation: dict[str, Any] | None = None


@dataclass(slots=True)
class PolicyEvaluation:
    policy_id: Any
    version: str
    result: str
    criteria: list[CriterionEvaluation]
    dependencies: list[tuple[str, date, date]]


def evaluate_policy(
    *,
    policy_id: Any,
    version: str,
    criteria: Iterable[CriterionRule],
    facts: FactTable,
    period: tuple[date, date],
    as_of: date | None = None,
) -> PolicyEvaluation:
    """按政策快照求四态。``as_of`` 仅用于审计与时间门槛判断，不改变规则口径。

    政策级默认 AND：存在冲突为 ``conflict``；否则任一条件不满足为
    ``not_satisfied``；否则存在必要事实未知为 ``insufficient_evidence``；全部满足
    才是 ``satisfied``。
    """

    evaluations: list[CriterionEvaluation] = []
    for rule in criteria:
        if rule.review_finding == "conflict":
            evaluations.append(
                CriterionEvaluation(
                    criterion_id=rule.id,
                    result=CONFLICT,
                    reason="该条款在来源中互相抵触，需人工复核后才能给出肯定结论。",
                    fact_refs=[],
                    missing=[],
                )
            )
            continue
        outcome = evaluate_expression(rule.expression, facts, period=period)
        evaluations.append(
            CriterionEvaluation(
                criterion_id=rule.id,
                result=outcome.result,
                reason=outcome.reason,
                fact_refs=outcome.fact_refs,
                missing=outcome.missing,
                calculation=outcome.calculation,
            )
        )

    dependencies: list[tuple[str, date, date]] = []
    for item in evaluations:
        for ref in item.fact_refs:
            if ref not in dependencies:
                dependencies.append(ref)

    return PolicyEvaluation(
        policy_id=policy_id,
        version=version,
        result=aggregate(
            Evaluation(item.result, item.reason, item.fact_refs, item.missing)
            for item in evaluations
        ),
        criteria=evaluations,
        dependencies=dependencies,
    )


# --------------------------------------------------------------------- 工具


def quarters_of(year: int) -> list[tuple[date, date]]:
    bounds = ((1, 3), (4, 6), (7, 9), (10, 12))
    return [(date(year, start, 1), date(year, end, calendar.monthrange(year, end)[1])) for start, end in bounds]


def _node_period(node: dict, period: tuple[date, date]) -> tuple[date, date] | None:
    raw = node.get("period")
    if raw == "assessment":
        return period
    start = _as_date((raw or {}).get("startDate")) if isinstance(raw, dict) else None
    end = _as_date((raw or {}).get("endDate")) if isinstance(raw, dict) else None
    if start is None or end is None:
        return None
    return start, end


def _natural_year_of(node: dict, period: tuple[date, date]) -> int | None:
    span = _node_period(node, period)
    if span is None or not _is_full_natural_year(*span):
        return None
    return span[0].year


def _is_full_natural_year(start: date, end: date) -> bool:
    return (
        start.month == 1
        and start.day == 1
        and end.month == 12
        and end.day == 31
        and start.year == end.year
    )


def _compare_numbers(left: Decimal, right: Decimal, comparator: str) -> bool:
    if comparator == "lt":
        return left < right
    if comparator == "lte":
        return left <= right
    if comparator == "eq":
        return left == right
    if comparator == "gte":
        return left >= right
    if comparator == "gt":
        return left > right
    if comparator == "ne":
        return left != right
    raise ValueError(f"未知比较符 {comparator}")


def _compare_scalar(left: Any, right: Any, comparator: str) -> bool:
    if comparator == "eq":
        return left == right
    if comparator == "ne":
        return left != right
    if comparator == "lt":
        return left < right
    if comparator == "lte":
        return left <= right
    if comparator == "gt":
        return left > right
    if comparator == "gte":
        return left >= right
    raise ValueError(f"未知比较符 {comparator}")


def _comparator_text(comparator: str) -> str:
    return {
        "lt": "<",
        "lte": "≤",
        "eq": "=",
        "gte": "≥",
        "gt": ">",
        "ne": "≠",
    }.get(comparator, comparator)


def _unit_label(unit: str | None) -> str:
    return {"CNY": " 元", "person": " 人"}.get(unit or "", "")


def _fmt(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return text


def _numeric_or_none(fact: FactSnapshot | None) -> Decimal | None:
    if fact is None or not fact.known:
        return None
    return fact.value if isinstance(fact.value, Decimal) else _to_decimal(fact.value)


def _to_decimal(value: Any) -> Decimal | None:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool) or value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _as_date(value: Any) -> date | None:
    if isinstance(value, date) and not hasattr(value, "hour"):
        return value
    if not isinstance(value, str) or not _DATE_RE.match(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _merge(groups: Iterable[list[tuple[str, date, date]]]) -> list[tuple[str, date, date]]:
    out: list[tuple[str, date, date]] = []
    for group in groups:
        for ref in group:
            if ref not in out:
                out.append(ref)
    return out


def _ref_json(ref: tuple[str, date, date]) -> dict[str, str]:
    return {"key": ref[0], "period": {"startDate": ref[1].isoformat(), "endDate": ref[2].isoformat()}}


def _join(path: str, *parts: str | int) -> str:
    return "/".join([path, *[str(part) for part in parts]]) if path else "/".join(str(part) for part in parts)
