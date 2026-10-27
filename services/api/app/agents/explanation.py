"""结果解释（FR-06/FR-08）。

解释只**改措辞**：四态、缺失事实与补问都由规则与 ``questioning`` 决定，本模块不改
结论、也不自行选问题。默认路径不联网：用确定性中文说明（``saved``/``fixture``）；
只有配置了 DeepSeek 且调用成功时才用模型措辞（``deepseek``）。模型失败时规则结果照常
返回，改用已存说明并给出降级警告，不把整次判断伪装成模型成功。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from ..integrations.llm import MODEL_UNAVAILABLE_MESSAGE, LlmClient

RESULT_LABELS = {
    "satisfied": "满足",
    "not_satisfied": "不满足",
    "insufficient_evidence": "证据不足",
    "conflict": "条款冲突待复核",
}

HANDLING_LABELS = {
    "tax_self_assessment": "税收优惠自行判别、申报享受",
    "grant_application": "资金项目按通知提交申报材料",
}

FIXTURE_WARNING = "当前为样例模式：解释由规则生成，未调用模型。"

_SYSTEM_PROMPT = (
    "你是政策申报助理，只做中文改写。不得改变结论、不得新增事实、不得给出税务意见。"
    "用两到三句话说明结论与依据，提到缺失事实时只说明还需补充什么。"
)


@dataclass(frozen=True, slots=True)
class CriterionLine:
    title: str
    result: str
    reason: str


@dataclass(slots=True)
class Explanation:
    text: str
    mode: str
    warnings: list[str] = field(default_factory=list)


def explain(
    *,
    policy_title: str,
    result: str,
    criteria: Sequence[CriterionLine],
    handling_mode: str,
    missing_keys: Sequence[str] = (),
    llm: LlmClient | None = None,
) -> Explanation:
    """给出与四态一致的解释。``mode`` 只反映解释来源，不反映判断来源。"""

    baseline = _deterministic(policy_title, result, criteria, handling_mode, missing_keys)

    if llm is None or getattr(llm, "mode", "fixture") != "deepseek":
        return Explanation(baseline, "fixture", [FIXTURE_WARNING])

    try:
        completion = llm.complete(
            system=_SYSTEM_PROMPT,
            user=f"政策：{policy_title}\n结论：{RESULT_LABELS.get(result, result)}\n依据：{baseline}",
            max_tokens=300,
        )
    except Exception:  # noqa: BLE001 - 模型失败一律降级，不影响规则结果
        return Explanation(baseline, "saved", [MODEL_UNAVAILABLE_MESSAGE])

    text = completion.text.strip()
    if not text:
        return Explanation(baseline, "saved", [MODEL_UNAVAILABLE_MESSAGE])
    return Explanation(text, "deepseek", ["解释由模型改写，结论以规则结果为准。"])


def _deterministic(
    policy_title: str,
    result: str,
    criteria: Sequence[CriterionLine],
    handling_mode: str,
    missing_keys: Sequence[str],
) -> str:
    label = RESULT_LABELS.get(result, result)
    parts = [f"《{policy_title}》本次判断为「{label}」。"]

    failed = [line for line in criteria if line.result == "not_satisfied"]
    if failed:
        parts.append("不满足的条款：" + "；".join(f"{line.title}（{line.reason}）" for line in failed))

    conflicting = [line for line in criteria if line.result == "conflict"]
    if conflicting:
        parts.append("需人工复核的冲突条款：" + "、".join(line.title for line in conflicting))

    if missing_keys:
        parts.append("仍需补充的事实：" + "、".join(dict.fromkeys(missing_keys)) + "。补齐后可重新判断。")

    if result == "satisfied":
        parts.append(HANDLING_LABELS.get(handling_mode, "按政策通知办理") + "。")
    else:
        parts.append("本结论不构成税务或法律意见。")

    return "".join(parts)
