"""办理清单条目：把已仲裁的材料要求展开成这次判断要交的东西。

一条材料要看三件事才说得清「要不要交、交没交」：

1. **适不适用**：条件交的材料只有政策真的用到它证明的事实键时才出现。政策条件没提
   从业人数，就不该让企业去准备社保凭证——所以触发条件是运行期算的，不是抄一段固定
   文案。没有事实键的条件交（申请书、项目承诺书）判断不了何时需要，按「需人工核对」
   列出而不是省略：静默消失会让人以为这项不用交。
2. **要哪些事实、哪个期间**：随判断变化的键按本次判断期间展开，季度类键（资产负债、
   从业人数）按规则口径展开成四个季度的期初与期末共八个引用；这个展开与
   :func:`app.domain.rules.referenced_facts` 是同一套口径，不另写一份。
3. **到底缺不缺**：见下方四个状态。

四态的理由（M2 在 PR #37 的 P1）：页面上「资料缺失」是一句断言，只有真的知道缺了才
能这么说。没有事实键的材料（申请书、承诺书）本就不由程序判断，只能说「需人工核对」；
政策已经到期或申报窗口已关闭的，材料再齐也用不上，状态要说「政策已关闭」。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

from ..agents.contracts import MaterialRequirement

#: 标题前缀承载「必交 / 条件交」。契约里没有这个字段，口径见 MATERIAL_REQUIREMENTS §4。
REQUIRED_PREFIX = "【必交】"
CONDITIONAL_PREFIX = "【条件交】"

STATUS_PROVIDED = "provided"
STATUS_MISSING = "missing"
STATUS_MANUAL_CHECK = "manual_check"
STATUS_POLICY_CLOSED = "policy_closed"

STATUSES: tuple[str, ...] = (
    STATUS_PROVIDED,
    STATUS_MISSING,
    STATUS_MANUAL_CHECK,
    STATUS_POLICY_CLOSED,
)

FactRefKey = tuple[str, date, date]


@dataclass(frozen=True, slots=True)
class MaterialItem:
    """一条清单条目。``missing`` 是保守的那一侧：``manual_check`` 也算 ``True``。"""

    material_id: str
    title: str
    description: str
    is_required: bool
    fact_refs: tuple[FactRefKey, ...]
    status: str

    @property
    def missing(self) -> bool:
        return self.status in (STATUS_MISSING, STATUS_MANUAL_CHECK)


def _display_title(material: MaterialRequirement) -> str:
    prefix = REQUIRED_PREFIX if material.is_required else CONDITIONAL_PREFIX
    return f"{prefix}{material.title}"


def _display_description(material: MaterialRequirement) -> str:
    if material.condition_desc is None:
        return material.description
    return f"{material.description}（何时需要：{material.condition_desc}）"


def build_items(
    *,
    materials: Sequence[MaterialRequirement],
    referenced: Iterable[FactRefKey],
    known: Iterable[FactRefKey],
    period: tuple[date, date],
    policy_closed: bool,
) -> list[MaterialItem]:
    """按本次判断的实际引用生成条目；顺序按材料代号稳定，与入库顺序无关。

    ``referenced`` 是政策条件实际用到的事实（含季度展开），``known`` 是其中已有取值
    的那些——「有取值」不等于「已核验」，材料只说明事实来源在手上。
    """

    refs = tuple(dict.fromkeys(referenced))
    referenced_keys = {ref[0] for ref in refs}
    known_refs = set(known)
    items: list[MaterialItem] = []

    for material in sorted(materials, key=lambda item: item.material_id):
        keys = material.proves_fact_keys
        triggered = any(key in referenced_keys for key in keys)
        # 必交无条件列出；条件交没被政策触发就不该出现在这份清单里。但「没有键」不等于
        # 「没触发」——申请书这类只有文本条件的材料判断不了，留着让人核对。
        if not material.is_required and keys and not triggered:
            continue

        if keys:
            fact_refs = [ref for ref in refs if ref[0] in keys]
            # 必交材料证明的键可能一条条件都没用到（如成立日期），仍按判断期间列出：
            # 这些事实是登记类材料本来就该有的，缺了也确实要补
            if material.is_required:
                present = {ref[0] for ref in fact_refs}
                fact_refs.extend((key, *period) for key in keys if key not in present)
        else:
            fact_refs = []

        if policy_closed:
            status = STATUS_POLICY_CLOSED
        elif not fact_refs:
            # 没有事实键：不驱动 `verified`，也无从判断企业手上有没有——要人看
            status = STATUS_MANUAL_CHECK
        elif any(ref not in known_refs for ref in fact_refs):
            status = STATUS_MISSING
        else:
            status = STATUS_PROVIDED

        items.append(
            MaterialItem(
                material_id=material.material_id,
                title=_display_title(material),
                description=_display_description(material),
                is_required=material.is_required,
                fact_refs=tuple(fact_refs),
                status=status,
            )
        )
    return items
