"""把下载到的 API-13 报告转成不含有效性段的冻结产物（EVALUATION_FREEZE §5.2 第 4 项）。

报告里有一类内容按**下载时刻**现算，不是历史快照：`- 下载时间：…` 行、「## 截至下载
时刻的有效性」整段，以及摘要表与政策段里的「有效性 / 失效原因 / 有效期至」。同一个 run
隔天再下载，这些值会变，直接拿文件哈希当冻结证据必然对不上。

M3 定的口径：冻结产物只留不随下载时刻变化的部分；原始 `report.md` 连同下载时间与
哈希另存供审计。剔除必须是**确定性**的——按固定标记整行/整段删除，不做任何按语义
猜测的裁剪；输入不像原始下载时直接报错，避免把手工改过的文件当冻结产物签认。
"""

from __future__ import annotations

from dataclasses import dataclass

TITLE = "# 惠策协同：政策机会分析报告"
VALIDITY_HEADING = "## 截至下载时刻的有效性"
SUMMARY_HEADING = "## 判断结论"
SUMMARY_HEADER = "| 政策 | 结论 | 有效性 |"
SUMMARY_RULE = "| --- | --- | --- |"
DOWNLOAD_PREFIX = "- 下载时间："
#: 每个政策段里按读取时刻现算的行；与摘要表的「有效性」列同一来源
POLICY_VOLATILE_PREFIXES = ("- 有效性：", "- 失效原因：", "- 有效期至：")

FROZEN_SUMMARY_HEADER = "| 政策 | 结论 |"
FROZEN_SUMMARY_RULE = "| --- | --- |"


class ReportError(ValueError):
    """输入不是一份可冻结的 API-13 原始下载。"""


@dataclass(frozen=True)
class FrozenReport:
    text: str
    #: 原始下载里的「下载时间」，供审计记录（冻结产物里不再出现）
    downloaded_at: str | None
    dropped_lines: int


def freeze_report(markdown: str) -> FrozenReport:
    """删掉所有按下载时刻现算的内容，返回可直接哈希的冻结正文。"""

    normalized = markdown.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")

    if TITLE not in lines:
        raise ReportError(f"不是 API-13 报告：缺少标题行「{TITLE}」")
    if VALIDITY_HEADING not in lines:
        raise ReportError(
            f"缺少「{VALIDITY_HEADING}」段——这不是原始下载，"
            "已经冻结过或手工裁剪过的文件不能再签认一次"
        )
    if SUMMARY_HEADER not in lines or SUMMARY_RULE not in lines:
        raise ReportError(
            f"缺少「{SUMMARY_HEADING}」的表头（{SUMMARY_HEADER}），报告的格式已变，拒绝猜测"
        )

    out: list[str] = []
    downloaded_at: str | None = None
    dropped = 0
    in_summary_table = False
    index = 0
    while index < len(lines):
        line = lines[index]

        if in_summary_table:
            if line == SUMMARY_RULE:
                out.append(FROZEN_SUMMARY_RULE)
                dropped += 1
                index += 1
                continue
            if line.startswith("|"):
                out.append(_drop_last_cell(line))
                dropped += 1
                index += 1
                continue
            in_summary_table = False

        if line.startswith(DOWNLOAD_PREFIX):
            downloaded_at = line[len(DOWNLOAD_PREFIX) :].strip()
            dropped += 1
            index += 1
            continue

        if line == VALIDITY_HEADING:
            # 整段删掉：连同它前面的空行，直到下一个二级标题
            while out and out[-1] == "":
                out.pop()
                dropped += 1
            dropped += 1
            index += 1
            while index < len(lines) and not lines[index].startswith("## "):
                if lines[index] != "":
                    dropped += 1
                index += 1
            if index < len(lines):
                out.append("")
            continue

        if line == SUMMARY_HEADER:
            out.append(FROZEN_SUMMARY_HEADER)
            in_summary_table = True
            dropped += 1
            index += 1
            continue

        if line.startswith(POLICY_VOLATILE_PREFIXES):
            dropped += 1
            index += 1
            continue

        out.append(line)
        index += 1

    return FrozenReport(text="\n".join(out), downloaded_at=downloaded_at, dropped_lines=dropped)


def _drop_last_cell(row: str) -> str:
    """摘要表的数据行去掉最后的「有效性」列。"""

    cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
    if len(cells) != 3:
        raise ReportError(f"摘要表行的列数不是 3（{len(cells)}），拒绝猜测哪一列是有效性")
    return f"| {cells[0]} | {cells[1]} |"
