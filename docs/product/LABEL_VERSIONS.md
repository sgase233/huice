# 留出集标签版本记录（M3）

> 编写 2026-11-03。用于说明留出集标签的**实际形成时间、改判过程、正式冻结状态与新旧版本用途**，回应 11/03 评审意见。
> 评测一律使用当前 [`data/evaluation/labels.csv`](../../data/evaluation/labels.csv)（v2）；v1 仅作历史记录，不用于评分。

## 1. 版本概览

| 版本 | 时间（北京时间） | 提交 | 文件 | 用途 |
| --- | --- | --- | --- | --- |
| **v1** | 2026-11-01 20:36:07 | `750c52f` | [`data/evaluation/archive/labels_holdout_v1_2026-11-01.csv`](../../data/evaluation/archive/labels_holdout_v1_2026-11-01.csv) | 原始仲裁记录（历史留档），**不用于评测** |
| **v2** | 2026-11-02 16:21–17:37 | `398d037` → `d089d7a` → `df05346` | [`data/evaluation/labels.csv`](../../data/evaluation/labels.csv) 的 `split=holdout` 行 | **当前评测真值**（11/08 使用） |

两版留出集均为 **6 组合 / 26 条**；组合构成未变。

## 2. v1 → v2 实际改了什么

留出集中只有 **C029 / HT-007** 一条的 `adjudicated_result` 发生变化：

| 版本 | C029/HT-007 `annotator_a_result` | `annotator_b_result` | `adjudicated_result` |
| --- | --- | --- | --- |
| v1（`750c52f`） | conflict | conflict | **conflict** |
| v2（当前） | conflict | conflict | **insufficient_evidence** |

过程：

1. `398d037`（2026-11-02 16:21）——按条款语义/引文复核作第 2 轮仲裁，把 HT-007 由 `conflict` 改为 `insufficient_evidence`；该次提交**同时覆盖了 A/B 原始判断**。
2. `d089d7a`（16:52）——恢复 HT-007 单引财税〔2015〕119号 的 `manual_review` 表达。
3. `df05346`（17:37）——**恢复原始 A/B 判断**（conflict / conflict），只把 `adjudicated_result` 保留为 `insufficient_evidence`，并在 `notes` 写明「第 2 轮仲裁、规则变更后的重新仲裁、非重新独立标注、原结论作废」。

改判理由（详见 `criteria_review.csv` 的 HT-007 `adjudication_note`）：深科技创新规〔2021〕5号 **第三条** 明文采用财税〔2015〕119号 的研发费用归集范围；国科发火〔2016〕195号 的 20% 在本政策中仅见**第七条**（第三方审计/鉴证机构资质），不属本政策归集口径。两者不是同一条件的冲突，故撤销 `review_finding=conflict`，改为 `manual_review`，求值恒 `insufficient_evidence`。

另：`GXJS-V1-001` / `GXJS-V2-001` 的 4 条**开发集**标签也于同期因比例条件降为 `manual_review` 而改判；不在留出集内，不影响本文件的 26 条统计。

## 3. 版本形成与正式冻结的准确表述

- **不是**「2026-11-01 完成盲测冻结」。v1 在 11-01 完成，但 11-02 因条款语义复核做了第 2 轮仲裁，留出集确有 1 条改判。
- **正确的表述**：v2 于 **2026-11-02 17:37**（`df05346`）**形成**，此后未修改，作为当前评测真值；现有记录未见 **P0 验收记录**（`ACCEPTANCE.md` 验收表仍为空，无 run ID/截图/实测记录），但这不能单独证明此前没有其他试跑或标签使用。是否在任何评测/调参前锁定尚需成员确认；正式冻结待 M2 签认，签认时间单独记录。
- 这是「**评测前形成的标签版本**」，不是「标注者在不知道程序结果的情况下盲测」。现有记录**不能证明**标注人看过程序结果，也没有证据表明看过；第 2 轮仲裁依据是政策原文与规则语义，不是引擎输出。
- 11/08 验收期间不再修改 v2 标签。若后续确需改判：**新增 v3 版本记录并保留 v2 文件**，不得直接覆盖。
- 正式冻结声明见 [`EVALUATION_FREEZE.md`](./EVALUATION_FREEZE.md)（状态：待 M2 复核）。

## 4. 复现方式

```powershell
# 查看 v1 的原始文件
git show 750c52f:data/evaluation/labels.csv

# 只取留出集（26 条）
# 与当前 labels.csv 的 split=holdout 行逐行比较
```

v1 快照文件由上述 `750c52f` 的 `split=holdout` 行逐字导出，未增删列。
