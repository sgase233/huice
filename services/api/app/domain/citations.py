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

比对刻意留了三处余地，都是「引文表达不到、AST 却必须确定」的地方：

- 比较方向只比到「同一侧」：引文说「1000 万元以下」是上界，AST 写 ``lt`` 还是 ``lte``
  属标注口径，不算抽错；把「不超过 300 万元」写成「不低于」仍会命中。
- ``0`` 阈值不做数值要求（``rdExpense > 0`` 对应的原文是「实际发生的研发费用」，不会
  写出 0）。
- 日期条件接受「两个会计年度（含）以上」这类**时长型**相对表述：绝对日期是按规定换算
  出来的固定值，换算依据记在标注说明里。

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
    trails = _or_branch_trails(criterion.expression)
    for index, (first_span, first) in enumerate(bounds):
        for second_span, second in bounds[index + 1 :]:
            if first.family != second.family or first.amount != second.amount:
                continue
            if not _opposite_trend(first.direction, second.direction):
                continue
            # 「营业收入在 1000 万元以上，或在 1000 万元以下」是同一条件里两条互补的 OR
            # 分支，不是标注没处理完的冲突：AST 已经把它们写成互斥分支了。
            if _branches_are_exclusive(criterion.expression, trails, first, second):
                continue
            verdict.findings.append(
                Finding(
                    "CITATION_THRESHOLD_CONFLICT",
                    f"引文对同一指标给出方向相反的阈值（{_direction_text(first.direction)} 与 "
                    f"{_direction_text(second.direction)} {format(first.amount, 'f')}"
                    f"（原文以「{first.unit}」计）），请人工确认后重新标注。",
                    (first_span, second_span),
                )
            )
            return


def _or_branch_trails(expression: Any) -> dict[int, tuple[tuple[int, int], ...]]:
    """每个原子节点到根路径上「位于哪个 ``or`` 的第几个分支」的轨迹。

    轨迹用 ``(or 节点 id, 分支序号)`` 表示；两个节点在某条 ``or`` 上分支序号不同，
    就说明它们落在互斥分支里，逻辑上不可能同时生效。
    """
    trails: dict[int, tuple[tuple[int, int], ...]] = {}
    if not isinstance(expression, Mapping):
        return trails

    def walk(node: Any, trail: tuple[tuple[int, int], ...]) -> None:
        if not isinstance(node, Mapping):
            return
        op = node.get("op")
        children = node.get("children")
        if op in ("and", "or") and isinstance(children, list):
            for index, child in enumerate(children):
                if op == "or":
                    walk(child, trail + ((id(node), index),))
                else:
                    walk(child, trail)
            return
        trails[id(node)] = trail

    walk(expression, ())
    return trails


def _branches_are_exclusive(
    expression: Any,
    trails: Mapping[int, tuple[tuple[int, int], ...]],
    first: ThresholdBound,
    second: ThresholdBound,
) -> bool:
    """两条引文阈值是否各自落在 AST 的互斥 OR 分支上。"""
    if not isinstance(expression, Mapping) or not trails:
        return False

    def trails_for(bound: ThresholdBound) -> list[tuple[tuple[int, int], ...]]:
        found: list[tuple[tuple[int, int], ...]] = []
        for node in _walk_atoms(expression):
            if node.get("op") != "compare":
                continue
            if str(node.get("unit")) not in ("CNY", "person"):
                continue
            family = _METRIC_FAMILY_BY_KEY.get(str(node.get("factKey")))
            amount = _to_decimal(node.get("value"))
            if family == bound.family and amount == bound.amount:
                found.append(trails.get(id(node), ()))
        return found

    for left in trails_for(first):
        for right in trails_for(second):
            shared = {or_id for or_id, _ in left} & {or_id for or_id, _ in right}
            left_map = dict(left)
            right_map = dict(right)
            if any(left_map[or_id] != right_map[or_id] for or_id in shared):
                return True
    return False


def _walk_atoms(node: Any) -> Iterable[Mapping[str, Any]]:
    if not isinstance(node, Mapping):
        return
    op = node.get("op")
    children = node.get("children")
    if op in ("and", "or") and isinstance(children, list):
        for child in children:
            yield from _walk_atoms(child)
        return
    if op == "not":
        yield from _walk_atoms(node.get("child"))
        return
    yield node


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
    # 0 不是引文会写出来的阈值，而是「存在」这个条件本身（如「实际发生的研发费用」
    # 对应 rdExpense > 0）。要求引文给出数字会让这类条款永远无法通过校验。
    if amount == 0:
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
        if bound.amount == amount and _same_trend(bound.direction, comparator):
            return

    span, nearest = candidates[0]
    verdict.findings.append(
        Finding(
            "CITATION_THRESHOLD_MISMATCH",
            f"条件写的是 {_comparator_text(comparator)} {format(amount, 'f')}"
            f"{_unit_label(node.get('unit'))}，但引文写的是 "
            f"{_direction_text(nearest.direction)} {format(nearest.amount, 'f')}"
            f"（原文以「{nearest.unit}」计，已换算到元/人），数值或方向不一致，请复核。",
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
    # 原文用相对期间表述时（「注册成立时间在两个会计年度（含）以上」「近两年均已申报」），
    # 绝对日期是我们为了可复现而换算出来的固定值，原文里当然找不到。此时以相对表述为
    # 证据，换算依据由标注方记在仲裁说明里，不由校验器反推。
    if any(_has_relative_period(quote) for quote in quotes):
        return
    verdict.findings.append(
        Finding("CITATION_DATE_NOT_FOUND", f"引文中找不到日期 {text}，无法支持该条件。", criterion.spans)
    )


def _has_relative_period(quote: str) -> bool:
    if _RELATIVE_PERIOD_RE.search(quote):
        return True
    return any(cue in quote for cue in _RELATIVE_PERIOD_CUES)


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


_DIRECTION_INCLUSIVE_LOWER = ("不低于", "不少于", "不得低于", "至少", "大于等于", "达到", "达")
_DIRECTION_INCLUSIVE_UPPER = ("不超过", "不得高于", "不高于", "至多", "小于等于")
_DIRECTION_UPPER = ("低于", "少于", "不足", "未超过")
#: 「达到/达 200 万元」在政策文本里是含本数的下限（「达 2 年以上」同理），归入下限而不是
#: 严格大于：2026-10-29 修 SRDI-002 时发现原来的归属会让「达到 2000 万元以上」抽成 gt，
#: 与 AST 的 gte 无端报不一致。
_DIRECTION_LOWER = ("超过", "高于", "多于", "大于")

_UNIT_MULTIPLIER = {"万元": Decimal(10000), "亿元": Decimal(100000000), "元": Decimal(1), "人": Decimal(1)}

#: 中文阈值的两种书写顺序。前置式「不低于 100 万元」与后置式「100 万元（含）以上」
#: 在地方通知里都很常见；只认前置式会让「上年度营业收入总额在 1000 万元以上」这类句子
#: 抽不出阈值，进而把正确的 AST 判成「引文没有数值阈值」。
_DIRECTION_WORDS = (
    "不低于|不少于|不得低于|至少|大于等于|不超过|不得高于|不高于|至多|小于等于"
    "|低于|少于|不足|未超过|超过|高于|多于|大于|达到|达"
)
_THRESHOLD_PREFIX_RE = re.compile(
    rf"({_DIRECTION_WORDS})\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*(万元|亿元|元|人)"
)
_THRESHOLD_SUFFIX_RE = re.compile(
    r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*(万元|亿元|元|人)"
    r"\s*(?:（含）|\(含\))?\s*(及以上|以上|及以下|以下|以内)"
)

_WHITESPACE = re.compile(r"[\s　]+")

#: 「两个会计年度（含）以上」「近两年」这类**时长型**相对表述：原文没有绝对日期，AST 里
#: 却是绝对日期（引擎必须用可复现的固定值）。引文出现这些表述即视为支持，绝对日期由
#: 标注方按申报年度换算并记在仲裁说明里，而不是让校验去猜。
#:
#: 刻意不收「上年度」「近两年均已申报」里的期间词：「上年度」是期间不是时长，把它算作
#: 日期证据会把毫不相关的引文也算成支持。
_RELATIVE_PERIOD_CUES = (
    "会计年度",
    "连续两年",
    "连续2年",
    "满两年",
    "满2年",
    "近两年",
    "近2年",
    "近三年",
    "近3年",
    "近五年",
    "近5年",
)
#: 上面是逐条枚举的固定写法，这里是同一类表述的数字/汉字通配形式——「满3年」
#: 「连续三年」「从事该细分市场不少于5年」「近10年」都会命中。之所以要放宽：
#: rc.4 的 ``specificMarketSince`` 记的是日期，而原文几乎只会写年数；枚举式清单
#: 只能覆盖到政策原文恰好用了哪几个写法，换个数字就漏。
#:
#: 前缀词是判定关键——必须是表示时长的词（满/连续/近/超过/不少于…），
#: 「上年度」「本年度」这类**期间**词不在其中，因此不会被误当作时长证据。
_RELATIVE_PERIOD_RE = re.compile(
    r"(?:满|连续|近|超过|不少于|不低于|至少)\s*[一二三四五六七八九十两0-9]+\s*个?\s*(?:会计)?年(?:度)?"
)


def threshold_bounds(quote: str) -> list[ThresholdBound]:
    """从引文里抽出阈值；识别不到就返回空列表，由存在性检查报「找不到阈值」。"""
    extractions: list[tuple[int, int, str, str, str | None]] = []
    for match in _THRESHOLD_PREFIX_RE.finditer(quote):
        extractions.append(
            (match.start(), match.end(), match.group(2), match.group(3), _direction_of(match.group(1)))
        )
    for match in _THRESHOLD_SUFFIX_RE.finditer(quote):
        direction = "gte" if match.group(3) in ("及以上", "以上") else "lte"
        extractions.append(
            (match.start(), match.end(), match.group(1), match.group(2), direction)
        )

    bounds: list[ThresholdBound] = []
    taken: list[tuple[int, int]] = []
    # 「达到 200 万元（含）以上」会被前置式（达 200 万元）与后置式（200 万元以上）各命中
    # 一次，两段区间重叠；只保留靠前的那条，避免同一个阈值被数两次。
    for start, end, number, unit, direction in sorted(extractions):
        if direction is None or any(start < other_end and other_start < end for other_start, other_end in taken):
            continue
        family = _family_near(quote, start)
        if family is None:
            continue
        try:
            amount = Decimal(number.replace(",", "")) * _UNIT_MULTIPLIER[unit]
        except InvalidOperation:
            continue
        taken.append((start, end))
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


_LOWER_BOUNDS = frozenset({"gt", "gte"})
_UPPER_BOUNDS = frozenset({"lt", "lte"})


def _same_trend(first: str, second: str) -> bool:
    """两个比较方向是否指向同一侧。

    引文只能可靠表达「上界还是下界」；「1000 万元以下」是否含本数、AST 写 ``lt`` 还是
    ``lte``，属于标注口径而非引文事实，因此比到「同侧」即算一致。方向抽反（把「不超过
    300 万元」抽成「不低于」）仍然会命中，那是这条检查真正要防的错。
    """
    if first in _LOWER_BOUNDS and second in _LOWER_BOUNDS:
        return True
    if first in _UPPER_BOUNDS and second in _UPPER_BOUNDS:
        return True
    return first == second


def _opposite_trend(first: str, second: str) -> bool:
    return (_same_trend(first, "gt") and _same_trend(second, "lt")) or (
        _same_trend(second, "gt") and _same_trend(first, "lt")
    )


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
