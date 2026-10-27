"""补问排序（FR-05）。

一次只给**一个**全局第一问：同一个「key+期间」被越多的待定政策需要，就越值得先问；
同样多时先问获取成本低的、再问敏感度低的，最后按 key 与日期字典序固定顺序，保证
同样的输入永远得到同样的顺序（评测时可比对排序理由）。

成本与敏感度是 M3 在标注约定里给的人为档位，不由模型猜。M3 尚未给出前，本模块用
:data:`DEFAULT_COST` / :data:`DEFAULT_SENSITIVITY` 作为**本地默认**并把取值写进
``reason``，便于验收时逐条替换。调用方也可以直接传入自己的档位表。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Mapping, Sequence

FactRefKey = tuple[str, date, date]

#: 获取成本档位（1 最低）：越小的越先问。
DEFAULT_COST: Mapping[str, int] = {
    "establishedDate": 1,
    "region": 1,
    "industry": 1,
    "rdActivity": 2,
    "employeeCountStart": 2,
    "employeeCountEnd": 2,
    "assetsStart": 3,
    "assetsEnd": 3,
    "taxableIncome": 3,
    "rdExpense": 3,
}

#: 敏感程度档位（1 最不敏感）：越小的越先问。
DEFAULT_SENSITIVITY: Mapping[str, int] = {
    "establishedDate": 1,
    "region": 1,
    "industry": 2,
    "rdActivity": 2,
    "employeeCountStart": 3,
    "employeeCountEnd": 3,
    "assetsStart": 4,
    "assetsEnd": 4,
    "taxableIncome": 4,
    "rdExpense": 4,
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
}

#: 未知键的兜底档位：既不抢先，也不至于永远排不上。
UNKNOWN_COST = 4
UNKNOWN_SENSITIVITY = 4


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
