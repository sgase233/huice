"""企业画像归一化（API-08 / API-09 / API-10，FR-03）。

画像只做「把用户填的内容变成可求值的事实」这一件事：

* 每个事实键的取值、单位与期间按实现约定 2.3 的字典校验；单位必须与键配套，
  数值只用十进制字符串，**不接受空串、``"null"``、NaN**，``"0"`` 是有效已知值。
* ``value=null`` 表示「未知」，求值时一律得到证据不足，不能当 0。
* 地区与行业在画像年度内各必须有一个非空值，否则画像不成立。
* ``factVersion`` 是完整画像规范化快照的 SHA-256：数组按 key/期间排序、金额按
  Decimal 规范化，所以同样内容永远得到同一个版本，不会因为保存一次就变。
* ``verified`` 只由服务端决定。公开接口写入的事实一律为 ``false``；M1 按 M3 审核
  数据导入时才会写 ``true``。客户端不能自称已核实。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from ..domain.rules import BOOLEAN_KEYS, CODE_KEYS, FACT_UNITS, FactSnapshot
from ..errors import ApiProblem
from ..schemas import EnterpriseFact, EnterpriseInput, FactInput, FactUpdate, Period

DATE_KEYS = frozenset({"establishedDate"})
MONEY_KEYS = frozenset({"taxableIncome", "rdExpense", "assetsStart", "assetsEnd"})
#: 允许为负的事实：应纳税所得额可能为负（亏损），其余金额与人数非负。
SIGNED_KEYS = frozenset({"taxableIncome"})
#: 期间必须等于画像年度的事实。
PROFILE_SCOPED_KEYS = ("region", "industry", "establishedDate")
#: 必须在画像年度内给出非空值的键。
REQUIRED_PROFILE_KEYS = ("region", "industry")

_DECIMAL_RE = re.compile(r"^-?(0|[1-9][0-9]*)(\.[0-9]{1,6})?$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

MONEY_SCALE = 2


@dataclass(frozen=True, slots=True)
class NormalizedFact:
    key: str
    unit: str
    period_start: date
    period_end: date
    source: str
    value: Decimal | date | str | bool | None

    @property
    def ref(self) -> tuple[str, date, date]:
        return (self.key, self.period_start, self.period_end)

    @property
    def known(self) -> bool:
        return self.value is not None


@dataclass(frozen=True, slots=True)
class NormalizedProfile:
    name_synthetic: str
    period_start: date
    period_end: date
    facts: tuple[NormalizedFact, ...]
    fact_version: str


class _FieldError(Exception):
    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.message = message


def normalize_profile(payload: EnterpriseInput) -> NormalizedProfile:
    """校验并归一化一份画像；任何一项不合法都整体 422，不静默丢弃事实。"""

    if payload.period.start_date > payload.period.end_date:
        raise ApiProblem(
            422,
            "VALIDATION_ERROR",
            "企业画像不合法，请按提示修改后重试。",
            {
                "fieldErrors": [
                    {"field": "period", "message": "开始日期不能晚于结束日期。"},
                ]
            },
        )

    errors: list[dict[str, str]] = []
    facts: list[NormalizedFact] = []
    seen: set[tuple[str, date, date]] = set()
    for index, item in enumerate(payload.facts):
        prefix = f"facts[{index}]"
        try:
            fact = _normalize_fact(item, payload.period, prefix)
        except _FieldError as exc:
            errors.append({"field": exc.field, "message": exc.message})
            continue
        if fact.ref in seen:
            errors.append(
                {"field": f"{prefix}.period", "message": "同一事实键与期间只能出现一次。"}
            )
            continue
        seen.add(fact.ref)
        facts.append(fact)

    for key in REQUIRED_PROFILE_KEYS:
        known = [fact for fact in facts if fact.key == key and fact.known]
        if len(known) != 1:
            errors.append(
                {"field": "facts", "message": f"画像年度内 {key} 必须且只能有一个非空值。"}
            )

    if errors:
        raise ApiProblem(
            422,
            "VALIDATION_ERROR",
            "企业画像不合法，请按提示修改后重试。",
            {"fieldErrors": errors},
        )

    ordered = tuple(sorted(facts, key=lambda fact: (fact.key, fact.period_start, fact.period_end)))
    return NormalizedProfile(
        name_synthetic=payload.name_synthetic,
        period_start=payload.period.start_date,
        period_end=payload.period.end_date,
        facts=ordered,
        fact_version=fact_version_of(ordered),
    )


def normalize_fact_update(
    *, key: str, update: FactUpdate, profile_period: tuple[date, date]
) -> NormalizedFact:
    """校验 API-10 的单条事实更新；``key`` 来自路径，期间在 body 中确定。"""

    payload = FactInput(
        key=key,  # type: ignore[arg-type] - 路由已按契约 FactKey 枚举校验过路径参数
        value=update.value,
        unit=update.unit,
        period=update.period,
        source=update.source,
    )
    period = Period(start_date=profile_period[0], end_date=profile_period[1])
    try:
        return _normalize_fact(payload, period, f"facts.{(key)}")
    except _FieldError as exc:
        raise ApiProblem(
            422,
            "VALIDATION_ERROR",
            "事实不合法，请按提示修改后重试。",
            {"fieldErrors": [{"field": exc.field, "message": exc.message}]},
        ) from exc


# ------------------------------------------------------------------ 单项校验


def _normalize_fact(item: FactInput, profile_period: Period, prefix: str) -> NormalizedFact:
    expected_unit = FACT_UNITS.get(item.key)
    if expected_unit is None:
        raise _FieldError(f"{prefix}.key", "未知的事实键。")
    if item.unit != expected_unit:
        raise _FieldError(
            f"{prefix}.unit",
            f"{item.key} 的单位必须是 {expected_unit}，与事实字典不一致。",
        )
    if item.period.start_date > item.period.end_date:
        raise _FieldError(f"{prefix}.period", "开始日期不能晚于结束日期。")
    if item.key in PROFILE_SCOPED_KEYS and (
        item.period.start_date,
        item.period.end_date,
    ) != (profile_period.start_date, profile_period.end_date):
        raise _FieldError(
            f"{prefix}.period",
            f"{item.key} 的期间必须等于画像年度 "
            f"{profile_period.start_date}~{profile_period.end_date}。",
        )

    return NormalizedFact(
        key=item.key,
        unit=expected_unit,
        period_start=item.period.start_date,
        period_end=item.period.end_date,
        source=item.source,
        value=_normalize_value(item.key, item.value, prefix),
    )


def _normalize_value(key: str, value, prefix: str):
    if value is None:
        return None
    if key in CODE_KEYS:
        if not isinstance(value, str) or not value.strip():
            raise _FieldError(f"{prefix}.value", "地区与行业必须是行政区划码或行业码字符串。")
        if value != value.strip():
            raise _FieldError(f"{prefix}.value", "编码两端不能有空白字符。")
        return value
    if key in DATE_KEYS:
        if not isinstance(value, str) or not _DATE_RE.match(value):
            raise _FieldError(f"{prefix}.value", "成立日期必须是 YYYY-MM-DD。")
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise _FieldError(f"{prefix}.value", "成立日期不是有效日期。") from exc
    if key in BOOLEAN_KEYS:
        if not isinstance(value, bool):
            raise _FieldError(f"{prefix}.value", "研发活动标记必须是 JSON 布尔值。")
        return value

    if not isinstance(value, str):
        raise _FieldError(f"{prefix}.value", "数值必须用十进制字符串提交，不接受数字类型或布尔。")
    if not _DECIMAL_RE.match(value):
        raise _FieldError(
            f"{prefix}.value", "数值必须是不带前导零的十进制字符串，最多 6 位小数。"
        )
    try:
        number = Decimal(value)
    except InvalidOperation as exc:  # pragma: no cover - 正则已挡住
        raise _FieldError(f"{prefix}.value", "数值无法解析。") from exc
    if key in MONEY_KEYS and -number.as_tuple().exponent > MONEY_SCALE and number != number.to_integral():
        raise _FieldError(f"{prefix}.value", "金额最多 2 位小数。")
    if key not in SIGNED_KEYS and number < 0:
        raise _FieldError(f"{prefix}.value", "该事实不能为负数。")
    return number


# ------------------------------------------------------------------ 序列化


def fact_value_text(value, *, unit: str = "") -> str | bool | None:
    """事实值的线上形态：金额固定两位小数，人数/日期按字典口径，其余用规范十进制。

    金额统一补到两位，前端不必猜 ``2800000`` 与 ``2800000.00`` 哪个是同一个数；
    这与 ``factVersion`` 的规范化无关——哈希那一步会把尾随零去掉，所以同一内容
    永远不会因为写法不同而换版本。
    """

    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        if unit == "CNY":
            return format(value.quantize(Decimal("0.01")), "f")
        if value == value.to_integral():
            return str(value.quantize(Decimal(1)))
        return format(value.normalize(), "f")
    return str(value)


def to_wire_fact(fact: NormalizedFact, *, verified: bool = False) -> EnterpriseFact:
    return EnterpriseFact(
        key=fact.key,  # type: ignore[arg-type] - 由事实字典保证在枚举内
        value=fact_value_text(fact.value, unit=fact.unit),
        unit=fact.unit,  # type: ignore[arg-type]
        period=Period(start_date=fact.period_start, end_date=fact.period_end),
        source=fact.source,  # type: ignore[arg-type]
        verified=verified,
    )


def fact_snapshots(facts) -> list[FactSnapshot]:
    return [
        FactSnapshot(
            key=fact.key,
            period_start=fact.period_start,
            period_end=fact.period_end,
            unit=fact.unit,
            value=fact.value,
        )
        for fact in facts
    ]


def fact_version_of(facts) -> str:
    """完整画像规范化快照的 SHA-256（实现约定 2.3）。"""

    payload = [
        {
            "key": fact.key,
            "periodStart": fact.period_start.isoformat(),
            "periodEnd": fact.period_end.isoformat(),
            "unit": fact.unit,
            "source": fact.source,
            "value": _canonical_value(fact.value),
        }
        for fact in sorted(facts, key=lambda item: (item.key, item.period_start, item.period_end))
    ]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _canonical_value(value) -> str | bool | None:
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, date):
        return value.isoformat()
    return value
