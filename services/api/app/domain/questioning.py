"""补问排序（FR-05）。

一次只给**一个**全局第一问：同一个「key+期间」被越多的待定政策需要，就越值得先问；
同样多时先问获取成本低的、再问敏感度低的，最后按 key 与日期字典序固定顺序，保证
同样的输入永远得到同样的顺序（评测时可比对排序理由）。

成本与敏感度是 M3 在标注约定里给的人为档位，不由模型猜。2026-10-28 起两张表的
取值对齐 M3《条款标注规范》§5（1–5 档），本模块的 :data:`DEFAULT_COST` /
:data:`DEFAULT_SENSITIVITY` 是**唯一实现口径**：改档位只改这里，并把整数写进
``reason`` 便于验收比对。调用方也可以直接传入自己的档位表。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Mapping, Sequence

FactRefKey = tuple[str, date, date]

#: 获取成本档位（1 最低）：越小的越先问。
#: 取值对齐 M3 的《条款标注规范》§5（2026-10-28 定稿，1–5 档）。
DEFAULT_COST: Mapping[str, int] = {
    "establishedDate": 1,
    "region": 1,
    "industry": 1,
    "rdActivity": 1,
    "taxableIncome": 2,
    "rdExpense": 2,
    "employeeCountStart": 3,
    "employeeCountEnd": 3,
    "assetsStart": 3,
    "assetsEnd": 3,
    # 2026-10-28 新增键的档位：§5 尚未逐键给出，以下为 M1 提案，待 M3 确认后同步。
    "revenue": 2,
    "equityFinancing": 3,
    "rdExpenseIncrease": 2,
    "rdExpenseRevenueRatio": 2,
    "rdAddDeductionClaimed": 2,
    "creditRecord": 3,
}

#: 敏感程度档位（1 最不敏感）：越小的越先问。
DEFAULT_SENSITIVITY: Mapping[str, int] = {
    "establishedDate": 1,
    "region": 1,
    "industry": 1,
    "employeeCountStart": 2,
    "employeeCountEnd": 2,
    "assetsStart": 2,
    "assetsEnd": 2,
    "taxableIncome": 3,
    "rdExpense": 3,
    "rdActivity": 4,
    # 同上：M1 提案，待 M3 确认。
    "revenue": 3,
    "equityFinancing": 4,
    "rdExpenseIncrease": 3,
    "rdExpenseRevenueRatio": 3,
    "rdAddDeductionClaimed": 3,
    "creditRecord": 4,
}

PROMPTS: Mapping[str, str] = {
    "region": "请补充企业登记所在地区（行政区划码，例如 440300）。",
    "industry": "请补充企业所属行业码（用于判断是否属于排除行业）。",
    "establishedDate": "请补充企业成立日期。",
    "taxableIncome": "请补充本次判断所属年度的应纳税所得额（元）。",
    "rdExpense": "请补充本次判断所属年度的研发费用金额（元）。",
    "rdActivity": "请确认该年度是否有研发活动。",
    "employeeCountStart": "请补充该季度期初的从业人数。",
    "employeeCountEnd": "请补充该季度期末的从业人数。",
    "assetsStart": "请补充该季度期初的资产总额（元）。",
    "assetsEnd": "请补充该季度期末的资产总额（元）。",
    "revenue": "请补充该年度的营业收入总额（元）。",
    "equityFinancing": "请补充近两年新增股权融资总额（元，按合格机构投资者实缴额）。",
    "rdExpenseIncrease": "请补充本年度较上年度的研发费用增量（元，下降时填负数）。",
    "rdExpenseRevenueRatio": "请补充研发费用占营业收入的比重（小数，例如 3% 填 0.03）。",
    "rdAddDeductionClaimed": "请确认近两年是否均已申报享受研发费用加计扣除。",
    "creditRecord": "请确认是否存在限制申请财政性资金的情形（失信惩戒、超期未验收未退款等）。",
}

#: 未知键的兜底档位：1–5 量表下取最差档，既不抢先，也不至于永远排不上。
#: 与 §5 的档位含义一致：档位越小越先问，未知键垫底。
UNKNOWN_COST = 5
UNKNOWN_SENSITIVITY = 5


@dataclass(frozen=True, slots=True)
class Question:
    fact_ref: FactRefKey
    prompt: str
    reason: str
    rank: int = 1


@dataclass(frozen=True, slots=True)
class PendingAssessment:
    """参与排序的待定结果：只有 ``insufficient_evidence`` 才缺事实。"""

    policy_id: object
    result: str
    missing: Sequence[FactRefKey]


def choose_next_question(
    assessments: Iterable[PendingAssessment],
    skipped_fact_refs: Iterable[FactRefKey] = (),
    *,
    costs: Mapping[str, int] | None = None,
    sensitivity: Mapping[str, int] | None = None,
) -> Question | None:
    """返回全局第一问；没有待补事实（或被全部跳过）时返回 ``None``。"""

    skipped = {_ref_key(ref) for ref in skipped_fact_refs}
    cost_table = costs if costs is not None else DEFAULT_COST
    sense_table = sensitivity if sensitivity is not None else DEFAULT_SENSITIVITY

    # ref -> 受影响的待定政策集合
    affected: dict[FactRefKey, set[object]] = {}
    for assessment in assessments:
        if assessment.result != "insufficient_evidence":
            continue
        for ref in assessment.missing:
            key = _ref_key(ref)
            if key in skipped:
                continue
            affected.setdefault(key, set()).add(assessment.policy_id)

    if not affected:
        return None

    def sort_key(ref: FactRefKey):
        key, start, end = ref
        return (
            -len(affected[ref]),
            cost_table.get(key, UNKNOWN_COST),
            sense_table.get(key, UNKNOWN_SENSITIVITY),
            key,
            start,
            end,
        )

    best = min(affected, key=sort_key)
    key, start, end = best
    count = len(affected[best])
    reason = (
        f"补齐该事实可推进 {count} 项待定政策；获取成本档位 "
        f"{cost_table.get(key, UNKNOWN_COST)}、敏感程度档位 "
        f"{sense_table.get(key, UNKNOWN_SENSITIVITY)}。"
    )
    return Question(
        fact_ref=best,
        prompt=PROMPTS.get(key, f"请补充事实 {key}（{start.isoformat()} 至 {end.isoformat()}）。"),
        reason=reason,
        rank=1,
    )


def _ref_key(ref) -> FactRefKey:
    if isinstance(ref, tuple):
        return ref
    return (ref.key, ref.period_start, ref.period_end)
