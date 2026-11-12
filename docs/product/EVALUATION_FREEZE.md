# 留出集评测集冻结声明（草案）

> 版本：v0.9-draft｜起草：M3｜日期：2026-11-04｜最近修订：2026-11-11
> **状态：M2 已于 2026-11-10 完成本轮文档评审并签认修订方向（审查基线 `164ba32`，见 PR #57 评论），并于 2026-11-11 复审要求修正评测库配置步骤、补全证据索引完整哈希、统一 `--process-pid` 口径；M3 已按该轮意见整改，M2 于 2026-11-11 15:13 复审确认阶段性文档可推进、未发现新的阻塞问题（基准 head `6de4aee`）。本轮修订作为阶段性记录推进，保留草案、未正式冻结；原始证据核验与剩余事项待闭环。** 在 M2 完成正式冻结签认前，本文件不得作为「已冻结」的依据引用；仓库内也不得出现「已冻结」的对外表述。
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

> **两种统计口径（2026-11-10 补）：** 原始标签 **148 条 / 34 个唯一组合**（dev 122 / holdout 26）；消融与导入跑数排除旧版本 `C031`、`C033` 后为 **32 个组合 / 146 条标签**（dev 120 / holdout 26）。留出集评分对象始终为 **6 个组合 / 26 条标签**，两种口径下不变。详见 §5.2 的统计口径说明。

## 3. 不可变指纹

以下指纹中，`labels.csv`、`enterprises.csv` 与旧版 `criteria_review.csv` 于 2026-11-04 在 `main`（`fc5c8e0`）计算；`criteria_review.csv` 的新值于 2026-11-08 修正后重算：

| 文件 | SHA-256 |
| --- | --- |
| `data/evaluation/labels.csv`（整文件） | `9ea7310ce2f7d90956e26053426fa313185960d9f32047aeac91f361bb66e1c8` |
| `data/evaluation/labels.csv` 的 holdout 子集（规范化） | `f380ca6d88b7710a27de49bee1f833d5429663bc247dbaff7bd624e165f6177e` |
| `data/policies/criteria_review.csv`（2026-11-04，`main` `fc5c8e0`；2026-11-06 试跑使用） | `767c6b224bad5246b6ea0e5bfa72f8cdc54df211157937a4df99312c5e2ee586` |
| `data/policies/criteria_review.csv`（2026-11-08 修正后，当前） | `01c8821c7ee5a50750acdbf3a65cc7f8b7fe65d51367ca59ab23fd8a20234627` |
| `data/fixtures/enterprises.csv` | `fae2a66a947a010a4fd4b568b1793c2c4b0de85daf78d8d15d40c5b6b6b1be9a` |

> `criteria_review.csv` 的 2026-11-07 修正只改 10 行的 `source_version` 归属（SRDI-001～008、RD-001、HT-007）；**RD-001 的 `source_document` 保持空**，表示引文出自政策本体，负面清单 8 段在 `data/policies/sources/CN-TAX-RD-2023-007.txt` 中逐字可查。**在 2026-11-07 当时**，`labels.csv`、`enterprises.csv` 与 `sources/财税〔2015〕119号.txt` 均未改；11-08 修改 119 号节选见 §4。
>
> 哈希对应：`767c6b…` = 2026-11-06 试跑使用；`6ece5e0a…` = M2 复审 head（原 `0e4a54d`，清理签名后 `8c975dd`；RD-001 单值，未按 8 段对齐）；`998da4c0…` = 原 `689f1ae`（清理签名后 `61939bd`；曾按 8 段填 119 号，属错误改法，已回退）；`f7db3424…` = 2026-11-07 归属修正后（`source_document` 保持空）；`01c8821c…` = 2026-11-08 将 HT-007 的 `source_span` 改为官方逐字句后（当前）。**五者不得混用。**
>
> 指纹只识别所列文件，**不证明数据库导入与运行输入一致**；后者见 §5.2 的映射与一致性核对。

> **换行口径（2026-11-10 补）：** 上表中 `labels.csv`（`9ea7310c…`）与 `enterprises.csv`（`fae2a66a…`）是 **Windows 工作树 `sha256sum` 的原始字节值**（`core.autocrlf=true` 检出为 CRLF）。最终提交 `b9d2371` 的 Git blob、以及 `git -c core.autocrlf=false archive` 导出的文件是 **LF 原始字节**；同一文件两套值不同属**换行差异，不是内容变化**。ECS 侧记录的是 LF 值：
>
> | 文件（LF 原始字节，`b9d2371`） | SHA-256 |
> | --- | --- |
> | `data/evaluation/labels.csv` | `62f53bd7e7d70246162367d370b4367d12d769a3b9ed69e4a749ff11975b93fa` |
> | `data/fixtures/enterprises.csv` | `e836d551dd3e872ed00a9685f5da75bee6f921881816f3db67e9310316e89996` |
> | `data/policies/source_register.csv` | `1d4c2b1e86e3472f7b0949470f8b4f0245e3eedb7fce2cb40043878cff0f44ed` |
> | `data/policies/criteria_review.csv` | `01c8821c7ee5a50750acdbf3a65cc7f8b7fe65d51367ca59ab23fd8a20234627` |
>
> 上表 `criteria_review.csv` 的当前值 `01c8821c…` 本身就是 LF 口径，故两侧一致；其 Windows CRLF 检出值与之不同，不另列。`labels.csv` 的 holdout 子集哈希 `f380ca6d…` 按**规范化内容**（排序、UTF-8、LF、`csv.writer` 最小引号）计算，与工作树换行无关，两种检出下相同。本节不改变 §5.2 `manifest` / `codeFingerprint` 的原始字节签名规则：两者仍按实际读取/导出的字节计算，不忽略签名差异。

### 复现方法

**整文件，Windows 工作树原始字节**（对应上表 `labels.csv` 的 `9ea7310c…`）：

```bash
sha256sum data/evaluation/labels.csv   # CRLF 检出
```

**整文件，最终提交 LF 原始字节**（对应 ECS 与上节 LF 表）：从明确提交导出到独立目录后再算实际字节哈希；不要在 Windows 工作树直接 `sha256sum` 后当作 ECS 值：

```bash
mkdir -p /tmp/huice-b9d2371
git -c core.autocrlf=false archive b9d2371 data/evaluation/labels.csv | tar -x -C /tmp/huice-b9d2371
sha256sum /tmp/huice-b9d2371/data/evaluation/labels.csv   # 62f53bd7…

# 等价：直接对 Git blob 求哈希
git cat-file blob b9d2371:data/evaluation/labels.csv | sha256sum
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
- **2026-11-07（本 PR 的归属修正）**：`criteria_review.csv` 只修正 10 行的 `source_version`（SRDI-001～008、RD-001、HT-007）；**RD-001 的 `source_document` 保持空**（引文出自政策本体，负面清单 8 段在 `CN-TAX-RD-2023-007.txt` 中逐字可查）。**11-07 当时不改条件、AST、标签或 119 号节选**；11-08 已修改 119 号节选，见下条。`labels.csv` 仍为 v2。**2026-11-06 的试跑使用修改前的 `criteria_review.csv`（分母 140）**；修正后引用可证实率分母为 146，旧跑数不得套用新分母。
- **2026-11-08（HT-007 引文逐字修正）**：`source_span` 从「与研发活动直接相关的其他相关费用总额不得超过…」改为官方原句「此项费用总额不得超过可加计扣除研发费用总额的10%」；`sources/财税〔2015〕119号.txt` 换成官方原文句（附原文 URL），不再用自己的节选给 span 做逐字校验。**不改条件、AST 或标签**；`labels.csv` 仍为 v2（其 `source_span` 是标签快照，未随本次逐字修正改写，评测引用以 criteria 的逐字 span 为准）。
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

### 5.2 输入与执行快照（输入已由 M1 提供；执行快照部分完成，消融已于 2026-11-10 补跑；M2 已签认修订方向并复审，原始证据待核验）

三份 CSV 的哈希只能识别文件，不足以复现一次实际评测。执行前须由 M1 提供并核对：

1. **政策导入映射**：`policy_id / policy_version / criterion_id` → 数据库中的政策/条件 ID、AST 哈希、导入提交与时间。
2. **企业事实映射**：`enterprise_id + fact_key + period_start/end` → 事实值哈希、事实版本、来源文件。
3. **运行参数**：数据集版本/哈希、代码 commit、规则版本、`asOf`、分析期间、`MODEL_MODE`、配置哈希。`codeCommit` 在 ECS 无 `.git` 时为 `null`，不得编造；签认前须由部署记录补 commit，或明确标为「代码身份未证明」，不得以双 `null` 当作代码相同。`verify` 在两侧均 `null` 时应打印「代码身份未证明」提示；退出码可为 0（证据缺失≠内容不一致），但**该提示必须进入评测记录**；代码身份不由 `--commit` 证明：它只是注记，两侧同值也不能说明磁盘或进程上跑的是该提交。清单另记 `codeFingerprint`——范围 `services/api/app`、`contracts/openapi.yaml`、`services/api/requirements.lock`，算原始字节、不做换行归一化，排除 `__pycache__`；复算用 `python -m app.evaluation fingerprint --root <代码根>`，跨系统核对须用 `git -c core.autocrlf=false archive` 导出同一份 LF 内容再算，并写明导出方式。三件事分开证明：磁盘上是不是这份代码（指纹回答）、这份代码对应哪个提交（checkout 后复算回答）、进程里跑的是不是它（指纹答不了，只能拿进程启动时间与代码文件 mtime 交叉核对）。进程启动时间由 `inputs --process-pid` 记入清单的 `processStartedAt`；**历史 `b9d2371` 的 `verify` 接受但不消费 `--process-pid`，不能用它证明进程身份；#58 合并后新代码的 `verify` 不再接受该参数**。`inputs` 记录的启动时间与代码文件 mtime 只能作交叉核对，不能单独证明进程内存中的代码身份。旧清单没有 `codeFingerprint` 的一律记「磁盘代码身份未证明」，须重新签认；`codeFingerprint 8fe2368b…` 只对应历史 `b9d2371`，#58 合并会改变代码指纹，新部署须在 ECS 重算并在 §5.3 标注对应关系。
4. **结果导出（2026-11-05 定案：方案 A；条件级证据以 report 稳定正文为准）**：
   - `runId`；
   - **`report.md` 稳定正文**：用 `python -m app.evaluation freeze-report report.md --out report.frozen.md` 生成；剔除**四处**按下载时刻现算的内容——① 开头「截至下载时刻的有效性」段；② 头部「- 下载时间」行；③ 判断结论表的「有效性」列；④ 每个政策段的「- 有效性 / - 失效原因 / - 有效期至」行。只删这四处，保留条件级四态、理由、逐字引文/出处、`calculation.formula` 与结果、办理清单；剔除规则由 M1 实现并有测试，**不在评测时临时手工裁剪**；
   - 原始 `report.md` 由调用方另存；`freeze-report` 的 stderr 审计行给出 `sourceSha256`、`frozenSha256`、`downloadedAt`、`droppedLines`，冻结产物以 `frozenSha256` 为准；输入必须是未裁剪的原始报告，缺标题/缺有效性段/摘要表列数异常直接报错、不产出文件；
   - 完整 `trace` JSON 及其 SHA-256，用于轨迹可追溯；**trace 只有阶段摘要与 `{type,id}` 引用，不作为条件级证据**；
   - run 行的 `factVersion`、`modelOrRuleVersion`、`asOf`、`codeCommit`（或部署记录中的 commit）；
   - **不新增条件级结果表**。若 report 稳定正文不足以机器解析条件级结果，先记录缺口，再评估机器可读导出（不新增表）或方案 B；不临时改库；
   - 条件级对账以 `labels.csv` 的条件级标签为准，结果从 report 稳定正文导出。
5. **一致性核对**：运行前用上述映射核对运行时输入与签认快照；不一致即停止评测并记录，不得用运行结果反推快照。

> **输入签名范围（2026-11-08 补，替代 2026-11-05 的「三份文件」口径）：** 冻结声明按阶段分别签认实际读取的文件，不能用一份固定文件名清单代替：
>
> | 阶段 | 实际读取 | 签名与失败条件 |
> | --- | --- | --- |
> | 数据集（消融输入） | 四份 CSV（`labels.csv`、`criteria_review.csv`、`enterprises.csv`、`source_register.csv`）+ 被 case 引用到的政策正文 + 每条 `source_document` 的出处原文 | `Dataset.manifest()`；任一文件缺失即 `DatasetError`，不得判为同一输入；正文改一字，对应条目必须变化 |
> | 导入映射 | 导入器实际读取的四份 CSV、政策正文、文号命名的出处原文、`reference_sources.csv` | `import_eval_dataset.py --manifest`；缺失即失败/blocked，不借用主政策链接 |
> | 消融跑数 | 该次运行实际读取的文件集合 | JSONL 头 `manifest` 字段，按实际文件逐项比对 |
>
> 三类签名不可互相替代。旧试跑（2026-11-06）保留当时的旧范围，不回溯套用新清单；`data/evaluation` 下的复核表/标注表不属于评测输入。本分支自 `ae15dc0` 起导入器为 main 版（#51 已合入 `cf74ffa`）；`--manifest` 已于 2026-11-09 用本 PR 最终数据重出（13 份，不连库）；与实库的一致性由 `inputs` 与 `verify` 单独证明；`reference_sources.csv` 已由导入器读取。消融已于 2026-11-10 在最终数据上跑完（32 case / 失败 0，JSONL `fe44e33e…`，见下）；三类签名产物均已产生，其中消融 JSONL 在 ECS 留档、仓库内未收录，仓库归档仍待补。
>
> **跨文件来源链接（2026-11-08 补）：** 正文用 `source_register.csv` 的 `source_url`；跨文件出处（如 HT-007 引自财税〔2015〕119号）的官方链接由 `data/policies/reference_sources.csv`（列 `document,source_url`）单独维护。缺失该行时，引用它的条件 blocked、附件不写库，不借用主政策链接。`span.sourceVersion` 仍记该引文支持的政策版本，来源文号由来源行的 title/file_name 表达；本次不改变既有契约语义。
>
> **注意：** 自 `ae15dc0` 起导入器按 `reference_sources.csv` 写入跨文件出处的官方链接；缺该行时引用它的条件判 blocked、附件不写库，不借用主政策链接。HT-007 的 `source_span` 已改为官方逐字句「此项费用总额不得超过可加计扣除研发费用总额的10%」，119 号节选同步换成官方原文句。

**进度（截至 2026-11-10）：** 第 1、2、3、5 项与第 4 项 `freeze-report` 由 #39 实现（已合入，2026-11-06 09:59）；部署文件指纹 `codeFingerprint` 由 #42 补入（已合入，2026-11-07 03:46），复算方式见第 3 项。清单结构以当前 `main` 的 `app.evaluation` 为准，不再引用 #39 的 head 提交。已合入不等于已完成：M1 报告的 ECS 清单分两次——@2 时代那次映射未解决 30 项、`verify` 非零，已作废；@3 在 `a087284…`（119 号旧节选）+ `15f3acd` 上跑出未解决 0、`verify` 0，但输入不是本 PR 最终数据，不得套用。**2026-11-09 已在 ECS 上用最终代码 `main@b9d2371` + 本 PR 完整 `data/` + 实库重出：inputs 清单 `86290c43…`（7 版 / 6 企业 / 148 条条件级标签映射，未解决 0）、`verify --commit b9d2371` 退出 0（一致 10 个政策版本 / 8 个企业；多出的 3 版为 11/05 演练遗留的 drill-policy）、`codeFingerprint` `8fe2368b…`（54 文件）。** M2 未独立登录 ECS 复核上述云端证据；本声明保持草案，正式冻结待证据闭环与 M2 签认。上述执行证据已在 ECS 留档、仓库内未收录（完整索引见 §5.3），M2 签认时需一并归档。
>
> M1 已把消融加载口径改为读 `source_document`（按 `|` 与段数对齐，空时退回政策正文；提交 `200b7fe`（cherry-pick 为 `e7a25b4`，已带入本分支）。归属修正后 RD-001 的 8 段引文在第 7 号正文逐字可查、HT-007 的引文在 119 号节选可查，`unverifiable_criteria()` 为空；引用可证实率分母由 **140 → 146**。这是语义修正的必然结果，不是调数：**11-06 旧跑数的 140 必须与旧哈希 `767c6b…` 一起报告，不得套用 146。**

> **2026-11-08 状态（历史）：** 当日 #51 仍 OPEN（head `1ef0bc6`），本 PR 的导入器仍是旧实现，故当时不验证整条链；M2 对 `--manifest` 缺来源的疑点待 #51 解决。
>
> **2026-11-09 状态（历史）：** #51 已合入 main（merge `cf74ffa`），#36 已合入（`d551ac7`），本分支 `ae15dc0` 已合入 main，导入器与消融代码同 main；ECS 已用最终代码 `main@b9d2371` + 本 PR 完整 `data/` + 实库重出（`--manifest` 13 份、干跑 7/7/0、inputs 清单 `86290c43…`、`verify` 退出 0、`codeFingerprint` `8fe2368b…`）。HT-2021 已按受控流程重导：119 号附件 `7148785…`/282 字，HT-007 引文为官方逐字句，新版本 `58b427a4…`。M2 尚未独立核实，本声明保持草案。
>
> **2026-11-09 ECS 重出证据（M1 提供，M2 未独立核实）：** 部署 `main@b9d2371`（ECS 无 `.git`，该 commit 来自部署记录；磁盘代码身份由 `codeFingerprint` `8fe2368b…` 证明）；`--manifest` 13 份（criteria `01c8821c…`、119号 `7148785…`、labels `62f53bd…`，ECS LF 口径与 M3 一致）；干跑 7/7/0、退出 0；`--apply` 仅 HT-2021 判 conflict，按受控流程重导（全库备份 `31edecb5…`、删除 11/08 旧 run `2235ade8…`、十个触发器 `tgenabled` 恢复 `O`；119 号附件 `7148785…`/282 字，HT-007 引文为官方逐字句，新版本 `58b427a4…`）；回归 run `5ba1223b-7771-4a58-95d6-98173fd6d3d5`（微光软件、HT-2021 + 第 7 号、2026 全年），报告 111 行、带来源 17 条（第 7 号 10 + HT-2021 7），与 `evidenceRefs` 逐条对上。空壳 policy `7b5fcb15…`、drill-policy 三版及临时目录待演示前清理。该组 `verify` 数字对应清理前的库状态；清理后需重跑一次并更新本节（消融读 `data/`，不受影响）。
>
> **2026-11-10 消融补跑（M1 提供，M2 未独立核实）：** 在 ECS `/opt/huice/app/services/api`，代码 `main@b9d2371`（磁盘身份由 `codeFingerprint 8fe2368b…` 证明），数据 `/opt/huice/app/data`，执行 `python -m app.ablation pipeline --as-of 2026-11-06 --out /root/ablation-final.jsonl`：完成 **32 个 case，outcome 失败 0**（outcome 失败数，非分类误判数）。产物 `/root/ablation-final.jsonl` 在 ECS 留档、仓库内未收录：33 行（1 表头 + 32 case），sha256 `fe44e33efc1292c729deae7a3bea829c243305243ab8a557916b7d7f2f6ebe46`；头 `manifest` **12 项**；`cases/labels` 32/146；`as_of` `2026-11-06T12:00:00+08:00`；`scheme/prompt_version/model` 为 `pipeline`/`rule:criteria@1`/`rule-only`。三项关键哈希与 §5.2 一致：`labels.csv 62f53bd7…`、`criteria_review.csv 01c8821c…`、`财税〔2015〕119号.txt 71487854…`。消融侧 12 项 ≠ 导入器 13 份：`Dataset.manifest()` 只收四份 CSV + 被 case 引用的政策正文与出处原文，`reference_sources.csv` 只进导入器签名，按上表分工不是漏项；`model=rule-only` 是因为 `pipeline` 不传模型客户端，判断全由规则引擎完成。本次在 RDS 另建空库 `huice_eval` 供临时 schema（`huice_ablation_<pid>`，跑完自动删），未触碰演示库。
>
> **复现前提与命令口径（2026-11-11 按 M2 复审拆分「历史操作」与「后续复现建议」）：** 上句命令是 M1 的历史执行命令。**工作目录** `services/api`（使 `python -m app.ablation` 解析到本仓库 `app` 包；`Dataset` 默认数据目录由 `REPO_ROOT` 解析为 `/opt/huice/app/data`，可用 `--data` 覆盖）。**Python 环境**为部署 venv。**asOf** `--as-of 2026-11-06` 落为北京时间正午 `2026-11-06T12:00:00+08:00`（不给默认今天）。**输出** `--out` 指定 JSONL；不给则打到 stdout。
>
> **历史操作（2026-11-10，仅记录，不是复现步骤）：** 当时在 ECS 交互 shell 中由 `DATABASE_URL` 换库名派生 `HUICE_EVAL_DATABASE_URL` 后 export，仅在该会话有效、不落盘、不进命令行参数。这只说明那一次怎么做的，不能当作推荐配置来源，也不能证明目标库安全。
>
> **后续复现建议（M1 2026-11-11 提出；`/etc/huice/eval.env` 尚未创建）：**
>
> 1. 在 ECS 上建 `/etc/huice/eval.env`，属主 `root:root`、权限 `600`，只放一行 `HUICE_EVAL_DATABASE_URL=…`；不进仓库、不进 `/etc/huice/api.env`、不写进 systemd unit、不写进命令行。
> 2. 加载：`set -a; . /etc/huice/eval.env; set +a`，仅该会话有效。
> 3. **运行前由目标库自证，不能只比较 DSN 字符串或库名：** `select current_database()` 必须返回预期评测库；须核实目标数据库**及所在实例**（不同 DSN 可能因换名、别名或代理指向同一数据库，同名库也可能分属不同实例）；该库 `public` 下业务表数必须为 0。任一不满足即停止，不跑。
> 4. 代码依据：`app.ablation.pipeline._resolve_dsn` 只认 `HUICE_EVAL_DATABASE_URL` 或 `--dsn`，**不回退 `DATABASE_URL`**（且两者相同时直接拒绝灌库）；单独 `export HUICE_EVAL_DATABASE_URL` 不会赋值，须先按上述方式安全加载有效值。
>
> 评测库前提不变：空闲库、有 `CREATE SCHEMA` 权限且预装 `vector`；脚本建临时 schema `huice_ablation_<pid>`，跑完 `DROP SCHEMA ... CASCADE`。**不得**把业务库地址或真实连接串写进文档、命令行参数或提交。该库的配置来源由 M1 提供（2026-11-10 回复）：不在 `/etc/huice/api.env`、systemd unit 或仓库中，为在 RDS 上手工创建的空库 `huice_eval`；`eval.env` 的建立与目标库自检完成前，上述建议标为「待 M1 建/确认」，不写成已存在或已执行。
>
> **统计口径（2026-11-10 补）：** 文中「失败 0」是**运行 outcome 失败数**，不是分类误判数；本次是 `pipeline`/`rule-only` 补跑，**不代表** `baseline` 对照、指标计算、真实模型调用或完整消融实验全部完成。样本：原始 **34 个唯一组合 / 148 条条件级标签映射**；排除旧版本 `C031`、`C033` 后 **32 个组合 / 146 条标签**（dev 120 / holdout 26）；留出集评分对象始终为 **6 个组合 / 26 条标签**。`conflict` 仍无留出样本，政策级指标仍不报告，非盲测限制不变。

### 5.3 剩余证据待办与证据索引（2026-11-11，M3 整理）

> 依据 M1 2026-11-10 15:01（PR #52）及 2026-11-11 02:13/02:16/02:23（PR #57）回复整理。云端原始产物由 M1 在 ECS 留存，M3 未登录 ECS 复核，也未代做云端待办；未逐项公开的条目按 M2 要求标「待提供/待核实」，不据缺席反推不存在。本节仅整理，不改变 §5.2 的证据要求。

**A. 剩余证据待办表**

| # | 事项 | 负责人 | 状态（截至 2026-11-11） | 说明 |
| --- | --- | --- | --- | --- |
| 1 | 对原始 `report-r2.md` 执行 `freeze-report` | M1 | 已产出，待 M2 核验 | M2 14:32 评审时尚未执行；M1 于 15:01 回复补出：原始 `03082c21…`（111 行）、稳定正文 `8d4c45ab…`、`sourceSha256 03082c21…`、`frozenSha256 8d4c45ab…`、`downloadedAt 2026-11-09T11:06:35Z`、`droppedLines 12`；完整哈希见 B；ECS `/root/` 留档，仓库未收录。 |
| 2 | 完整 trace JSON 及哈希 | M1 | 已提供，待 M2 核验 | `/root/trace-5ba1223b.json`，5012 B，`f6921e7c…`（完整值见 B）；只含阶段摘要与 `{type,id}`，**不作为条件级证据**。 |
| 3 | run 元数据 | M1 | 已提供，待 M2 核验 | 演示库 `assessment_run`/`assessment` 实查（非报告转述），字段见 C；未导出转储。 |
| 4 | 代码/进程身份核对 | M1 | 已提供，待 M2 核验 | 磁盘指纹 `8fe2368b…`（54 文件，完整值见 B；**只对应历史 `b9d2371`**）、清单进程 `processStartedAt 2026-11-09T10:56:46Z`、按启动时间复算（写于其后的代码文件 0 个）。`inputs --process-pid` 只提供启动时间与 mtime 交叉核对；历史 `verify` 接受但不消费该参数，#58 合并后 `verify` 不再接受。**进程内存中的内容仍不能证明**，按 §5.2 第 3 项口径保留。 |
| 5 | 证据索引 | M3 整理 | 已列（见 B） | 依据 M1 提供；ECS 留存与仓库归档分开；不提交转储、密钥或真实敏感材料。 |
| 6 | 清理空壳 policy/drill-policy 后重出快照并 `verify` | M1 | 待办（演示前） | 清理会改变库状态，须重出 `inputs` + `verify` 并保留清理前记录；清理前「10 版本 / 8 企业」不用于证明清理后；消融读 `data/`，不受影响。 |
| 7 | `verify --process-pid` 参数未消费 | M1 | 已修复（#58 已合并 `257bb56`） | 历史 `b9d2371` 的 `_cmd_verify` 接受但不消费该参数，用其补签认无效；#58 将参数移到 `inputs` 专属，合并后新代码的 `verify` 传该参数会报错（退出码 2）。旧指纹对应历史代码，新部署须重算。 |
| 8 | 消融 JSONL 入库归档 | 待定（M1 提供） | ECS 留档、仓库未收录 | `ablation-final.jsonl`，`fe44e33e…`（完整值见 B）；是否入库由所有者决定，不提交转储。 |
| 9 | 独立核验原始材料并完成正式冻结签认 | M2 | 待办 | 本轮（`164ba32`）为文档评审签认；M2 2026-11-11 复审提出的三项修订已由 M3 整改；M2 于 2026-11-11 15:13 复审确认阶段性文档可推进、未发现新的阻塞问题；最终冻结仍未完成。 |

**B. 证据索引（产物 / 所属运行 / 访问位置 / 完整哈希 / 提供人 / 状态）**

| 产物 | 所属运行 | 位置 | 完整哈希 | 提供人 | 状态 |
| --- | --- | --- | --- | --- | --- |
| `report-r2.md`（原始报告） | run `5ba1223b…` | ECS `/root/` | `03082c218fea950a22eb30b7d0c015363658fee40b87deca42f4bc2ce6c843a4` | M1 | ECS 留档，仓库未收录，待 M2 核验 |
| `report-r2.frozen.md`（稳定正文） | 同上 | ECS `/root/` | `8d4c45ab69bd7e4a0d7be0ebf41698eead1316570e188c64daf44b60144050e4` | M1 | 同上；审计四字段见 A#1 |
| `trace-5ba1223b.json` | 同上 | ECS `/root/` | `f6921e7c069ead986f1484dfa2b71d40c345e61c4bef4bb815875e41c8f3b974` | M1 | 同上；仅作可追溯索引 |
| run 元数据 | 同上 | 演示库两表 | —（见 C） | M1 | 库内可查，未导出转储 |
| `inputs-b9d2371-r2.json` | 库状态签认 | ECS `/root/` | `86290c437ad0c80a88d360784597c8a21012b8414fb7e9d76f597c3ca13df98d` | M1 | ECS 留档，待 M2 核验 |
| `ablation-final.jsonl` | 消融补跑 | ECS `/root/` | `fe44e33efc1292c729deae7a3bea829c243305243ab8a557916b7d7f2f6ebe46` | M1 | ECS 留档，待 M2 核验 |
| 代码指纹 | `main@b9d2371` | 可复算 | `8fe2368b6a123e9508cc92e2c821cda242d033b91d3854870ca295cb09095590` | M1 | 仓库代码 + ECS 磁盘一致；对应历史 `b9d2371`，待 M2 核验 |
| 部署提交 `b9d2371` | — | 部署记录 | — | M1 | ECS 无 `.git`，不可用 git 验证 |

> 仓库内已归档的只有 `data/**`、`contracts/`、`services/api/**` 及其可复算的指纹部分；其余均为 ECS 留档，未提交转储、密钥或真实企业材料。

**C. run 元数据（演示库两表实查）**

| 字段 | 值 |
| --- | --- |
| `id` | `5ba1223b-7771-4a58-95d6-98173fd6d3d5` |
| `enterprise_id` | `0e7799f3…`（合成企业） |
| `as_of` | `2026-11-06T12:00+08:00` |
| `status` | `succeeded` |
| `fact_version` | `647e4842…` |
| `model_or_rule_version` | `rule:criteria@1` |
| `created_at` / `finished_at` | 同为 `2026-11-09T19:05:52+08:00`（同一事务落库、同步执行） |
| 两条 `assessment` | HT-2021 政策 `10c0f286…` / 版本 `58b427a4…` → `insufficient_evidence`；第 7 号 `d3165f03…` / 版本 `cd05c775…` → `insufficient_evidence` |

> run 引用的政策根是重导后的新根 `10c0f286…`，不是待清理的空壳 `7b5fcb15…`；版本 `58b427a4…` 与 §5.2 记录一致。

> **2026-11-12 基线复核（M3）：** 当前 `origin/main` 为 `5d1adeb`（2026-11-12 15:29 +0800，合并 PR #63，仅 M3 文档；此前为 `ea44ae0`，合并 PR #61 的 E1 后端、候选契约 `1.1.0-rc.1` 与迁移 009）。本文件 §3/§5.2/§5.3 的指纹、清单与 run 证据仍只对应历史 `b9d2371`；`8fe2368b…` 不能证明当前 `main`。若正式冻结改以当前 `main` 为基线，须由 M1 重新部署、重算 `codeFingerprint` 并重出 `inputs`/`verify`，再由 M2 独立核验；本次未执行这些步骤，也没有新增评测证据。2026-11-06 的留出集试跑仍按 §6 记为“不是独立验证”，不因 #61/#63 合并而改变；M2 的正式冻结签认仍未完成。

## 6. 锁定边界声明（避免误读）

- 本声明**不是**「已冻结」，也不是「标注时盲测」。
- 现有记录显示：v2 于 `df05346`（2026-11-02 17:37）形成，此后 `labels.csv` 未再修改；`ACCEPTANCE.md` 未见 P0 验收记录。**这只能证明现有记录中没有 P0 验收记录，不能单独证明此前没有其他试跑或标签使用。**
- 是否在任何相关评测/调参前锁定，尚需各成员确认；本声明不作此推断。
- 第 2 轮仲裁的理由记录在 `criteria_review.csv` 的 HT-007 `adjudication_note`（政策原文与规则语义）；**「是否参考过引擎输出」尚需实际参与者确认**，本声明不作此推断；不主张标注过程具备统计意义上的盲测条件。
- 正式冻结以 M2 签认时间为准，**不以历史提交时间代替签认时间**。
- **留出集试跑历史（M1 自述）：** 2026-11-06 16:08–16:29 以 DeepSeek 跑过一次完整对照（`asOf=2026-11-06`，32 个 case / 146 条标签，dev 120 / holdout 26），输出仅 M1 本人看过。**该次试跑使用修改前的 `criteria_review.csv`（`767c6b…`），引用可证实率分母为 140（RD-001 的 6 个 case 被排除）**；此后 `labels.csv` 未改（最后改动 2026-11-02），`criteria_review.csv` 于 2026-11-07 修正 `source_version` 归属（`f7db3424…`），2026-11-08 将 HT-007 的 `source_span` 改为官方逐字句（`01c8821c…`），`source_document` 保持空，不改条件与标签。修正后不可验证集合为空、分母为 **146**；**旧跑数 140 只能与旧哈希/旧数据一起报告，不得把新分母套到旧跑数**。据该次结果只修了实现缺陷（抽取提示词算子键名、指标表百分比渲染）与三处口径，未改条件与标注。故该次结果既不能归属到当前输入，也不作为留出集的独立验证；后续若以留出集下结论，须注明「实现曾据该次结果修过缺陷」，并说明当前输入已在试跑后变更。

## 7. 签认

| 角色 | 动作 | 状态 | 起草/复核日期 | 正式签认时间 |
| --- | --- | --- | --- | --- |
| M3 | 起草本声明、提供指纹与复现方法 | 已起草；2026-11-09 按 M1/M2 复审意见修订；2026-11-10 按 PR #52 评论补消融结果与措辞；2026-11-10 按 M2 本轮评审补 §3 换行口径与导出复现、§5.2 复现前提、§5.3 剩余证据待办与索引；2026-11-11 按 M2 复审拆分历史/后续评测库配置、补 §5.3 完整哈希、统一 `--process-pid` 与指纹对应口径 | 2026-11-04；2026-11-11 | — |
| M2 | 复核正式表述、评测协议与政策级/输入快照口径 | 已完成 2026-11-10 本轮文档评审并签认修订方向（审查基线 `164ba32`，见 PR #57 评论）；2026-11-11 复审（head `4fe52ce`）提出三项修订：评测库配置步骤、证据索引完整哈希、`--process-pid` 口径；M3 已整改；2026-11-11 15:13 复审（基准 head `6de4aee`）确认阶段性文档可推进、未发现新的阻塞问题，正式冻结仍未签认；剩余原始证据核验待闭环，**未完成正式冻结** | 2026-11-07（head 原 `0e4a54d` / 现 `8c975dd`）；2026-11-10（`164ba32`）；2026-11-11（`4fe52ce`） | — |
| M1 | 提供输入映射、运行参数与结果导出方式，确认运行时数据与快照一致 | 已提供 #39、`200b7fe`（cherry-pick `e7a25b4`）与 #51（head `1ef0bc6`，已合入 main `cf74ffa`；消融实际文件签名、导入器 `--manifest`）。ECS 清单分两次：@2 时代 30 项未解决、`verify` 非零，已作废；@3 在 `a087284…`（119 号旧节选）+ `15f3acd` 上未解决 0、`verify` 0，但输入非本 PR 最终数据，不得套用。最终组合已于 2026-11-09 重出：inputs 清单 `86290c43…`、`verify` 退出 0（10 版本 / 8 企业）、`codeFingerprint` `8fe2368b…`；消融已于 2026-11-10 补跑（32 case / outcome 失败 0，JSONL `fe44e33e…`，头 `manifest` 12 项）；2026-11-10 补出 `freeze-report`（原始 `03082c21…` / 稳定正文 `8d4c45ab…` / `droppedLines 12`）、trace `f6921e7c…`、run 元数据、代码/进程身份与证据索引（见 §5.3）；2026-11-11 补出 `inputs-b9d2371-r2.json` 完整哈希 `86290c43…`、`eval.env` 独立评测库建议（尚未创建）与 #58（已合并 `257bb56`，`verify` 不再接受 `--process-pid`）；清理后重跑快照待办；M2 未独立核实 | — | — |

> 正式冻结以 M2 在本表填写**签认时间**为准；本文件状态改为「已复核」前，不得对外称已冻结。

**M2 复核问题：**

1. 「锁定边界」的限定表述（§6）是否可接受？
2. 本轮先不报政策级指标、按 §5.1 完成前置条件后再报，是否可接受？
3. §5.2 的输入/执行快照要求是否足够；待 M1 提供后是否可作为冻结前置？
4. 指纹与版本时间线是否仍足够复现（§3–§4）？

> 2026-11-10 更新：M2 已完成本轮文档评审并签认修订方向（见 PR #57 评论）；上述 1–4 项本轮未被单独驳回，正式冻结前仍待原始证据核验后确认。
>
> 2026-11-11 更新：M2 复审（head `4fe52ce`）提出三项修订——历史/后续评测库配置分离、§5.3 完整哈希、`--process-pid` 口径；M3 已整改并回告；M2 于 2026-11-11 15:13 复审（基准 head `6de4aee`）确认阶段性文档可推进、未发现新的阻塞问题。本轮仍为阶段性文档修订，不改变正式冻结状态。

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
