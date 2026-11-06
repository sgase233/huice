"""单智能体基线的提示词与回答解析。

这一版提示词**在任何结果跑出来之前定稿**，整轮不改——消融对照要求两个方案的
提示词迭代次数相同，现方案这一轮是零次（不动代码），基线也只能是一次。

给基线的输入与现方案「判断」阶段对齐：政策正文、政策有效期、企业全部事实、
该政策的条件清单（代号 + 原文片段 + 所在条款）。**不给**的是：仲裁后的 AST、
规则求值、引用校验、提问策略。把条件清单给它是**对它有利**的取舍——不给就
对不上 ``criterion_id``，也就没法按条件比较；这一点写在评测报告的局限里。

回答必须是 JSON，每个条件一项：``criterionId`` / ``result`` / ``reason`` /
``quote`` / ``facts``。``quote`` 要求从给定原文里**逐字**摘一句，摘不出就写
``null``——不许改写、不许拼接。
"""

from __future__ import annotations

import json
from typing import Any

from .dataset import Case, Dataset, as_prompt_facts, normalize_for_match

#: 提示词版本。改了措辞就换一个值：报告要写清两个方案各自的版本。
PROMPT_VERSION = "single-agent@1"

#: 与抽取调用的上限一致，两个方案的预算按同一口径比。
MAX_TOKENS = 2048

RESULTS = ("satisfied", "not_satisfied", "insufficient_evidence", "conflict")

SYSTEM_PROMPT = """你是政策申报资格判断助手。给定一项政策的正文、一家企业的全部已知事实，以及该政策下的若干条件，你要逐条判断这家企业是否满足。

判断规则：
1. 只看给定的事实与政策原文，不使用你的常识或对类似政策的记忆。
2. 事实缺失（未列出、或值为 null）一律判 insufficient_evidence，不要用「通常」「一般」去补齐。已列出但标注 verified=false 的事实照常使用，视为企业自报。
3. 条件之间默认「且」；原文明确是「或」的按原文。
4. 政策已过适用期（截止日期早于判断日期）时，conditions 照常判断，但要在 policyExpired 里写 true 并在 summary 里说明。
5. 结论互相矛盾、或原文本身有歧义时判 conflict，并在 reason 里说明矛盾在哪。

每个条件输出一个对象，字段固定：
- criterionId：照抄给定的代号，不得改写。
- result：satisfied / not_satisfied / insufficient_evidence / conflict 之一。
- reason：不超过 60 字，说明依据的是哪条事实或原文的哪一句。
- quote：从上面给出的政策正文里**逐字**摘一句支持你判断的话，不要改写、不要拼接、不要加标点；摘不出来就写 null。
- facts：你实际用到的事实，形如 [{"key": "taxableIncome", "period": "2026-01-01/2026-12-31"}]；一个都没用到就写空数组。

只输出 JSON，不要任何解释性文字或 Markdown 代码块。格式：
{"results": [{"criterionId": "...", "result": "...", "reason": "...", "quote": "..." , "facts": [...]}], "policyExpired": false, "summary": "不超过 80 字"}"""


def build_user_message(dataset: Dataset, case: Case) -> str:
    policy = dataset.policies[(case.policy_code, case.document)]
    enterprise = dataset.enterprises[case.enterprise]
    criteria = [dataset.criterion(case, code) for code in case.criterion_codes]

    lines = ["# 政策", ""]
    lines.append(f"名称：{policy.title}")
    lines.append(f"文号：{policy.document}")
    lines.append(f"类型：{'税收优惠' if policy.policy_type == 'tax' else '财政资金'}")
    window = f"{policy.effective_from.isoformat()} 起"
    if policy.effective_to is not None:
        window += f"，{policy.effective_to.isoformat()} 止"
    if policy.application_close is not None:
        window += f"；申报截止 {policy.application_close.isoformat()}"
    lines.append(f"适用期：{window}")
    lines.append("")
    lines.append("# 政策正文")
    lines.append("")
    lines.append(dataset.source_text(policy))
    lines.append("")
    lines.append("# 企业")
    lines.append("")
    lines.append(f"名称：{enterprise.name}")
    lines.append(f"注册地区代码：{enterprise.region}；行业代码：{enterprise.industry}")
    lines.append("")
    lines.append("已知事实（键 / 值 / 单位 / 期间 / 来源 / 是否已核实）：")
    for fact in as_prompt_facts(enterprise):
        value = "null" if fact["value"] is None else fact["value"]
        verified = "已核实" if fact["verified"] else "未核实"
        lines.append(
            f"- {fact['key']} = {value}（{fact['unit']}，{fact['period']}，"
            f"{fact['source']}，{verified}）"
        )
    lines.append("")
    lines.append(f"# 待判断条件（{len(criteria)} 条）")
    lines.append("")
    for index, criterion in enumerate(criteria, start=1):
        lines.append(f"{index}. {criterion.code}｜{criterion.section}｜原文：{criterion.source_span}")
    lines.append("")
    lines.append("请对这 %d 条各给一个结论，按上面的 JSON 格式输出。" % len(criteria))
    return "\n".join(lines)


class BaselineFormatError(Exception):
    """回答不是约定的 JSON 时抛出；消息里点名缺哪一项，便于重试与留证。"""


def parse_response(text: str, *, expected: list[str]) -> dict[str, Any]:
    """解析回答。缺条件、代号不认识、结论不在四值里，都算格式错误。

    提示词已经要求「只输出 JSON」，但模型仍可能包一层 Markdown 代码块——允许剥掉
    这一层，其余一律不猜。
    """

    body = text.strip()
    if body.startswith("```"):
        first_newline = body.find("\n")
        if first_newline == -1:
            raise BaselineFormatError("回答只有代码块围栏，没有内容")
        body = body[first_newline + 1 :]
        if body.rstrip().endswith("```"):
            body = body.rstrip()[:-3]
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise BaselineFormatError(f"回答不是合法 JSON：{exc.msg}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise BaselineFormatError("回答里没有 results 数组")

    seen: dict[str, dict[str, Any]] = {}
    for item in payload["results"]:
        if not isinstance(item, dict):
            raise BaselineFormatError("results 里有不是对象的元素")
        code = item.get("criterionId")
        if code not in expected:
            raise BaselineFormatError(f"回答里的条件代号「{code}」不在给定的清单里")
        if code in seen:
            raise BaselineFormatError(f"{code} 给了两次结论")
        result = item.get("result")
        if result not in RESULTS:
            raise BaselineFormatError(f"{code} 的 result 是「{result}」，不在四值里")
        quote = item.get("quote")
        if quote is not None and not isinstance(quote, str):
            raise BaselineFormatError(f"{code} 的 quote 既不是字符串也不是 null")
        facts = item.get("facts") or []
        if not isinstance(facts, list) or any(
            not isinstance(fact, dict) or "key" not in fact for fact in facts
        ):
            raise BaselineFormatError(f"{code} 的 facts 不是 [{{key, period}}] 形式")
        seen[code] = {
            "criterionId": code,
            "result": result,
            "reason": str(item.get("reason") or "")[:200],
            "quote": quote,
            "facts": [
                {"key": str(fact["key"]), "period": str(fact.get("period") or "")}
                for fact in facts
            ],
        }

    missing = [code for code in expected if code not in seen]
    if missing:
        raise BaselineFormatError(f"少给了 {len(missing)} 条：{'、'.join(missing)}")

    return {
        "results": [seen[code] for code in expected],
        "policyExpired": bool(payload.get("policyExpired")),
        "summary": str(payload.get("summary") or "")[:400],
    }


def quote_supported(dataset: Dataset, case: Case, code: str, quote: str | None) -> bool:
    """引文是否能在该条件的出处原文里逐字找到（归一化后子串匹配）。

    出处原文不可用的条件（见 ``Dataset.unverifiable_criteria``）不进分母，
    调用方应先筛掉——这里只回答「这句话在不在原文里」。
    """

    if not quote:
        return False
    criterion = dataset.criterion(case, code)
    haystack = normalize_for_match(dataset.verification_text(criterion))
    return normalize_for_match(quote) in haystack
