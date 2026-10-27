"""条款抽取（API-05）：给出候选条件与原文跨度，从不直接发布。

两条路径：

* ``fixture``（默认，离线）：在已存正文上按句式规则抽「应纳税所得额 / 研发费用」阈值、
  「YYYY 年 M 月 D 日前成立」日期条件。规则只覆盖能可靠抽出的写法，其余一律记为
  ``needs_review``，交给人工标注（M3 本来就是人工复核 AST 的来源）。
* ``deepseek``：让模型给出候选 JSON，但模型产物只当**建议**——结构、单位、引用一律
  再走服务端校验；解析不出来就退回 ``needs_review``，不猜。

抽取结果必须再经过 ``domain/citations.py`` 的引用校验才能成为可发布条款；本模块不写
``validationStatus``。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence
from uuid import UUID, uuid4

from ..domain.citations import SourceSpanCheck, quote_hash
from ..domain.rules import validate_expression
from ..integrations.llm import LlmClient, LlmResult
from ..schemas import ModelUsage

EXTRACTION_RULE_VERSION = "rule:extraction@1"

MAX_SENTENCE_LENGTH = 2000

#: 只抽取能唯一映射到事实字典的指标；资产与人数分不清期初/期末，留给人工标注。
_FAMILY_TO_KEY = {
    "taxableIncome": "taxableIncome",
    "rdExpense": "rdExpense",
}

_SENTENCE_SPLIT = re.compile(r"[^。；;\n]+[。；;]?")
_HEADING = re.compile(r"第[一二三四五六七八九十百零〇0-9]+条")

_THRESHOLD_RE = re.compile(
    r"(?P<metric>应纳税所得额|研发费用|研发投入|应纳税额)\s*"
    r"(?P<conn>不超过|不得高于|不高于|不低于|不少于|低于|少于|超过|高于|达到|未超过)?\s*"
    r"(?P<value>[0-9][0-9,]*(?:\.[0-9]+)?)\s*(?P<unit>万元|亿元|元)"
)

_DATE_RE = re.compile(
    r"(?P<year>\d{4})\s*年\s*(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*日?\s*(?:以前|之前|前)\s*成立"
)

_EXCLUSION_CUES = ("不适用", "排除", "负面清单", "不得属于", "不属于")

_UNIT_MULTIPLIER = {"万元": 10000, "亿元": 100000000, "元": 1}

_CONNECTION_TO_COMPARATOR = {
    "不超过": "lte",
    "不得高于": "lte",
    "不高于": "lte",
    "不低于": "gte",
    "不少于": "gte",
    "低于": "lt",
    "少于": "lt",
    "未超过": "lte",
    "超过": "gt",
    "高于": "gt",
    "达到": "gte",
}


@dataclass(frozen=True, slots=True)
class ExtractionSource:
    id: UUID
    source_url: str
    text: str
    kind: str = "body"


@dataclass(slots=True)
class ExtractedCriterion:
    id: UUID
    title: str
    expression: dict[str, Any]
    spans: tuple[SourceSpanCheck, ...]
    review_finding: str = "none"


@dataclass(frozen=True, slots=True)
class ExtractionFinding:
    code: str
    message: str
    source_spans: tuple[SourceSpanCheck, ...] = ()


@dataclass(slots=True)
class ExtractionOutcome:
    criteria: list[ExtractedCriterion] = field(default_factory=list)
    findings: list[ExtractionFinding] = field(default_factory=list)
    usage: ModelUsage | None = None
    status: str = "ok"

    def add(self, code: str, message: str, spans: tuple[SourceSpanCheck, ...] = ()) -> None:
        self.findings.append(ExtractionFinding(code, message, spans))
        self.status = "needs_review"


def extract_criteria(
    *,
    version: str,
    sources: Sequence[ExtractionSource],
    llm: LlmClient | None = None,
    model_version: str | None = None,
) -> ExtractionOutcome:
    """按已存正文抽取候选条款。没有正文时返回 ``needs_review``，不凭空生成。"""
    body = _body_source(sources)
    if body is None or not body.text.strip():
        outcome = ExtractionOutcome(usage=_usage(None, None, 0, 0, 0))
        outcome.add("EXTRACTION_NO_BODY", "该版本没有可用于抽取的正文，请先补充原文。")
        return outcome

    started = time.monotonic()
    if llm is not None and getattr(llm, "mode", "fixture") == "deepseek":
        return _extract_with_model(body, version=version, llm=llm, started=started)

    outcome = _extract_with_rules(body, version=version)
    outcome.usage = _usage(
        None, model_version or EXTRACTION_RULE_VERSION, 0, 0, _elapsed_ms(started)
    )
    return outcome


# ---------------------------------------------------------------- 规则抽取


def _extract_with_rules(body: ExtractionSource, *, version: str) -> ExtractionOutcome:
    outcome = ExtractionOutcome()
    seen: set[tuple[str, str, str]] = set()

    for index, (section, sentence) in enumerate(_iter_sentences(body.text), start=1):
        span = _span_for(body, version=version, section=section or f"第{index}句", quote=sentence)

        match = _THRESHOLD_RE.search(sentence)
        if match and match.group("conn"):
            criterion = _threshold_criterion(match, span, sentence)
            if criterion is not None:
                key = (
                    criterion.expression["factKey"],
                    criterion.expression["comparator"],
                    criterion.expression["value"],
                )
                if key not in seen:
                    seen.add(key)
                    outcome.criteria.append(criterion)
                continue

        date_match = _DATE_RE.search(sentence)
        if date_match:
            criterion = _date_criterion(date_match, span)
            key = (
                criterion.expression["factKey"],
                criterion.expression["comparator"],
                criterion.expression["value"],
            )
            if key not in seen:
                seen.add(key)
                outcome.criteria.append(criterion)
            continue

        if any(cue in sentence for cue in _EXCLUSION_CUES) and "行业" in sentence:
            outcome.add(
                "EXTRACTION_INDUSTRY_NEEDS_MANUAL",
                "识别到行业排除表述，但无法自动映射为行业码，请人工标注。",
                (span,),
            )

    if not outcome.criteria and outcome.status == "ok":
        outcome.add(
            "EXTRACTION_NO_CRITERIA",
            "未从正文中识别出可结构化的条件，请人工录入条款或补充更完整的正文。",
        )
    return outcome


def _threshold_criterion(
    match: re.Match[str], span: SourceSpanCheck, sentence: str
) -> ExtractedCriterion | None:
    metric = match.group("metric")
    family = _family_of(metric)
    fact_key = _FAMILY_TO_KEY.get(family)
    if fact_key is None:
        return None

    comparator = _CONNECTION_TO_COMPARATOR.get(match.group("conn"))
    if comparator is None:
        return None

    try:
        amount = float(match.group("value").replace(",", "")) * _UNIT_MULTIPLIER[match.group("unit")]
    except ValueError:
        return None

    expression = {
        "op": "compare",
        "factKey": fact_key,
        "period": "assessment",
        "comparator": comparator,
        "value": _decimal_text(amount),
        "unit": "CNY" if fact_key in ("taxableIncome", "rdExpense") else "person",
    }
    if validate_expression(expression):
        return None
    return ExtractedCriterion(
        id=uuid4(),
        title=_title_of(sentence, fallback=f"{metric}{match.group('conn')}{match.group('value')}"),
        expression=expression,
        spans=(span,),
    )


def _date_criterion(match: re.Match[str], span: SourceSpanCheck) -> ExtractedCriterion:
    year, month, day = (int(match.group(name)) for name in ("year", "month", "day"))
    expression = {
        "op": "date",
        "factKey": "establishedDate",
        "period": "assessment",
        "comparator": "lte",
        "value": f"{year:04d}-{month:02d}-{day:02d}",
    }
    return ExtractedCriterion(
        id=uuid4(),
        title=_title_of(span.quote, fallback="成立日期要求"),
        expression=expression,
        spans=(span,),
    )


def _family_of(metric: str) -> str:
    if metric in ("研发费用", "研发投入"):
        return "rdExpense"
    return "taxableIncome"


def _title_of(sentence: str, *, fallback: str) -> str:
    cleaned = sentence.strip()
    if 0 < len(cleaned) <= 40:
        return cleaned
    return fallback[:40]


def _iter_sentences(text: str):
    """按句切分，并把最近的「第 X 条」当作章节名。"""
    section: str | None = None
    for match in _SENTENCE_SPLIT.finditer(text):
        sentence = match.group(0).strip()
        if not sentence:
            continue
        heading = _HEADING.search(sentence)
        if heading:
            section = heading.group(0)
        yield section, sentence[:MAX_SENTENCE_LENGTH]


def _span_for(
    body: ExtractionSource, *, version: str, section: str, quote: str
) -> SourceSpanCheck:
    return SourceSpanCheck(
        source_id=body.id,
        source_url=body.source_url,
        source_version=version,
        page=None,
        section=section,
        quote=quote,
        text_hash=quote_hash(quote),
    )


def _body_source(sources: Sequence[ExtractionSource]) -> ExtractionSource | None:
    for source in sources:
        if source.kind == "body":
            return source
    return sources[0] if sources else None


# ---------------------------------------------------------------- 模型抽取


_SYSTEM_PROMPT = (
    "你是政策条款结构化助手。只输出 JSON 数组，不要解释。"
    "每项形如 {\"title\":string,\"expression\":object,\"quote\":string,\"section\":string}。"
    "expression 只能是 and/or/not/compare/date/industry_exclusion/quarterly_average 之一，"
    "factKey 只能取 region/industry/establishedDate/taxableIncome/rdExpense/"
    "assetsStart/assetsEnd/employeeCountStart/employeeCountEnd/rdActivity；"
    "数值用十进制字符串，unit 用 CNY/person/code/date/boolean。"
    "quote 必须是原文中一字不差的片段，找不到原文就不要输出该项。"
)


def _extract_with_model(
    body: ExtractionSource, *, version: str, llm: LlmClient, started: float
) -> ExtractionOutcome:
    outcome = ExtractionOutcome()
    try:
        result = llm.complete(system=_SYSTEM_PROMPT, user=body.text[:20000], max_tokens=2048)
    except Exception:  # noqa: BLE001 - 模型错误由调用方统一映射为 503
        raise

    outcome.usage = _usage(result, result.model, result.prompt_tokens, result.completion_tokens, result.elapsed_ms)
    items = _parse_model_json(result.text)
    if items is None:
        outcome.add("EXTRACTION_OUTPUT_INVALID", "模型返回的内容不是约定的 JSON 数组，请人工录入条款。")
        return outcome

    seen: set[str] = set()
    for item in items:
        if not isinstance(item, Mapping):
            continue
        title = str(item.get("title") or "").strip()
        expression = item.get("expression")
        quote = str(item.get("quote") or "").strip()
        if not title or not isinstance(expression, Mapping) or not quote:
            outcome.add("EXTRACTION_ITEM_INCOMPLETE", "模型返回的条款缺少标题、表达式或引文。")
            continue
        if quote not in body.text:
            outcome.add(
                "EXTRACTION_QUOTE_NOT_IN_BODY",
                "模型给出的引文在正文中找不到，已丢弃该项。",
            )
            continue
        problems = validate_expression(expression)
        if problems:
            outcome.add(
                "EXTRACTION_EXPRESSION_INVALID",
                f"模型给出的表达式不合契约：{problems[0].message}",
            )
            continue
        fingerprint = json.dumps(expression, ensure_ascii=False, sort_keys=True)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        section = str(item.get("section") or "").strip() or "模型抽取"
        outcome.criteria.append(
            ExtractedCriterion(
                id=uuid4(),
                title=title[:200],
                expression=dict(expression),
                spans=(
                    _span_for(body, version=version, section=section[:120], quote=quote[:MAX_SENTENCE_LENGTH]),
                ),
            )
        )

    if not outcome.criteria and outcome.status == "ok":
        outcome.add("EXTRACTION_NO_CRITERIA", "模型没有给出可用条款，请人工录入。")
    return outcome


def _parse_model_json(text: str) -> list[Any] | None:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end <= start:
        return None
    try:
        payload = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, list) else None


# ------------------------------------------------------------------ 小工具


def _usage(result: LlmResult | None, model_version: str | None, prompt: int, completion: int, elapsed: int) -> ModelUsage:
    return ModelUsage(
        mode=getattr(result, "mode", None) or ("deepseek" if result else "fixture"),
        model_version=model_version,
        input_tokens=max(0, int(prompt)),
        output_tokens=max(0, int(completion)),
        elapsed_ms=max(0, int(elapsed)),
    )


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _decimal_text(value: float) -> str:
    text = format(value, ".2f")
    return text.rstrip("0").rstrip(".") if "." in text else text
