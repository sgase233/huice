# 留出集评测集冻结声明（草案）

> 版本：v0.9-draft｜起草：M3｜日期：2026-11-04
> **状态：待 M2 复核。** 在 M2 签认前，本文件不得作为「已冻结」的依据引用；仓库内也不得出现「已冻结」的对外表述。
> 关联文件：[`LABEL_VERSIONS.md`](./LABEL_VERSIONS.md)、[`ACCEPTANCE.md`](./ACCEPTANCE.md)、[`ANNOTATION_GUIDE.md`](./ANNOTATION_GUIDE.md) §6.1、[`data/evaluation/labels.csv`](../../data/evaluation/labels.csv)。

## 1. 冻结对象与用途

- **冻结对象**：`data/evaluation/labels.csv` 中 `split=holdout` 的条件级标签（v2）。
- **用途**：11/08 留出集评测、报告与演示；**不用于**规则调参或开发集指标。
- **不冻结**：开发集（`split=dev`）标签；开发集仅用于开发与回归，不对外报告为留出集指标。
- **不在本声明范围**：P0 程序验收记录（见 `ACCEPTANCE.md`）、材料要求复核（见 `MATERIAL_REQUIREMENTS.md`）。

## 2. 数据集身份

| 项 | 值 |
| --- | --- |
| 文件 | `data/evaluation/labels.csv` |
| 版本 | v2（当前评测真值） |
| 留出集规模 | **26 条条件级标签 / 6 个唯一组合** |
| 组合（case_id） | C004、C008、C015、C019、C024、C029 |
| 企业 | E-ENT-001 ~ E-ENT-006（6 家合成企业） |
| 政策版本 | CN-TAX-RD-2023-007、CN-TAX-SME-2023-012、SZ-GRANT-HIGHTECH-2021、SZ-GRANT-RD-SUBSIDY-2026、SZ-GRANT-SRDI-2025 |
| 四态分布 | **12 `satisfied` / 4 `not_satisfied` / 10 `insufficient_evidence` / 0 `conflict` |
| 全量标签（参考） | 148 条 / 34 个唯一组合（122 dev + 26 holdout） |

> **`conflict` 覆盖说明：** 留出集 `conflict` 为 **0**。四态中的冲突目前仅由引擎单元测试覆盖，**没有真实数据支撑**；评测报告不得声称已验证 conflict 场景（见 `ACCEPTANCE.md` 覆盖状态）。

## 3. 不可变指纹

以下指纹在 2026-11-04 于 `main`（`fc5c8e0`）计算：

| 文件 | SHA-256 |
| --- | --- |
| `data/evaluation/labels.csv`（整文件） | `9ea7310ce2f7d90956e26053426fa313185960d9f32047aeac91f361bb66e1c8` |
| `data/evaluation/labels.csv` 的 holdout 子集（规范化） | `f380ca6d88b7710a27de49bee1f833d5429663bc247dbaff7bd624e165f6177e` |
| `data/policies/criteria_review.csv` | `767c6b224bad5246b6ea0e5bfa72f8cdc54df211157937a4df99312c5e2ee586` |
| `data/fixtures/enterprises.csv` | `fae2a66a947a010a4fd4b568b1793c2c4b0de85daf78d8d15d40c5b6b6b1be9a` |

### 复现方法

整文件：

```bash
sha256sum data/evaluation/labels.csv
```

holdout 子集（按 `case_id, criterion_id` 排序、UTF-8、LF、`csv.writer` 最小引号）：

```python
import csv, hashlib, io

rows = list(csv.DictReader(open("data/evaluation/labels.csv", encoding="utf-8-sig", newline="")))
holdout = [r for r in rows if r["split"] == "holdout"]
holdout.sort(key=lambda r: (r["case_id"], r["criterion_id"]))

buf = io.StringIO(newline="")
writer = csv.writer(buf, lineterminator="\n")
writer.writerow(rows[0].keys())
writer.writerows([r.values() for r in holdout])
print(hashlib.sha256(buf.getvalue().encode("utf-8")).hexdigest())
# f380ca6d88b7710a27de49bee1f833d5429663bc247dbaff7bd624e165f6177e
```

## 4. 锁定时间线与改判记录

| 版本 | 时间（北京时间） | 提交 | 留出集变化 |
| --- | --- | --- | --- |
| v1 | 2026-11-01 20:36:07 | `750c52f` | 26 条；C029/HT-007 判 `conflict` |
| v2 | 2026-11-02 17:37:44 | `df05346` | 26 条；C029/HT-007 改判 `insufficient_evidence`，原 A/B 恢复 |
| v1 存档 | — | — | [`data/evaluation/archive/labels_holdout_v1_2026-11-01.csv`](../../data/evaluation/archive/labels_holdout_v1_2026-11-01.csv) |

- v1 的 26 条原样留档，仅作历史记录，**不用于评分**。
- v2 的改判理由：深科技创新规〔2021〕5号 第三条采用财税〔2015〕119号 的归集范围；国科发火〔2016〕195号 的 20% 仅见第七条（第三方审计/鉴证机构资质），不属本政策归集口径。原 `conflict` 结论作废，改为 `manual_review` → 求值恒 `insufficient_evidence`。
- **`labels.csv` 自 `df05346` 起未再修改**（`git log df05346..HEAD -- data/evaluation/labels.csv` 为空）。
- 锁定后唯一相关变更：`d2acd9c` 修改了 `criteria_review.csv` 中 **GXJS-V1/V2 的 `adjudication_note` 措辞**（仅说明，不改 AST），且 **GXJS 不在留出集**，不影响 26 条留出标签。`enterprises.csv` 自锁定后未修改。

## 5. 评测协议

1. **只用 v2 留出集评分**；开发集不报留出集指标。
2. **冻结后不改标签、不按结果调规则**；如发现标签错误，停止评测，建 v3，重跑并同时报告 v2 / v3 结果。
3. 每次运行记录：**run ID、代码 commit、`MODEL_MODE`、运行时间、输入企业/政策版本**。
4. 指标分层报告：
   - 条件级四态混淆矩阵（按 `criterion_id`）；
   - 政策级四态；
   - 硬门槛误判（`not_satisfied` 被判 `satisfied`）单列；
   - 失败样例逐条列出，不隐藏。
5. **`conflict` 不报准确率**（留出集 0 条），只说明「仅单测覆盖」。
6. 报告注明样本量小（26 条 / 6 组合）带来的不确定性；不宣称零风险。

## 6. 锁定边界声明（避免误读）

- 本声明是「**评测前锁定**」，**不是**「标注时盲测」。
- 依据：冻结时 `ACCEPTANCE.md` 验收表仍为空，无 run ID、截图或实测记录；没有证据表明标注人在标注时查看过程序结果。v1 的第 2 轮仲裁依据是政策原文与规则语义，不是引擎输出。
- 不推断、不夸大：本声明不主张标注过程具备统计意义上的盲测条件。

## 7. 签认

| 角色 | 动作 | 状态 | 日期 |
| --- | --- | --- | --- |
| M3 | 起草本声明、提供指纹与复现方法 | 已起草 | 2026-11-04 |
| M2 | 复核正式表述与评测协议，确认可用于报告/演示 | **待复核** | — |
| M1 | 确认评测入口、run ID 与数据快照一致 | 待确认 | — |

**M2 复核问题：**

1. 「评测前锁定」的表述是否可接受，是否需要在报告中补充其他限定？
2. 指纹、版本时间线与评测协议是否足够复现？
3. 是否同意在报告/演示中引用本声明，并在 `ACCEPTANCE.md` 中作为冻结口径？

## 8. 变更控制

- 本声明经 M2 复核后，状态改为「已复核」，并记录复核意见与日期。
- 冻结后如需修改标签：**新增 v3**，保留 v2 与 v1 文件，重跑评测并同时报告；不得覆盖原文件。
- 如仅修改本声明的措辞（不改标签），记录修改原因与提交号，状态保持「已复核」需 M2 确认。
- 任何「已冻结」的对外表述，以本文件状态为「已复核」为前提。

## 9. 引用

- [`LABEL_VERSIONS.md`](./LABEL_VERSIONS.md)：v1/v2 版本记录与复现命令。
- [`ANNOTATION_GUIDE.md`](./ANNOTATION_GUIDE.md) §6.1：双人标注与仲裁口径。
- [`ACCEPTANCE.md`](./ACCEPTANCE.md)：P0 验收记录与覆盖状态。
- [`data/evaluation/labels.csv`](../../data/evaluation/labels.csv)：当前评测真值。
