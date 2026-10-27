"""报告渲染（API-13）。

报告是**历史快照的转写**，不重新求值：四态、条件结果、证据、清单与解释都来自 run 本身。
只有「截至下载时刻的有效性提示」是按当前时间与失效事件重算的读取视图——这正是下载
那一刻用户真正需要看到的东西，所以它在正文之前单独成段。

Markdown 里不写提示词、密钥或原始上传文件；引用只放已审核的原文片段与官方链接。
"""

from __future__ import annotations

from datetime import datetime

from ..schemas import AssessmentRun, PolicyAssessment

DISCLAIMER = "政策信息辅助整理；非税务专业意见。"


def render_markdown(run: AssessmentRun, *, downloaded_at: datetime) -> str:
    lines: list[str] = [
        "# 惠策协同：政策机会分析报告",
        "",
        f"> {DISCLAIMER}",
        "",
        f"- 判断运行：`{run.id}`",
        f"- 生成时间：{run.created_at.isoformat()}",
        f"- 下载时间：{downloaded_at.isoformat()}",
        f"- 企业画像：`{run.enterprise_id}`（版本 `{run.fact_version}`）",
        f"- 判断期间：{run.period.start_date.isoformat()} 至 {run.period.end_date.isoformat()}",
        f"- 判断时点：{run.as_of.isoformat()}",
        f"- 规则版本：`{run.rule_version}`",
        f"- 政策版本：{_version_map(run)}",
    ]
    if run.supersedes_run_id is not None:
        lines.append(f"- 替代的历史运行：`{run.supersedes_run_id}`")
    lines += ["", "## 截至下载时刻的有效性", ""]
    lines += [_validity_line(item) for item in run.assessments]
    lines += ["", "## 判断结论", "", "| 政策 | 结论 | 有效性 |", "| --- | --- | --- |"]
    for item in run.assessments:
        title = _policy_label(run, item)
        lines.append(f"| {title} | {_RESULT_TEXT.get(item.result, item.result)} | {_VALIDITY_TEXT[item.validity]} |")

    groups: dict[str, list[PolicyAssessment]] = {"tax": [], "grant": []}
    for item in run.assessments:
        groups.setdefault(item.preparation.policy_type, []).append(item)
    for policy_type, heading in (("tax", "税收政策"), ("grant", "资金政策")):
        items = groups.get(policy_type) or []
        if not items:
            continue
        lines += ["", f"## {heading}", ""]
        for item in items:
            lines += _policy_section(run, item)

    lines += [
        "",
        "## 说明",
        "",
        "- 四态由已审核规则确定性求值，模型只改写措辞，不决定资格。",
        "- 「证据不足」表示缺少必要事实，不能当作不满足；补答后应以新的判断运行重算。",
        "- 本文件是历史快照的转写。离开系统后不会自动更新；再次申报前请回到系统核验。",
        "",
    ]
    return "\n".join(lines)


_RESULT_TEXT = {
    "satisfied": "满足",
    "not_satisfied": "不满足",
    "insufficient_evidence": "证据不足",
    "conflict": "条款冲突",
}
_VALIDITY_TEXT = {"current": "当前", "stale": "已失效"}
_HANDLING_TEXT = {"tax_self_assessment": "自行申报", "grant_application": "申请办理"}


def _validity_line(assessment: PolicyAssessment) -> str:
    label = f"`{assessment.policy_id}`"
    if assessment.validity == "current":
        tail = f"，有效期至 {assessment.valid_until}" if assessment.valid_until else ""
        return f"- {label}：仍按当前规则有效{tail}。"
    reasons = "；".join(reason.message for reason in assessment.stale_reasons) or "依赖已变化"
    return f"- {label}：**已失效**（{reasons}）。结论仅作历史记录，请重新分析。"


def _policy_section(run: AssessmentRun, assessment: PolicyAssessment) -> list[str]:
    title = _policy_label(run, assessment)
    lines = [f"### {title}", ""]
    lines.append(f"- 结论：**{_RESULT_TEXT.get(assessment.result, assessment.result)}**")
    lines.append(f"- 有效性：{_VALIDITY_TEXT[assessment.validity]}")
    if assessment.validity == "stale":
        lines.append(
            "- 失效原因：" + "；".join(reason.message for reason in assessment.stale_reasons)
        )
    if assessment.valid_until:
        lines.append(f"- 有效期至：{assessment.valid_until}")
    if assessment.missing_facts:
        missing = "、".join(f"`{ref.key}`（{ref.period.start_date}~{ref.period.end_date}）" for ref in assessment.missing_facts)
        lines.append(f"- 缺少的事实：{missing}")
    if assessment.next_question is not None:
        lines.append(f"- 建议补充：{assessment.next_question.prompt}")

    lines += ["", "**条件与证据**", ""]
    for criterion in assessment.criteria_results:
        lines.append(
            f"- {_RESULT_TEXT.get(criterion.result, criterion.result)}：{_one_line(criterion.reason)}"
        )
        for evidence in criterion.evidence_refs:
            where = evidence.section or (f"第 {evidence.page} 页" if evidence.page else "")
            lines.append(f"  - 「{_one_line(evidence.quote)}」{('，' + where) if where else ''}（{evidence.source_url}）")
        if criterion.calculation is not None:
            lines.append(
                f"  - 计算：{_one_line(criterion.calculation.formula)} 结果 {criterion.calculation.value} {criterion.calculation.unit}"
            )

    preparation = assessment.preparation
    lines += ["", "**办理清单**", ""]
    lines.append(f"- 办理方式：{_HANDLING_TEXT.get(preparation.handling_mode, preparation.handling_mode)}")
    if preparation.official_entry_url:
        lines.append(f"- 官方入口：{preparation.official_entry_url}")
    if preparation.deadline:
        lines.append(f"- 申报截止：{preparation.deadline.isoformat()}")
    if preparation.items:
        for item in preparation.items:
            lines.append(f"- {item.title}：{_one_line(item.description)}")
    else:
        lines.append("- 具体材料要求待政策材料导入后补齐。")

    lines += ["", "**解释**", "", _one_line(assessment.explanation), ""]
    lines.append(f"- 解释来源：`{assessment.explanation_mode}`")
    for warning in assessment.warnings:
        lines.append(f"- 注意：{_one_line(warning)}")
    lines.append("")
    return lines


def _policy_label(run: AssessmentRun, assessment: PolicyAssessment) -> str:
    version = run.policy_version_map.get(str(assessment.policy_id))
    return f"`{assessment.policy_id}`" + (f"@{version}" if version else "")


def _version_map(run: AssessmentRun) -> str:
    if not run.policy_version_map:
        return "（无）"
    return "、".join(f"`{policy_id}`@{version}" for policy_id, version in sorted(run.policy_version_map.items()))


def _one_line(text: str) -> str:
    return " ".join(str(text).split())
