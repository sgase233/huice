"""引用校验（FR-02）：条款的每一条原文证据都要能在那份来源里逐字找到。

校验发生在两个时点：保存条款草稿（API-06）和发布（API-07）前重算。任何一条
``failed`` 的条款都不允许发布——这是「超过 300 万元」被抽成「不低于 300 万元」
这类错误不会流出到用户界面的唯一保证。

核对三类东西：

1. **来源**：``sourceId`` 属于该政策版本、``sourceUrl`` 与政策来源一致、
   ``sourceVersion`` 与人工 version 一致。
2. **文本**：引文在该来源正文中逐字存在（只容忍空白差异），``textHash`` 与引文
   的 sha256 一致，``page``/``section`` 至少给一个。
3. **数值与方向**：把引文里的阈值抽出来，与实际 AST 的数值、单位换算、比较方向
   逐项比对；数值对不上或方向相反（否定词）都判为不支持。行业排除与研发活动这类
   非数值条件只做「引文里有对应表述」的存在性检查。

``reviewFinding=conflict`` 的条款**可以**发布：它本身就是「经人工确认的冲突记录」，
规则引擎会固定返回 ``conflict``。这与「引用不支持表达式」是两回事，后者不能发布。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping, Sequence
from uuid import UUID

MAX_QUOTE_LENGTH = 2000


# ------------------------------------------------------------------ 输入对象


@dataclass(frozen=True, slots=True)
class SourceText:
    """校验需要的来源快照；与数据库 ``policy_source`` 的读取结果对应。"""

    id: UUID
    source_url: str
    text: str
    kind: str = "body"


@dataclass(frozen=True, slots=True)
class SourceSpanCheck:
    """待校验的引用片段；字段与契约 ``SourceSpan`` 一致。"""

    source_id: UUID
    source_url: str
    source_version: str
    page: int | None
    section: str | None
    quote: str
    text_hash: str

    def to_wire(self) -> dict[str, Any]:
        return {
            "sourceId": str(self.source_id),
            "sourceUrl": self.source_url,
            "sourceVersion": self.source_version,
            "page": self.page,
            "section": self.section,
            "quote": self.quote,
            "textHash": self.text_hash,
        }


@dataclass(frozen=True, slots=True)
class CriterionInput:
    id: UUID
    title: str
    review_finding: str
    spans: tuple[SourceSpanCheck, ...]
    expression: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class Finding:
    code: str
    message: str
    source_spans: tuple[SourceSpanCheck, ...] = ()

    def to_wire(self, criterion_id: UUID | None) -> dict[str, Any]:
        return {
            "criterionId": str(criterion_id) if criterion_id else None,
            "code": self.code,
            "message": self.message,
            "sourceSpans": [span.to_wire() for span in self.source_spans],
        }


@dataclass(slots=True)
class CriterionVerdict:
    """一条条款的校验结论。``status`` 直接写入 ``policy_criterion.validation_status``。"""

    criterion_id: UUID
    status: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.status == "passed"


# ------------------------------------------------------------------ 公开入口


def validate_criteria(
    criteria: Sequence[CriterionInput],
    sources: Mapping[UUID, SourceText],
    version: str,
) -> list[CriterionVerdict]:
    """校验整版条款：逐条结论 + 版本级重复 ID 检查。``version`` 是人工版本字符串。"""
    verdicts = [validate_criterion(item, sources, version=version) for item in criteria]

    seen: set[UUID] = set()
    for verdict, criterion in zip(verdicts, criteria):
        if criterion.id in seen:
            verdict.status = "failed"
            verdict.findings.append(
                Finding("CRITERION_DUPLICATE_ID", "同一政策版本内条款 ID 不能重复。")
            )
        seen.add(criterion.id)
    return verdicts


def validate_criterion(
    criterion: CriterionInput, sources: Mapping[UUID, SourceText], *, version: str
) -> CriterionVerdict:
    verdict = CriterionVerdict(criterion_id=criterion.id, status="passed")

    if not criterion.spans:
        verdict.findings.append(Finding("CRITERION_NO_SPANS", "条款至少需要一条原文引用。"))
        verdict.status = "failed"
        return verdict

    for index, span in enumerate(criterion.spans):
        _check_span(span, sources, verdict, version=version, index=index)

    _check_conflict_annotation(criterion, verdict)
    _check_threshold_conflict(criterion, verdict)
    if criterion.expression is not None:
        _check_expression_support(criterion, verdict)

    if verdict.findings:
        verdict.status = "failed"
    return verdict


def all_passed(verdicts: Iterable[CriterionVerdict]) -> bool:
    return all(verdict.passed for verdict in verdicts)


# ---------------------------------------------------------------- 来源与文本


def _check_span(
    span: SourceSpanCheck,
    sources: Mapping[UUID, SourceText],
    verdict: CriterionVerdict,
    *,
    version: str,
    index: int,
) -> None:
    label = f"第 {index + 1} 条引用"
    source = sources.get(span.source_id)
    if source is None:
        verdict.findings.append(
            Finding("SPAN_SOURCE_UNKNOWN", f"{label}指向的来源不属于该政策版本。", (span,))
        )
        return

    if span.source_url != source.source_url:
        verdict.findings.append(
            Finding("SPAN_SOURCE_URL_MISMATCH", f"{label}的来源链接与政策来源不一致。", (span,))
        )

    if span.source_version != version:
        verdict.findings.append(
            Finding(
                "SPAN_SOURCE_VERSION_MISMATCH",
                f"{label}的 sourceVersion 与政策版本 {version} 不一致。",
                (span,),
            )
        )

    if not span.quote.strip():
        verdict.findings.append(Finding("SPAN_QUOTE_EMPTY", f"{label}的引文为空。", (span,)))
        return

    if len(span.quote) > MAX_QUOTE_LENGTH:
        verdict.findings.append(
            Finding(
                "SPAN_QUOTE_TOO_LONG",
                f"{label}的引文超过 {MAX_QUOTE_LENGTH} 字，请截取与条件直接相关的片段。",
                (span,),
            )
        )
        return

    if span.page is None and span.section is None:
        verdict.findings.append(
            Finding("SPAN_LOCATION_MISSING", f"{label}缺少页码或章节，无法定位原文。", (span,))
        )

    if span.text_hash != quote_hash(span.quote):
        verdict.findings.append(
            Finding("SPAN_TEXT_HASH_MISMATCH", f"{label}的 textHash 与引文不一致。", (span,))
        )

    if not quote_in_text(span.quote, source.text):
        verdict.findings.append(
            Finding("SPAN_QUOTE_NOT_FOUND", f"{label}的引文在来源原文中找不到，请复核后重试。", (span,))
        )


def _check_conflict_annotation(criterion: CriterionInput, verdict: CriterionVerdict) -> None:
    if criterion.review_finding != "conflict":
        return
    if len(criterion.spans) < 2:
        verdict.findings.append(
            Finding(
                "CONFLICT_NEEDS_TWO_SPANS",
                "标记为条款冲突的条目必须给出两处互相矛盾的原文引用。",
                criterion.spans,
            )
        )
        return
    if len({_normalize(span.quote) for span in criterion.spans}) < 2:
        verdict.findings.append(
            Finding(
                "CONFLICT_SPANS_IDENTICAL",
                "标记为冲突的两处引用内容相同，不能作为冲突证据。",
                criterion.spans,
            )
        )


def _check_threshold_conflict(criterion: CriterionInput, verdict: CriterionVerdict) -> None:
    """两条引文对同一指标给出方向相反的阈值时，必须显式标注为冲突。

    标注了 ``reviewFinding=conflict`` 的条目是「经人工确认的冲突记录」，允许发布并由
    规则引擎固定返回 ``conflict``；没有标注却出现方向相反的两处引文，说明标注本身
    还没处理完，不能跳过人工复核直接发布。
    """

    if criterion.review_finding == "conflict":
        return

    bounds = [(span, bound) for span in criterion.spans for bound in threshold_bounds(span.quote)]
    for index, (first_span, first) in enumerate(bounds):
        for second_span, second in bounds[index + 1 :]:
            if first.family != second.family or first.amount != second.amount:
                continue
            if _same_direction(first.direction, second.direction):
                continue
            verdict.findings.append(
                Finding(
                    "CITATION_THRESHOLD_CONFLICT",
                    f"引文对同一指标给出方向相反的阈值（{_direction_text(first.direction)} 与 "
                    f"{_direction_text(second.direction)} {format(first.amount, 'f')}{first.unit}），"
                    "请人工确认后重新标注。",
                    (first_span, second_span),
                )
            )
            return


# ------------------------------------------------------------ AST 与引文比对


_METRIC_FAMILY_BY_KEY: Mapping[str, str] = {
    "rdExpense": "rdExpense",
    "taxableIncome": "taxableIncome",
    "assetsStart": "assets",
    "assetsEnd": "assets",
    "employeeCountStart": "employeeCount",
    "employeeCountEnd": "employeeCount",
    # 2026-10-29 补 rc.3 的金额键：不映射就等于不做引文数值核对（静默放行）。
    # 「研发费用增量」单独成族，不与「研发费用」合并——否则只有研发费用阈值的引文
    # 也能通过增量的条件，属于放宽方向的错判。
    "revenue": "revenue",
    "equityFinancing": "equityFinancing",
    "rdExpenseIncrease": "rdExpenseIncrease",
    # rdExpenseRevenueRatio 有意不映射：引文写「3%」而条件值是 0.03，按金额阈值
    # 比对必然误报。比例的引文核对留给人工复核。
}

_FAMILY_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("研发费用增量", "rdExpenseIncrease"),
    ("研发投入增量", "rdExpenseIncrease"),
    ("营业收入", "revenue"),
    ("股权融资", "equityFinancing"),
    ("研发费用", "rdExpense"),
    ("研发投入", "rdExpense"),
    ("应纳税所得额", "taxableIncome"),
    ("应纳税额", "taxableIncome"),
    ("税额", "taxableIncome"),
    ("税款", "taxableIncome"),
    ("资产总额", "assets"),
    ("资产总计", "assets"),
    ("总资产", "assets"),
    ("从业人数", "employeeCount"),
    ("从业人员", "employeeCount"),
    ("职工人数", "employeeCount"),
    ("职工总数", "employeeCount"),
    ("员工人数", "employeeCount"),
)

_EXCLUSION_CUES = ("行业", "不适用", "排除", "负面清单", "不得属于", "不属于")


def _check_expression_support(criterion: CriterionInput, verdict: CriterionVerdict) -> None:
    bounds = [(span, bound) for span in criterion.spans for bound in threshold_bounds(span.quote)]
    _walk_support(criterion.expression, criterion, verdict, bounds)


def _walk_support(
    node: Any,
    criterion: CriterionInput,
    verdict: CriterionVerdict,
    bounds: list[tuple[SourceSpanCheck, "ThresholdBound"]],
) -> None:
    if not isinstance(node, Mapping):
        return
    op = node.get("op")
    if op in ("and", "or"):
        for child in node.get("children") or []:
            _walk_support(child, criterion, verdict, bounds)
        return
    if op == "not":
        _walk_support(node.get("child"), criterion, verdict, bounds)
        return
    if op == "compare":
        family = _METRIC_FAMILY_BY_KEY.get(str(node.get("factKey")))
        unit = str(node.get("unit"))
        if family and unit in ("CNY", "person"):
            _require_threshold(node, family, criterion, verdict, bounds)
        elif node.get("factKey") == "rdActivity":
            _require_cue(criterion, verdict, "研发活动", "CITATION_RD_ACTIVITY_NOT_FOUND")
        return
    if op == "quarterly_average":
        family = "employeeCount" if node.get("metric") == "employeeCount" else "assets"
        _require_threshold(node, family, criterion, verdict, bounds, metric=node.get("metric"))
        return
    if op == "date":
        _require_date(node.get("value"), criterion, verdict)
        return
    if op == "industry_exclusion":
        excluded = [str(item) for item in node.get("excluded") or []]
        quotes = " ".join(span.quote for span in criterion.spans)
        if any(code in quotes for code in excluded):
            return
        if any(cue in quotes for cue in _EXCLUSION_CUES):
            return
        verdict.findings.append(
            Finding(
                "CITATION_INDUSTRY_NOT_FOUND",
                "引文中找不到行业排除清单或其表述，无法支持该条件。",
                criterion.spans,
            )
        )


def _require_threshold(
    node: Any,
    family: str,
    criterion: CriterionInput,
    verdict: CriterionVerdict,
    bounds: list[tuple[SourceSpanCheck, "ThresholdBound"]],
    *,
    metric: Any = None,
) -> None:
    amount = _to_decimal(node.get("value"))
    comparator = str(node.get("comparator"))
    if amount is None:
        return

    candidates = [(span, bound) for span, bound in bounds if bound.family == family]
    if not candidates:
        label = _family_label(family)
        verdict.findings.append(
            Finding(
                "CITATION_THRESHOLD_NOT_FOUND",
                f"引文中没有找到{label}的数值阈值，无法支持该条件。",
                criterion.spans,
            )
        )
        return

    for span, bound in candidates:
        if bound.amount == amount and _same_direction(bound.direction, comparator):
            return

    span, nearest = candidates[0]
    verdict.findings.append(
        Finding(
            "CITATION_THRESHOLD_MISMATCH",
            f"条件写的是 {_comparator_text(comparator)} {format(amount, 'f')}"
            f"{_unit_label(node.get('unit'))}，但引文写的是 "
            f"{_direction_text(nearest.direction)} {format(nearest.amount, 'f')}{nearest.unit}"
            "（已换算为单位元/人），数值或方向不一致，请复核。",
            (span,),
        )
    )


def _require_date(value: Any, criterion: CriterionInput, verdict: CriterionVerdict) -> None:
    text = str(value or "")
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
    if not match:
        return
    year, month, day = match.groups()
    forms = (
        f"{year}-{month}-{day}",
        f"{year}/{int(month)}/{int(day)}",
        f"{year}年{int(month)}月{int(day)}日",
    )
    quotes = [_normalize(span.quote) for span in criterion.spans]
    if any(form in quote for form in forms for quote in quotes):
        return
    verdict.findings.append(
        Finding("CITATION_DATE_NOT_FOUND", f"引文中找不到日期 {text}，无法支持该条件。", criterion.spans)
    )


def _require_cue(
    criterion: CriterionInput, verdict: CriterionVerdict, cue: str, code: str
) -> None:
    if any(cue in span.quote for span in criterion.spans):
        return
    verdict.findings.append(Finding(code, f"引文中找不到「{cue}」的表述，无法支持该条件。", criterion.spans))


# ------------------------------------------------------------------ 阈值抽取


@dataclass(frozen=True, slots=True)
class ThresholdBound:
    family: str
    amount: Decimal
    direction: str
    unit: str


_DIRECTION_INCLUSIVE_LOWER = ("不低于", "不少于", "不得低于", "至少", "大于等于")
_DIRECTION_INCLUSIVE_UPPER = ("不超过", "不得高于", "不高于", "至多", "小于等于")
_DIRECTION_UPPER = ("低于", "少于", "不足", "未超过")
_DIRECTION_LOWER = ("超过", "高于", "多于", "大于", "达到")

_UNIT_MULTIPLIER = {"万元": Decimal(10000), "亿元": Decimal(100000000), "元": Decimal(1), "人": Decimal(1)}

_THRESHOLD_RE = re.compile(
    r"(不低于|不少于|不得低于|至少|大于等于|不超过|不得高于|不高于|至多|小于等于|低于|少于|不足|未超过|超过|高于|多于|大于|达到)"
    r"\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*(万元|亿元|元|人)"
)

_WHITESPACE = re.compile(r"[\s　]+")


def threshold_bounds(quote: str) -> list[ThresholdBound]:
    """从引文里抽出阈值；识别不到就返回空列表，由存在性检查报「找不到阈值」。"""
    bounds: list[ThresholdBound] = []
    for match in _THRESHOLD_RE.finditer(quote):
        word, number, unit = match.groups()
        direction = _direction_of(word)
        family = _family_near(quote, match.start())
        if direction is None or family is None:
            continue
        try:
            amount = Decimal(number.replace(",", "")) * _UNIT_MULTIPLIER[unit]
        except InvalidOperation:
            continue
        bounds.append(ThresholdBound(family=family, amount=amount, direction=direction, unit=unit))
    return bounds


def _direction_of(word: str) -> str | None:
    if word in _DIRECTION_INCLUSIVE_UPPER:
        return "lte"
    if word in _DIRECTION_INCLUSIVE_LOWER:
        return "gte"
    if word in _DIRECTION_UPPER:
        return "lt"
    if word in _DIRECTION_LOWER:
        return "gt"
    return None


def _family_near(quote: str, position: int) -> str | None:
    window = quote[max(0, position - 20) : position]
    for keyword, family in _FAMILY_KEYWORDS:
        if keyword in window:
            return family
    return None


def _same_direction(first: str, second: str) -> bool:
    return first == second


def _direction_text(direction: str) -> str:
    return {
        "lt": "低于",
        "lte": "不超过",
        "gt": "超过",
        "gte": "不低于",
        "eq": "等于",
        "ne": "不等于",
    }.get(direction, direction)


def _comparator_text(comparator: str) -> str:
    return {
        "lt": "低于",
        "lte": "不超过",
        "gt": "超过",
        "gte": "不低于",
        "eq": "等于",
        "ne": "不等于",
    }.get(comparator, comparator)


def _family_label(family: str) -> str:
    return {
        "rdExpense": "研发费用",
        "taxableIncome": "应纳税所得额",
        "assets": "资产总额",
        "employeeCount": "从业人员数",
        "revenue": "营业收入",
        "equityFinancing": "股权融资",
        "rdExpenseIncrease": "研发费用增量",
    }.get(family, family)


def _unit_label(unit: Any) -> str:
    return {"CNY": " 元", "person": " 人"}.get(str(unit), "")


def _to_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


# ------------------------------------------------------------------ 文本工具


def quote_hash(quote: str) -> str:
    """引文的 sha256（UTF-8 字节），与 ``SourceSpan.textHash`` 对齐。"""
    return hashlib.sha256(quote.encode("utf-8")).hexdigest()


def quote_in_text(quote: str, text: str) -> bool:
    """逐字匹配；只容忍空白差异（PDF 抽取常把换行塞进句子中间）。"""
    return _normalize(quote) in _normalize(text)


def _normalize(value: str) -> str:
    return _WHITESPACE.sub("", value)
