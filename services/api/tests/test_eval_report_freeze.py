"""评测结果导出：把 API-13 报告裁成确定性冻结产物（``EVALUATION_FREEZE`` §5.2 第 4 项）。

M3 定的口径是「只导出不含有效性段的部分」，原始报告与下载时间另存审计。这里钉住三件事：

* 所有按下载时刻现算的内容都删干净（下载时间、有效性段、摘要表的有效性列、政策段的
  有效性/失效原因/有效期至）；
* 同一个 run 换个时刻、换个失效状态再下载，冻结后的字节必须一致——否则哈希不是证据；
* 输入不是原始下载时直接报错，不猜。
"""

from __future__ import annotations

import json

import pytest

from app.evaluation import __main__ as cli
from app.evaluation.report_freeze import ReportError, freeze_report
from support import published_draft

YEAR = {"startDate": "2026-01-01", "endDate": "2026-12-31"}
AS_OF = "2026-10-23T10:00:00+08:00"

INCOME_GATE = {
    "op": "compare",
    "factKey": "taxableIncome",
    "period": "assessment",
    "comparator": "gte",
    "value": "3000000.00",
    "unit": "CNY",
}

REPORT = """# 惠策协同：政策机会分析报告

> 政策信息辅助整理；非税务专业意见。

- 判断运行：`run-1`
- 生成时间：2026-10-23T02:00:00+00:00
- 下载时间：2026-11-05T09:00:00+00:00
- 企业画像：`ent-1`（版本 `fact-1`）
- 判断期间：2026-01-01 至 2026-12-31
- 判断时点：2026-10-23T10:00:00+08:00
- 规则版本：`rules-1`
- 政策版本：`p-1`@v1、`p-2`@v1

## 截至下载时刻的有效性

- `p-1`：仍按当前规则有效，有效期至 2027-01-01。
- `p-2`：**已失效**（政策条款已更新）。结论仅作历史记录，请重新分析。

## 判断结论

| 政策 | 结论 | 有效性 |
| --- | --- | --- |
| `p-1`@v1 | 满足 | 当前 |
| `p-2`@v1 | 不满足 | 已失效 |

## 税收政策

### `p-1`@v1

- 结论：**满足**
- 有效性：当前
- 有效期至：2027-01-01

**条件与证据**

- 满足：年应纳税所得额不低于 300 万元
  - 「年度应纳税所得额不超过300万元」第五条（https://example.gov.cn/a）

**办理清单**

- 办理方式：自行申报
- 具体材料要求待政策材料导入后补齐。

**解释**

按规则得出。

- 解释来源：`saved`

## 资金政策

### `p-2`@v1

- 结论：**不满足**
- 有效性：已失效
- 失效原因：政策条款已更新

**条件与证据**

- 不满足：行业属于禁止类
  - 「限制类行业」第二条（https://example.gov.cn/b）

**办理清单**

- 办理方式：申请办理
- 具体材料要求待政策材料导入后补齐。

**解释**

按规则得出。

- 解释来源：`fixture`
- 注意：模型未参与本次判断。

## 说明

- 四态由已审核规则确定性求值，模型只改写措辞，不决定资格。
- 「证据不足」表示缺少必要事实，不能当作不满足；补答后应以新的判断运行重算。
- 本文件是历史快照的转写。离开系统后不会自动更新；再次申报前请回到系统核验。
"""


def _later_download() -> str:
    """同一份 run 换一天下载：下载时间、失效状态都变了，冻结后应当一致。"""

    return (
        REPORT.replace("2026-11-05T09:00:00+00:00", "2026-11-20T23:30:00+00:00")
        .replace(
            "- `p-1`：仍按当前规则有效，有效期至 2027-01-01。",
            "- `p-1`：**已失效**（政策窗口已关闭）。结论仅作历史记录，请重新分析。",
        )
        .replace("| `p-1`@v1 | 满足 | 当前 |", "| `p-1`@v1 | 满足 | 已失效 |")
        .replace(
            "- 结论：**满足**\n- 有效性：当前\n- 有效期至：2027-01-01",
            "- 结论：**满足**\n- 有效性：已失效\n- 失效原因：政策窗口已关闭",
        )
        .replace("| `p-2`@v1 | 不满足 | 已失效 |", "| `p-2`@v1 | 不满足 | 当前 |")
    )


def test_frozen_export_drops_every_download_time_value() -> None:
    frozen = freeze_report(REPORT)

    assert frozen.downloaded_at == "2026-11-05T09:00:00+00:00"
    text = frozen.text
    assert "下载时间" not in text
    assert "截至下载时刻的有效性" not in text
    assert "有效性" not in text
    assert "有效期至" not in text
    assert "失效原因" not in text
    # 判断结论表从三列变两列
    assert "| 政策 | 结论 |\n| --- | --- |" in text
    assert "| `p-1`@v1 | 满足 |" in text
    assert "| `p-2`@v1 | 不满足 |" in text
    # 条件级内容与办理清单原样保留——冻结不能把结论一起删掉
    assert "**条件与证据**" in text
    assert "「年度应纳税所得额不超过300万元」第五条（https://example.gov.cn/a）" in text
    assert "- 办理方式：自行申报" in text
    assert frozen.dropped_lines > 0


def test_two_downloads_of_the_same_run_freeze_to_the_same_bytes() -> None:
    first = freeze_report(REPORT).text
    second = freeze_report(_later_download()).text

    assert first == second


def test_a_hand_trimmed_or_already_frozen_file_is_rejected() -> None:
    frozen = freeze_report(REPORT).text

    with pytest.raises(ReportError, match="截至下载时刻的有效性"):
        freeze_report(frozen)
    with pytest.raises(ReportError, match="标题"):
        freeze_report("# 别的报告\n\n## 截至下载时刻的有效性\n")


def test_a_summary_row_with_unexpected_columns_is_rejected() -> None:
    broken = REPORT.replace("| `p-1`@v1 | 满足 | 当前 |", "| `p-1`@v1 | 满足 |")

    with pytest.raises(ReportError, match="列数"):
        freeze_report(broken)


# ------------------------------------------------------------- 与真实渲染器的耦合


def _enterprise(client) -> dict:
    response = client.post(
        "/api/v1/enterprises",
        json={
            "nameSynthetic": "合成企业A",
            "period": YEAR,
            "facts": [
                {
                    "key": "region",
                    "value": "440300",
                    "unit": "code",
                    "period": YEAR,
                    "source": "user_input",
                },
                {
                    "key": "industry",
                    "value": "F51",
                    "unit": "code",
                    "period": YEAR,
                    "source": "user_input",
                },
                {
                    "key": "taxableIncome",
                    "value": None,
                    "unit": "CNY",
                    "period": YEAR,
                    "source": "user_input",
                },
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_a_real_report_freezes_without_any_validity_line(client, repository) -> None:
    """用真实渲染器的输出验证剔除口径，避免格式改了而工具还在按旧标记裁。"""

    policy = published_draft(expressions=[INCOME_GATE])
    repository.drafts.append(policy)
    created = client.post(
        "/api/v1/assessment-runs",
        json={
            "enterpriseId": _enterprise(client)["id"],
            "policyIds": [str(policy.id)],
            "asOf": AS_OF,
            "period": YEAR,
        },
    )
    assert created.status_code == 200, created.text
    raw = client.get(f"/api/v1/assessment-runs/{created.json()['id']}/report.md").text

    # 前提：原始下载里确实有有效性段，否则这条用例证明不了剔除
    assert "## 截至下载时刻的有效性" in raw
    assert "- 有效性：" in raw

    frozen = freeze_report(raw)
    assert "有效性" not in frozen.text
    assert "下载时间" not in frozen.text
    assert "**条件与证据**" in frozen.text
    # 同一份原始报告再裁一次，字节一致
    assert freeze_report(raw).text == frozen.text


def test_cli_writes_the_frozen_file_and_reports_both_hashes(tmp_path, capsys, monkeypatch) -> None:
    source = tmp_path / "report.md"
    source.write_text(REPORT, encoding="utf-8")
    target = tmp_path / "report.frozen.md"

    assert cli.main(["freeze-report", str(source), "--out", str(target)]) == 0

    audit = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    expected = freeze_report(REPORT)
    assert target.read_text(encoding="utf-8") == expected.text
    assert audit["downloadedAt"] == "2026-11-05T09:00:00+00:00"
    assert audit["sourceSha256"] != audit["frozenSha256"]
    assert len(audit["sourceSha256"]) == 64 and len(audit["frozenSha256"]) == 64
