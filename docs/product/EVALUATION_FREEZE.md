# 留出集评测集冻结声明（草案）

> 版本：v0.9-draft｜起草：M3｜日期：2026-11-04｜最近修订：2026-11-07
> **状态：M2 已复审（2026-11-07，head 原 `0e4a54d`；清理提交信息后为 `8c975dd`），保留草案、未签认；本轮修正后待 M2 再复核。** 在 M2 签认前，本文件不得作为「已冻结」的依据引用；仓库内也不得出现「已冻结」的对外表述。
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

以下指纹中，`labels.csv`、`enterprises.csv` 与旧版 `criteria_review.csv` 于 2026-11-04 在 `main`（`fc5c8e0`）计算；`criteria_review.csv` 的新值于 2026-11-07 修正后重算：

| 文件 | SHA-256 |
| --- | --- |
| `data/evaluation/labels.csv`（整文件） | `9ea7310ce2f7d90956e26053426fa313185960d9f32047aeac91f361bb66e1c8` |
| `data/evaluation/labels.csv` 的 holdout 子集（规范化） | `f380ca6d88b7710a27de49bee1f833d5429663bc247dbaff7bd624e165f6177e` |
| `data/policies/criteria_review.csv`（2026-11-04，`main` `fc5c8e0`；2026-11-06 试跑使用） | `767c6b224bad5246b6ea0e5bfa72f8cdc54df211157937a4df99312c5e2ee586` |
| `data/policies/criteria_review.csv`（2026-11-07 修正后，当前） | `f7db3424f0fa3297ebd0a388c7943de1743791a3e189eb6ef8b249f60451a2f8` |
| `data/fixtures/enterprises.csv` | `fae2a66a947a010a4fd4b568b1793c2c4b0de85daf78d8d15d40c5b6b6b1be9a` |

> `criteria_review.csv` 的 2026-11-07 修正只改 10 行的 `source_version` 归属（SRDI-001～008、RD-001、HT-007）；**RD-001 的 `source_document` 保持空**，表示引文出自政策本体，负面清单 8 段在 `data/policies/sources/CN-TAX-RD-2023-007.txt` 中逐字可查。`labels.csv`、`enterprises.csv` 与 `sources/财税〔2015〕119号.txt` 均未改。
>
> 哈希对应：`767c6b…` = 2026-11-06 试跑使用；`6ece5e0a…` = M2 复审 head（原 `0e4a54d`，清理签名后 `8c975dd`；RD-001 单值，未按 8 段对齐）；`998da4c0…` = 原 `689f1ae`（清理签名后 `61939bd`；曾按 8 段填 119 号，属错误改法，已回退）；`f7db3424…` = 当前（`source_document` 保持空）。**四者不得混用。**
>
> 指纹只识别所列文件，**不证明数据库导入与运行输入一致**；后者见 §5.2 的映射与一致性核对。

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

## 4. 标签形成时间线与改判记录

| 版本 | 时间（北京时间） | 提交 | 留出集变化 |
| --- | --- | --- | --- |
| v1 | 2026-11-01 20:36:07 | `750c52f` | 26 条；C029/HT-007 判 `conflict` |
| v2 | 2026-11-02 17:37:44 | `df05346` | 26 条；C029/HT-007 改判 `insufficient_evidence`，原 A/B 恢复 |
| v1 存档 | — | — | [`data/evaluation/archive/labels_holdout_v1_2026-11-01.csv`](../../data/evaluation/archive/labels_holdout_v1_2026-11-01.csv) |

- v1 的 26 条原样留档，仅作历史记录，**不用于评分**。
- v2 的改判理由：深科技创新规〔2021〕5号 第三条采用财税〔2015〕119号 的归集范围；国科发火〔2016〕195号 的 20% 仅见第七条（第三方审计/鉴证机构资质），不属本政策归集口径。原 `conflict` 结论作废，改为 `manual_review` → 求值恒 `insufficient_evidence`。
- **`labels.csv` 自 `df05346` 起未再修改**（`git log df05346..HEAD -- data/evaluation/labels.csv` 为空）。
- 形成后的相关变更：`d2acd9c` 仅修改 `criteria_review.csv` 中 **GXJS-V1/V2 的 `adjudication_note` 措辞**（不改 AST，且 **GXJS 不在留出集**）；2026-11-07 又修正了 `criteria_review.csv` 的归属字段（见下条）。`enterprises.csv` 自形成后未修改。
- **2026-11-07（本 PR 的归属修正）**：`criteria_review.csv` 只修正 10 行的 `source_version`（SRDI-001～008、RD-001、HT-007）；**RD-001 的 `source_document` 保持空**（引文出自政策本体，负面清单 8 段在 `CN-TAX-RD-2023-007.txt` 中逐字可查）。**不改条件、AST、标签或 119 号节选**；`labels.csv` 仍为 v2。**2026-11-06 的试跑使用修改前的 `criteria_review.csv`（分母 140）**；修正后引用可证实率分母为 146，旧跑数不得套用新分母。
- 上述时间是标签的**形成时间**，不是正式冻结/签认时间；正式签认时间见 §7，由 M2 填写。

## 5. 评测协议

1. **只用 v2 留出集评分**；开发集不报留出集指标。
2. **冻结后不改标签、不按结果调规则**；如发现标签错误，停止评测，建 v3，重跑并同时报告 v2 / v3 结果。
3. 每次运行记录：**run ID、代码 commit、`MODEL_MODE`、运行时间、输入企业/政策版本**。
4. **本轮只报条件级指标**：
   - 条件级四态混淆矩阵（按 `criterion_id`）；
   - 条件级硬门槛误判（`not_satisfied` 被判 `satisfied`）单列；
   - 失败样例逐条列出，不隐藏；
   - **政策级四态不报告**：见 §5.1，当前没有独立可核对的政策级人工真值，不能用被测程序的聚合结果作为真值。
5. **`conflict` 不报准确率**（留出集 0 条），只说明「仅单测覆盖」。
6. 报告注明样本量小（26 条 / 6 组合）带来的不确定性；不宣称零风险。报告必须保留：**26 条 / 6 组合、`conflict` 无样本、非盲测限制**；不得写成更大样本、已覆盖 `conflict` 或盲测。

### 5.1 政策级真值的前置条件（未完成，不得报政策级指标）

`labels.csv` 是条件级标签；政策级四态不能由被测程序的聚合结果代替真值。要报告政策级指标，须先完成并签认：

1. **条件集合完整性检查**：每个 `(policy_id, policy_version)` 的 criteria 集合与 `criteria_review.csv` 对齐，无缺漏/重复。
2. **硬门槛清单**：明确哪些 criterion 是硬门槛（地区、行业、成立、资格类），不满足直接 `not_satisfied`。
3. **优先级**：冲突与资料不足的优先级须由 M3 仲裁确定（项目口径为 conflict > not_satisfied > insufficient_evidence > satisfied），并说明 OR 分支如何处理。
4. **独立聚合规则**：用独立脚本按上述规则从条件级**人工标签**形成每个 case 的政策级真值；不得调用被测程序的聚合实现。
5. **M3 仲裁与签认**：形成 `labels_policy_level.csv`（每 case 一行），逐案复核并签认，记录文件哈希。

在上述 1–5 完成前，政策级指标写「未定义/不报告」，不能写成 0 或 100%。

### 5.2 输入与执行快照（M1 提供中）

三份 CSV 的哈希只能识别文件，不足以复现一次实际评测。执行前须由 M1 提供并核对：

1. **政策导入映射**：`policy_id / policy_version / criterion_id` → 数据库中的政策/条件 ID、AST 哈希、导入提交与时间。
2. **企业事实映射**：`enterprise_id + fact_key + period_start/end` → 事实值哈希、事实版本、来源文件。
3. **运行参数**：数据集版本/哈希、代码 commit、规则版本、`asOf`、分析期间、`MODEL_MODE`、配置哈希。`codeCommit` 在 ECS 无 `.git` 时为 `null`，不得编造；签认前须由部署记录补 commit，或明确标为「代码身份未证明」，不得以双 `null` 当作代码相同。`verify` 在两侧均 `null` 时应打印「代码身份未证明」提示；退出码可为 0（证据缺失≠内容不一致），但**该提示必须进入评测记录**；代码身份不由 `--commit` 证明：它只是注记，两侧同值也不能说明磁盘或进程上跑的是该提交。清单另记 `codeFingerprint`——范围 `services/api/app`、`contracts/openapi.yaml`、`services/api/requirements.lock`，算原始字节、不做换行归一化，排除 `__pycache__`；复算用 `python -m app.evaluation fingerprint --root <代码根>`，跨系统核对须用 `git -c core.autocrlf=false archive` 导出同一份 LF 内容再算，并写明导出方式。三件事分开证明：磁盘上是不是这份代码（指纹回答）、这份代码对应哪个提交（checkout 后复算回答）、进程里跑的是不是它（指纹答不了，只能拿进程启动时间与代码文件 mtime 交叉核对，`--process-pid` 签认时才留得下）。旧清单没有 `codeFingerprint` 的一律记「磁盘代码身份未证明」，须重新签认。
4. **结果导出（2026-11-05 定案：方案 A；条件级证据以 report 稳定正文为准）**：
   - `runId`；
   - **`report.md` 稳定正文**：用 `python -m app.evaluation freeze-report report.md --out report.frozen.md` 生成；剔除**四处**按下载时刻现算的内容——① 开头「截至下载时刻的有效性」段；② 头部「- 下载时间」行；③ 判断结论表的「有效性」列；④ 每个政策段的「- 有效性 / - 失效原因 / - 有效期至」行。只删这四处，保留条件级四态、理由、逐字引文/出处、`calculation.formula` 与结果、办理清单；剔除规则由 M1 实现并有测试，**不在评测时临时手工裁剪**；
   - 原始 `report.md` 由调用方另存；`freeze-report` 的 stderr 审计行给出 `sourceSha256`、`frozenSha256`、`downloadedAt`、`droppedLines`，冻结产物以 `frozenSha256` 为准；输入必须是未裁剪的原始报告，缺标题/缺有效性段/摘要表列数异常直接报错、不产出文件；
   - 完整 `trace` JSON 及其 SHA-256，用于轨迹可追溯；**trace 只有阶段摘要与 `{type,id}` 引用，不作为条件级证据**；
   - run 行的 `factVersion`、`modelOrRuleVersion`、`asOf`、`codeCommit`（或部署记录中的 commit）；
   - **不新增条件级结果表**。若 report 稳定正文不足以机器解析条件级结果，先记录缺口，再评估机器可读导出（不新增表）或方案 B；不临时改库；
   - 条件级对账以 `labels.csv` 的条件级标签为准，结果从 report 稳定正文导出。
5. **一致性核对**：运行前用上述映射核对运行时输入与签认快照；不一致即停止评测并记录，不得用运行结果反推快照。

> **数据集哈希范围（2026-11-05 补）：** 只覆盖 §3 的三份文件（`labels.csv`、`criteria_review.csv`、`enterprises.csv`）；`data/evaluation` 下的复核表/标注表（`material_review_*.csv`、`*_annotation_sheet.csv`）不属于评测输入，不应进入签认清单。`--dataset-dir` 如只能扫目录，应改为显式文件列表或支持多次指定。

**进度（2026-11-07）：** 第 1、2、3、5 项与第 4 项 `freeze-report` 由 #39 实现（已合入，2026-11-06 09:59）；部署文件指纹 `codeFingerprint` 由 #42 补入（已合入，2026-11-07 03:46），复算方式见第 3 项。清单结构以当前 `main` 的 `app.evaluation` 为准，不再引用 #39 的 head 提交。已合入不等于已完成：M1 报告 ECS 已有一份清单，但映射未解决 30 项、`verify` 非零退出；M2 未独立访问 ECS 核实，§5.2 不能算完成。本 PR 修改 `criteria_review.csv` 后必须重出映射/清单；本声明保持草案，直到跑出「未解决 0 项」的清单、`verify` 通过，并把清单、`asOf`、期间、run 输出与复核记录一并归档。
>
> M1 已把消融加载口径改为读 `source_document`（按 `|` 与段数对齐，空时退回政策正文；提交 `200b7fe`（cherry-pick 为 `e7a25b4`，已带入本分支）。归属修正后 RD-001 的 8 段引文在第 7 号正文逐字可查、HT-007 的引文在 119 号节选可查，`unverifiable_criteria()` 为空；引用可证实率分母由 **140 → 146**。这是语义修正的必然结果，不是调数：**11-06 旧跑数的 140 必须与旧哈希 `767c6b…` 一起报告，不得套用 146。**

## 6. 锁定边界声明（避免误读）

- 本声明**不是**「已冻结」，也不是「标注时盲测」。
- 现有记录显示：v2 于 `df05346`（2026-11-02 17:37）形成，此后 `labels.csv` 未再修改；`ACCEPTANCE.md` 未见 P0 验收记录。**这只能证明现有记录中没有 P0 验收记录，不能单独证明此前没有其他试跑或标签使用。**
- 是否在任何相关评测/调参前锁定，尚需各成员确认；本声明不作此推断。
- 第 2 轮仲裁的理由记录在 `criteria_review.csv` 的 HT-007 `adjudication_note`（政策原文与规则语义）；**「是否参考过引擎输出」尚需实际参与者确认**，本声明不作此推断；不主张标注过程具备统计意义上的盲测条件。
- 正式冻结以 M2 签认时间为准，**不以历史提交时间代替签认时间**。
- **留出集试跑历史（M1 自述）：** 2026-11-06 16:08–16:29 以 DeepSeek 跑过一次完整对照（`asOf=2026-11-06`，32 个 case / 146 条标签，dev 120 / holdout 26），输出仅 M1 本人看过。**该次试跑使用修改前的 `criteria_review.csv`（`767c6b…`），引用可证实率分母为 140（RD-001 的 6 个 case 被排除）**；此后 `labels.csv` 未改（最后改动 2026-11-02），`criteria_review.csv` 于 2026-11-07 只修正 `source_version` 归属（`f7db3424…`），`source_document` 保持空，不改条件与标签。修正后不可验证集合为空、分母为 **146**；**旧跑数 140 只能与旧哈希/旧数据一起报告，不得把新分母套到旧跑数**。据该次结果只修了实现缺陷（抽取提示词算子键名、指标表百分比渲染）与三处口径，未改条件与标注。故该次结果既不能归属到当前输入，也不作为留出集的独立验证；后续若以留出集下结论，须注明「实现曾据该次结果修过缺陷」，并说明当前输入已在试跑后变更。

## 7. 签认

| 角色 | 动作 | 状态 | 起草/复核日期 | 正式签认时间 |
| --- | --- | --- | --- | --- |
| M3 | 起草本声明、提供指纹与复现方法 | 已起草；2026-11-07 按复审意见修订 | 2026-11-04 | — |
| M2 | 复核正式表述、评测协议与政策级/输入快照口径 | 已复审；保留草案、未签认 | 2026-11-07（head 原 `0e4a54d` / 现 `8c975dd`） | — |
| M1 | 提供输入映射、运行参数与结果导出方式，确认运行时数据与快照一致 | 已提供 #39 与 `200b7fe`（cherry-pick `e7a25b4`；消融/导入器修复，已带入本分支）；ECS 清单映射未解决 30 项、`verify` 非零，M2 未独立核实，待重出 | — | — |

> 正式冻结以 M2 在本表填写**签认时间**为准；本文件状态改为「已复核」前，不得对外称已冻结。

**M2 复核问题：**

1. 「锁定边界」的限定表述（§6）是否可接受？
2. 本轮先不报政策级指标、按 §5.1 完成前置条件后再报，是否可接受？
3. §5.2 的输入/执行快照要求是否足够；待 M1 提供后是否可作为冻结前置？
4. 指纹与版本时间线是否仍足够复现（§3–§4）？

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
