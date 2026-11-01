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
    # 2026-10-29 起 §5 逐键定稿（M3 采纳 M1 提案，见 ANNOTATION_GUIDE §5）。
    "revenue": 2,
    "equityFinancing": 3,
    "rdExpenseIncrease": 2,
    "rdExpenseRevenueRatio": 2,
    "rdAddDeductionClaimed": 2,
    "creditRecord": 3,
    # 2026-10-29 扩展（rc.4）：细分市场起始日期与「成立日期」同级（都是企业自述的
    # 一个日期，翻营业执照即可）；独立法人资格是营业执照上的一行，成本与 rdActivity 同档。
    "specificMarketSince": 1,
    "independentLegalEntity": 1,
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
    # 同上：2026-10-29 定稿。
    "revenue": 3,
    "equityFinancing": 4,
    "rdExpenseIncrease": 3,
    "rdExpenseRevenueRatio": 3,
    "rdAddDeductionClaimed": 3,
    "creditRecord": 4,
    # 细分市场的经营范围可能透露业务方向，比成立日期敏感一档；法人资格是公开登记信息。
    "specificMarketSince": 2,
    "independentLegalEntity": 1,
}

#: 补问文案：**只引用事实期间（「该期间」），不写任何相对年份**。
#:
#: 这些文案是按 key 静态定义的，本身并不知道这次补问的 ``factRef.period`` 是哪一年；
#: 而界面会把期间单独展示出来。文案里一旦出现「本年度 / 上年度 / 近两年 / 本次判断
#: 所属年度」这类相对词，就会和期间打架：用户可能按文案字面填「本年 − 上年」，而事实
#: 却记在「上年度」那一行上。错年份的值仍是合法十进制数，精度、符号、范围校验全过，
#: 于是判错结果也不会报错——是 fail-wrong，不是 fail-safe。增量类还须写明公式，
#: 让「标注方预计算」与「用户自己算」得到同一个数。
PROMPTS: Mapping[str, str] = {
    "region": "请补充企业登记所在地区（行政区划码，例如 440300）。",
    "industry": "请补充企业所属行业码（用于判断是否属于排除行业）。",
    "establishedDate": "请补充企业成立日期。",
    "taxableIncome": "请补充该期间的应纳税所得额（元）。",
    "rdExpense": "请补充该期间的研发费用金额（元）。",
    "rdActivity": "请确认该期间内是否有研发活动。",
    "employeeCountStart": "请补充该期间期初的从业人数。",
    "employeeCountEnd": "请补充该期间期末的从业人数。",
    "assetsStart": "请补充该期间期初的资产总额（元）。",
    "assetsEnd": "请补充该期间期末的资产总额（元）。",
    "revenue": "请补充该期间的营业收入总额（元）。",
    "equityFinancing": "请补充该期间内新增的股权融资总额（元，按合格机构投资者实缴额）。",
    "rdExpenseIncrease": (
        "请补充该期间的研发费用增量（＝该期间的研发费用 − 上一期间的研发费用，"
        "按元填写；下降时填负数）。"
    ),
    "rdExpenseRevenueRatio": "请补充该期间的研发费用占营业收入比重（小数，例如 3% 填 0.03）。",
    "rdAddDeductionClaimed": (
        "请确认该期间内各年度是否均已申报享受研发费用加计扣除"
        "（期间内每个年度都已申报填「是」；已确认有年度未申报填「否」；"
        "尚未申报或不确定请跳过，按未知处理，不要填「否」）。"
    ),
    # 布尔语义按 M3《条款标注规范》§4.1：true = 诚信记录良好。问法必须与之一致，
    # 否则「是」被存成 true 会把「有限制情形」记成「记录良好」，规则反向判断。
    "creditRecord": (
        "请确认该期间的诚信记录是否良好（无失信惩戒、无超期未验收未退款等限制申请"
        "财政性资金的情形）——良好填「是」，否则填「否」。"
    ),
    "specificMarketSince": (
        "请补充企业开始从事该细分市场的日期（YYYY-MM-DD）。"
        "这是起始日期，不是年数——年限由引擎按判断时点计算。"
    ),
    # 布尔语义：true = 具有独立法人资格。问法必须同向，否则「是」被存成 true
    # 会把「不是独立法人（如分公司）」记成「是独立法人」。
    "independentLegalEntity": (
        "请确认企业是否具有独立法人资格（营业执照上的企业法人，分公司等非独立核算主体"
        "填「否」）——具有填「是」，否则填「否」。"
    ),
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
