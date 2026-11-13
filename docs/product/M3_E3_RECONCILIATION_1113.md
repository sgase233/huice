# M3 E3 对账差额样例（2026-11-13）

> **性质：M3 提供的业务输入与预期，用于 M1/M2 对账，不是验收通过记录，不是三方签收，也不是最终契约。** 本文只补 [COMPLETENESS_SPEC.md](COMPLETENESS_SPEC.md)、[METRICS_SPEC.md](METRICS_SPEC.md)、[E3A_BUSINESS_REVIEW.md](E3A_BUSINESS_REVIEW.md) 在 #68 交付中**尚未给出具体输入/预期**的差额；已能直接使用的公式、七态、样例 A—E、N1—N9、逐日表不复制重做。
>
> **基线：** `origin/main` 本轮开始时 `c5fe1e9`（PR #68 合并）；PR #67 的 E3 方案已合并，但 D4/D5、VIS 契约、VIS-06 与联合验收仍未签收。本文所有样例均为**合成数据**，不对应真实企业或真实政策冲突。
>
> **边界：** 不修改正式契约、迁移、前端代码、评测输入和 `data/**`；不执行数据库/接口/浏览器/真实模型；`not_due` 方案 C、`d4.static.v1` 静态引用等仍标“候选/待成员确认”，不得写成三方结论。

## 1. #68 已交付、直接引用的部分

| 主题 | 权威位置 | 本文如何使用 | 本文不重复什么 |
| --- | --- | --- | --- |
| 完整度公式、七种单位状态、分组、样例 A—E、N1—N9 | [COMPLETENESS_SPEC.md](COMPLETENESS_SPEC.md) §1—§7 | 只补样例 C 的行级输入、静态引用下的分母变化、样例 E 的算术复核；`not_due` 只做候选清单 | 不重写七态定义、不复制样例 A/B/D 全文 |
| 审核工作量、日终待办、运行趋势、逐日合成案例 | [METRICS_SPEC.md](METRICS_SPEC.md) §2—§6、§9 | 只补事件级 UTC 时刻、日终半开区间、跨午夜运行、`null`/0/`partial` 对账表 | 不重写指标定义、不复制 §6 的完整逐日表 |
| 结果分布、逻辑树、轨迹的静态评审与页面要求 | [E3A_BUSINESS_REVIEW.md](E3A_BUSINESS_REVIEW.md) §2—§5、§7 | 补一个含四态+`stale` 的合成 run、GXJS v1/v2 版本切换样本、`manual_review` 叶子 | 不重写静态评审结论、不把未执行项写成通过 |
| 日期来源逐字段清单与 PR #69 复核 | [M3_DATE_SOURCE_HANDOFF_1113.md](M3_DATE_SOURCE_HANDOFF_1113.md) | 作为时间线/逻辑树样本的日期与版本依据 | 不重复 PR #69 发现清单 |

### 1.1 依据等级（避免把方案/作者报告/契约/签收混写）

| 等级 | 本轮内容 | 能否作为验收依据 |
| --- | --- | --- |
| **方案/草案** | PR #67 的 E3 方案（已合并，仅文档）；PR #69 的 `draft-pending-m3` 回填数据 | 只能作实现/评审输入；不能写成 E3 验收或签收 |
| **作者报告** | M1 报告的本地测试、CI、云端部署/迁移证据 | 不能替代 M3 实际对账、M2 页面证据或负责人签认 |
| **正式契约** | `1.1.0-rc.1`；VIS-01/04/05/06 的 `dateProvenance`、D5 事件/趋势字段尚未进入正式契约 | 未定稿字段不能按已实现消费；页面不能提前依赖 |
| **已签收结论** | D4/D5/VIS-06、VIS-AT-01/03/05/06/07/08、时间线 VIS-AT-04 均无三方签收 | 本文所有预期均为“待对账”，不是“已通过” |

### 1.2 D4/D5 当前状态摘要（不替代规格）

| 主题 | 当前结论 | 状态 | 仍待 |
| --- | --- | --- | --- |
| D4 计数单位与七态 | `(FactKey, period_start, period_end)`；`filled`/`filled_unverified`/`answered_unknown`/`not_answered`/`not_applicable`/`applicability_unknown`/`not_due` | M3 草案；#68 已交付，未三方签收 | M1 实现 VIS-01；M2 展示；三方签收 |
| D4 `not_due` | M3 业务复核采用方案 C：季度类按季度结束、期初/期末不分层；年度/跨年类按 `period_start > asOf` | **候选，待 M1/M2 确认**；定稿前不实现 | M1 确认接口精度；M2 确认文案；三方定 `definitionVersion` |
| D4 静态引用 v1 | `definitionVersion="d4.static.v1"`、`applicabilityMode="static"`；页面写“按政策引用统计，可能包含不适用项”；不得称“零误判”；样例 B/C 不宣称通过 | **有条件接受（过渡）**，不是签收 | M1 返回版本/模式；M2 固定文案；三方确认过渡范围 |
| D4 scope/asOf | 画像页排除 `closed` 历史样本；run 页用该 run 的 `policyVersionMap` + `asOf`；分子读当前事实，不能宣称重现历史 | M3 建议；M1 方案已按此写，未签收 | M1/M2 确认接口与页面默认值；三方确认 `definitionVersion` |
| D5 审核完成事件 | 首版“人工审核完成”= 已识别审核员会话成功发布；`review_revision` 正常为 1；基线 `revision=0` 不计完成；历史不可追溯返回 `null` | M3 建议；`policy_review_event` 尚未落地 | M1 实现事件/基线/事务/幂等；M2 展示；M1/M3 确认 |
| D5 日终与运行 | 日终 `[当日 00:00, 次日 00:00)`；当天 `pendingAtEndOfDay=null`；运行按开始日归日；跨午夜完成后可更新开始日结果；`unfinished` 不并入失败 | M3 建议；采集未实现/未验收 | M1 实现快照/生命周期；M2 确认 `null`/`partial`/`dayOpen` 展示 |
| D5 API-12/状态 | API-12 计划 `409 RUN_NOT_COMPLETED`（行不存在才 404）；`queued` 当前不落行；`interrupted` 首版仅单进程 | 方案/待实现 | M1 落契约与测试；M2 复核错误态；不把多进程写成已支持 |

## 2. 完整度对账差额

### 2.1 样例 C 的行级输入（补充 COMPLETENESS_SPEC §7 样例 C）

**输入（合成）：**

- `scope = SZ-GRANT-RD-SUBSIDY-2026`；`asOf = 2026-11-10`（北京时间）。
- 企业成立日期 `2022-03-01`，按现有 AST 走“成立满两个会计年度”分支 A；`definitionVersion = branch-aware.v1`（仅用于本样例，不是已定稿版本）。
- 事实行如下；“无行”表示数据库没有该 `(FactKey, period)` 记录，不是值为 0。

| 单位 | 期间 | 事实行 | 值 | `verified` | 预期状态 | 入分母 | 入分子 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `region` | 2026 画像年度 | 有 | `440300` | `false` | `filled_unverified` | 是 | 是 |
| `independentLegalEntity` | 2026 画像年度 | 有 | `true` | `true` | `filled` | 是 | 是 |
| `establishedDate` | 2026 画像年度 | 有 | `2022-03-01` | `true` | `filled` | 是 | 是 |
| `creditRecord` | 2026 评估年度 | 有 | `false` | `false` | `filled_unverified` | 是 | 是 |
| `rdAddDeductionClaimed` | 2024-01-01—2025-12-31 | 有 | `true` | `true` | `filled` | 是 | 是 |
| `rdExpenseIncrease` | 2025-01-01—2025-12-31 | 无 | — | — | `not_answered` | 是 | 否 |
| `rdExpense` | 2025-01-01—2025-12-31 | 无 | — | — | `not_applicable`（分支 B） | 否 | 否 |
| `rdAddDeductionClaimed` | 2026-01-01—2026-12-31 | 无 | — | — | `not_applicable`（分支 B） | 否 | 否 |

**计算过程：**

- 分母 = 前 6 个单位（含 `not_answered` 的 `rdExpenseIncrease`），**不含**分支 B 的 2 个单位 = **6**。
- 分子 = 前 5 个已填写单位 = **5**。
- 比例 = 5 ÷ 6 = 0.833333… → **83.3%**（页面若取整写 83%，不得写 83.33% 而不说明精度）。
- `creditRecord=false` 是“明确不良好”的已填写事实；`verified=false` 不改变分子/分母；两者都不能显示为“未填写”。

**依据：** COMPLETENESS_SPEC §3（七态）、§5.1—§5.4（逐字段）、§7 样例 C；`criteria_review.csv` 的 RDSUB 条件分支。

**依赖：** M1 的 VIS-01 必须能返回每个 `(FactKey, period)` 的单位状态，并能区分“无行”与“行存在且值为 null”；M2 必须显示 `filled_unverified`、`not_answered`、`not_applicable` 三种不同文案。

**候选/待确认：静态引用 `d4.static.v1` 下的分母变化。** 如果首版按“政策引用静态统计”而不判断分支可达性，分支 B 的 `rdExpense(2025)` 与 `rdAddDeductionClaimed(2026)` 会进入分母，均为 `not_answered`；预期变为 **5/8 = 62.5%**，而不是 5/6。页面必须写“按政策引用统计，可能包含不适用项”，且**不得**用 62.5% 或 83.3% 宣称样例 C 通过。该差异属 `definitionVersion` 变更，需 M1/M2/M3 确认。

### 2.2 样例 E 的算术复核（引用 COMPLETENESS_SPEC §7 样例 E，不重复其全文）

**输入：** `scope = CN-TAX-SME-2023-012`；`asOf = 2026-11-10`；四个季度键 × Q1—Q4 = 16 个单位；Q1—Q3 的 12 个单位均已填写；Q4 的 4 个单位未到期；`taxableIncome(2026)` 未填写；SME-001 为 `manual_review`，不引用 FactKey。

**计算：**

- 局部季度字段：Q4 的 4 个单位 `not_due` 不入分母；Q1—Q3 的 12 个单位已填写 → **12/12**。
- 整政策 scope：12 个已到期季度 + 1 个年度 `taxableIncome(2026)` = **13** 个适用单位；已填 **12**；比例 12 ÷ 13 = 0.923076… → **92.3%**。
- 4 项 `not_due` 与 1 项人工核查（SME-001）单列，不混入分母。

**对照候选：** 若改为“季度期初按可观察日提前到期”，2026Q4 的 2 个期初单位会提前进入分母，整政策分母从 13 变 15，比例变为 12/15 = **80.0%**。这是 §3.1.1 明确不在 v1 执行的 `definitionVersion` 变更；当前只作为反例，**不得**写成 M3 结论或验收数字。

**依据：** COMPLETENESS_SPEC §3.1、§3.1.1、§5.2、§7 样例 E。

**依赖：** M1 固定 `asOf` 精度和 `period_end` 日期型语义（季度结束日当天仍未到期、次日 00:00 到期）；M2 显示“未到期”与“未填写”的区别。

### 2.3 `0`、`false`、`null`、无行的最小对账

> 本节只补“同一字段不同输入”的预期状态；公式与七态定义见 COMPLETENESS_SPEC §1—§3、§6，不重复。

| 输入 | 事实行 | 预期状态 | 是否入分子/分母（分子/分母） | 说明 |
| --- | --- | --- | --- | --- |
| `taxableIncome=0` | 有值 `0` | `filled` | 入/入 | 真实零营收/所得是事实，不是缺失；不能显示“未填写” |
| `rdActivity=false` | 有值 `false` | `filled` | 入/入 | 明确否定是事实，不是缺失 |
| `creditRecord=false` | 有值 `false` | `filled` | 入/入 | 资格上可能不满足，但完整度只问“填没填” |
| `rdAddDeductionClaimed=null` | 行存在、值为 `null` | `answered_unknown` | 不入/入 | “已答：尚未申报/不确定”，不是 `false`，也不是“未填写” |
| `revenue(2024)` 无行 | 无行 | `not_answered` | 不入/入 | 未填写；页面显示“未填写”，不显示 0 |
| `region=440300, verified=false` | 有值 | `filled_unverified` | 入/入 | `verified=false` 不改变分子/分母；显示“已填写（未核实）” |
| 未来季度 `assetsEnd(2026Q4)` | 无行或已有值 | `not_due`（候选） | 不入/不入 | 未到期优先于“已填写”；已有值另列“已提前填写（未到期）” |
| 营收未填时的 `equityFinancing` | 无行 | `applicability_unknown` | 不入/不入 | 无法判断 OR 分支；单列，整图不显示总百分比 |

**依赖：** M1 必须保留“行存在且值为 null”的行；若读取层把四列全空的行丢弃，`answered_unknown` 与 `not_answered` 无法区分。M2 必须为六种以上状态提供不同文案，不能合并成“缺失”。

### 2.4 `not_due` 候选边界（引用 N1—N9，标“候选/待成员确认”）

> **状态：候选，待 M1/M2 确认；定稿前 M1 不实现 `not_due` 分支，本文不把任何一版结果计入验收。** 下表只列最容易实现错的边界，完整 N1—N9 见 COMPLETENESS_SPEC §3.1。

| 编号 | `asOf`（Asia/Shanghai） | 单位 | 期间 | 预期 | 入分母 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| N1 | 2026-04-01 00:00:00 | 2026Q1 `employeeCountStart` | 2026-01-01—2026-03-31 | 到期 | 是 | Q1 已于 3-31 结束；4-1 起可统计 |
| N2 | 2026-03-31 23:59:59.999999 | 2026Q1 `employeeCountEnd` | 2026-01-01—2026-03-31 | `not_due` | 否 | 季度结束日当天仍未结束；4-1 00:00 才到期 |
| N3 | 2026-11-10 | 2026Q4 `assetsEnd` | 2026-10-01—2026-12-31 | `not_due` | 否 | 与样例 E 一致 |
| N4 | 2026-11-10 | `taxableIncome(2026)` | 2026-01-01—2026-12-31 | 到期 | 是 | 当前年度需要填写 |
| N5 | 2025-12-31 23:59:59.999999 | `taxableIncome(2026)` | 2026-01-01—2026-12-31 | `not_due` | 否 | 年度尚未开始 |
| N6 | 2026-11-10 | `rdAddDeductionClaimed` | 2024-01-01—2025-12-31 | 到期 | 是 | 跨年区间已开始且已结束 |
| N7 | 2026-11-10 | `rdAddDeductionClaimed` | 2027-01-01—2028-12-31 | `not_due` | 否 | 未来区间 |
| N8 | 2026-11-10 | 2026Q4 `employeeCountStart` | 2026-10-01—2026-12-31 | `not_due` | 否 | 期初值可观察，但季度未结束；v1 不提前入分母 |
| N9 | 2027-01-01 00:00:00 | 2026Q4 `employeeCountStart` | 2026-10-01—2026-12-31 | 到期 | 是 | 季度结束后，提前录入的值才计入 |

**依赖：** M1 在接口层固定 `asOf` 是日期还是带时区时间戳；若 `period_end` 是日期型（含当日），按“当天未到期、次日到期”实现。M2 显示“未到期”，不与“未填写”混用；未到期单位若已有值，另列“已提前填写（未到期）”，不计入比例。

## 3. 结果分布对账差额（VIS-AT-03）

### 3.1 合成 run 输入

> **这是页面/接口对账用合成 run，不是真实政策冲突样本。** 其中 `conflict` 是合成状态，**不得**据此宣称系统已有真实 `conflict` 数据；真实 `conflict` 仍为 0，只有单元测试覆盖。下表 `id` 是**业务演示标签**，不是真实 HTTP 响应值（正式 `AssessmentRun.id` 是 UUID）；`policyId`/版本取自 `source_register.csv` 的登记业务键，`result`/`validity` 为合成值。

| 项 | 值 |
| --- | --- |
| `id` | `RUN-RECON-001`（**业务演示标签**；正式响应是 UUID，不用此串） |
| `createdAt` | `2026-11-13T04:00:00Z`（= 北京时间 2026-11-13 12:00:00；`createdAt` 是正式 `AssessmentRun` 字段，但它是 **run 创建时间，不是读取时时钟**） |
| `factVersion` | 固定 SHA-256（合成，不落真实值）；只固定 run 输入的事实版本，不固定读取时的失效判定 |
| 评估期间 | 2026-01-01 至 2026-12-31 |
| 页面条件 | `runFresh=true`，`resultNeedsRefresh=false` |
| `assessments.length` | 6 |
| 未分析候选 | 0（不进入 `assessments`） |

> **`generatedAt` 不是正式契约字段：** 正式 `AssessmentRun`（`contracts/openapi.yaml`）只有 `id`/`enterpriseId`/`period`/`asOf`/`createdAt`/`policyVersionMap`/`factVersion`/`ruleVersion`/`supersedesRunId`/`assessments`，且 `additionalProperties: false`，没有 `generatedAt`。“响应生成时间”若需区别于 `createdAt`，属**新增契约字段（候选）**，须在 `1.2.0-rc.1` 逐项确认后再写，当前不指向接口返回 `generatedAt`。
>
> **`stale` 是读取时重算，`createdAt`/`factVersion` 固定不能保证两次读取一致：** `stale` 的失效来源除“企业事实变化”外，还包括**时间边界**（申报窗口关闭、政策有效期到期）和**政策版本/状态变化**。`createdAt` 是 run 创建时间、`factVersion` 只是 run 输入的事实版本，二者都不固定读取时的政策状态与当前时钟。**即使读取同一 run、固定 `createdAt`/`factVersion`，且两次读取之间没有新的失效事件或政策版本变化，仍不足以保证 `stale` 计数不变——读取时钟前进本身就可能跨过申报窗口关闭、政策到期等时间边界。** 因此，“两次读取的 `stale` 计数应一致”的完整前提是：
>
> 1. 读取同一 run，并固定该 run 的相关输入（`factVersion`、`policyVersionMap`/`asOf` 等；`createdAt` 只标识 run 创建时间，不是读取时钟）；
> 2. 控制两次读取的测试时钟，或确保两次读取之间没有跨越会影响该 run 的时间边界（申报窗口关闭、政策有效期到期等）；
> 3. 两次读取之间没有影响该 run 的失效事件（如企业事实变更）或政策版本/状态变化。
>
> 当前正式 `AssessmentRun` 没有读取时点字段；“本次读取时点”若需记录，属**新增契约字段（候选）**，须在 `1.2.0-rc.1` 逐项确认，不能写成已有接口字段。独立于 §3.1 六项分布的跨时间边界反例见 §3.2。

| # | `policyId` | 版本 | 预期 `result` | 预期 `validity` | 说明 |
| --- | --- | --- | --- | --- | --- |
| A | `CN-TAX-SME-2023-012` | 财政部 税务总局公告2023年第12号 | `satisfied` | 正常 | 小型微利 |
| B | `CN-TAX-RD-2023-007` | 财政部 税务总局公告2023年第7号 | `satisfied` | `stale` | 合成：run 后企业事实变更；仍保留原四态 |
| C | `SZ-GRANT-SRDI-2025` | 深工信规〔2025〕8号 | `not_satisfied` | 正常 | 专精特新 |
| D | `SZ-GRANT-HIGHTECH-2021` | 深科技创新规〔2021〕5号 | `insufficient_evidence` | 正常 | 高企培育（manual_review 条件） |
| E | `SZ-GRANT-RD-SUBSIDY-2026` | 深科创规〔2026〕1号 | `conflict`（合成） | 正常 | 仅用于页面四态；不是真实冲突 |
| F | `SZ-GRANT-SMART-2025` | 深工信规〔2025〕4号 | `not_satisfied` | 正常 | 智能终端 |

### 3.2 预期计数与计算

| 指标 | 预期 | 计算 |
| --- | --- | --- |
| 总数/分母 | **6** | `assessments.length = 6`；不含未分析候选 |
| `satisfied` | **2** | A、B |
| `not_satisfied` | **2** | C、F |
| `insufficient_evidence` | **1** | D |
| `conflict` | **1** | E（合成） |
| 四态之和 | **6** | 2 + 2 + 1 + 1 = 6，等于分母 |
| `stale` | **1** | 单独过滤 `validity='stale'`；B 同时计入 `satisfied` |
| 四态之和 + stale | **7 ≠ 6** | 证明 `stale` 不是第五态，不能加到四态里 |

**预期展示：** “统计本次分析的 6 项政策，不含未分析候选”；四态数量分别显示；`stale` 单独标注“失效与上述结果状态可同时存在”。不得把 `stale` 并入 `failed`/`insufficient_evidence`，也不得因为筛选列表而改变分布数。

**依据：** E3A_BUSINESS_REVIEW §2.1（四态、`stale` 单独计算、`total=assessments.length`）；#67 方案 §2.1（`assessment` 主键 `(run_id, policy_id)`、`assessments` 为 `min_length=1`、`stale` 读取时重算）。

**依赖：** M1 的 `/assessment-runs/{id}` 必须让 `assessments` 恰好包含这 6 项、不混入候选；四态计数由 `run.assessments` 决定，只要读同一 run 就稳定。**`stale` 例外**：它是读取时重算的独立字段，失效来源包括企业事实变更、时间边界（申报窗口关闭、政策有效期到期）和政策版本/状态变化。对账样本若要断言两次读取 `stale` 计数一致，必须同时满足：①读取同一 run 并固定相关输入；②控制两次读取的测试时钟，或确保两次读取之间没有跨越会影响该 run 的时间边界；③两次读取之间没有影响该 run 的失效事件或政策状态变化。仅固定 `createdAt`/`factVersion` 不足以保证；`createdAt` 是 run 创建时间，不是读取时钟。M2 的分布图只读 `run.assessments`，不读当前筛选后的列表；空态只在 `total=0` 时显示“本次分析没有结果”，不能显示 0%。

**`stale` 跨时间边界反例（独立样本，说明 `createdAt` 不能当读取时时钟）：** §3.1 的样本 B 初始已是 `stale`（run 后企业事实变更），不能再用来说明“由正常变 `stale`”。这里另设一组**独立合成反例样本 G**，只用于说明时间边界，**不并入 §3.1/§3.2 的 6 项分布计数**：

- 同一 run `RUN-RECON-BOUNDARY`（业务演示标签，正式响应是 UUID）；`createdAt = 2026-11-13T04:00:00Z`（run 创建时间）、`factVersion` 固定；两次读取之间没有新的失效事件，也没有政策版本/状态变化，**只推进测试时钟**。
- 样本 G 的政策版本使用合成日期（不对应真实政策）：申报窗口关闭时刻 `applicationWindow.closesAt = 2026-11-14T15:59:59Z`（= 北京时间 2026-11-14 23:59:59），有效期终点 `effectiveTo = 2026-11-14`（北京时间；现行实现按 `effective_to < 当前北京日期` 判过期，即 11-15 00:00 起过期）。
- 第一次读取时 G 的 `validity = current`（正常）；第二次读取跨过上述边界后变为 `stale`；四态 `result` 不变，仅 `validity`/`staleReasons` 重算。

| 读取 | 测试时钟（UTC） | 北京时间 | 样本 G 预期 `validity` | 预期 `staleReasons` |
| --- | --- | --- | --- | --- |
| 第一次 | `2026-11-14T02:00:00Z` | 2026-11-14 10:00 | `current`（正常） | `[]` |
| 第二次 | `2026-11-15T02:00:00Z` | 2026-11-15 10:00 | `stale` | `window_closed`、`policy_expired` |

第二次读取时 `createdAt`/`factVersion` 一字未变，两次读取之间也没有失效事件或版本变化；唯一变化是读取时钟跨过了窗口关闭/政策到期边界。因此分布图的「本次读取时点」必须在响应/页面记录（候选字段，正式契约暂无），近期日期可能随失效事件变化；固定 `createdAt` 只固定“这是哪个 run”，不固定“此刻政策是否已失效”。

## 4. 资格逻辑树对账差额（VIS-AT-01）

### 4.1 GXJS v1/v2 版本固定与切换

**输入（合成 run）：**

- `policyId = CN-TAX-GXJS`；run 钉住 `policyVersionId = v1（国科发火〔2008〕172号）`。
- `assessment.criteriaResults` 含 `criterionId = GXJS-V1-001`；`detail` 当前政策版本为 v2（国科发火〔2016〕32号）。
- 两版表达式均在 `data/policies/criteria_review.csv` 已仲裁；比例条件均为 `manual_review`（近三个会计年度口径无事实键）。

**预期：**

| 操作 | 预期树内容 | 预期 `criterionId` | 预期原文定位 | 不应出现 |
| --- | --- | --- | --- | --- |
| 查看 run/assessment（钉住 v1） | v1 三档：`revenue < 50,000,000`；`50,000,000 ≤ revenue ≤ 200,000,000`；`revenue ≥ 200,000,000`；每档比例条件为 `manual_review`（6%/4%/3% 原文） | `GXJS-V1-001` | `source_version=国科发火〔2008〕172号`；`source_page_or_section=第十条（四）` | 不把 v2 的 5% 树套到 v1 结果上 |
| 切换详情到 v2 | v2 三档：`revenue ≤ 50,000,000`；`50,000,000 ≤ revenue ≤ 200,000,000`；`revenue ≥ 200,000,000`；每档比例条件为 `manual_review`（5%/4%/3% 原文） | `GXJS-V2-001` | `source_version=国科发火〔2016〕32号`；`source_page_or_section=第十一条（五）` | 不把 v2 树写成“v1 不满足、v2 满足”的资格结论 |

**边界值检查：** 企业 `revenue = 50,000,000`（恰好 5,000 万元）时：

- v1 第一档 `lt 50,000,000` 为 false；第二档 `gte/lte` 为 true；比例叶子仍是 `manual_review`。
- v2 第一档 `lte 50,000,000` 为 true，第二档 `gte/lte` 也为 true；存在边界重叠；比例叶子仍是 `manual_review`。
- 因此页面只能展示树结构与 `manual_review` 原因，**不得**在节点上编造“满足/不满足”；整条仍为 `insufficient_evidence` 类结果。

**依据：** `criteria_review.csv` 的 `GXJS-V1-001`、`GXJS-V2-001`（`source_version`、`source_page_or_section`、`source_span`、`adjudicated_expression`）；E3A_BUSINESS_REVIEW §3.1—§3.2；POLICY_SCOPE 的版本对照“演示暂停”说明。

**依赖：** M1 的 `get_run` 用 `assessment.policy_version_id` 取该 run 钉住的版本；M2 详情页按当前选中版本取表达式，不能拿当前版本树解释旧 run；服务端不提供节点级结果，页面不得自行计算。

### 4.2 `manual_review` 叶子样本（HT-007）

**输入：** `policyId = SZ-GRANT-HIGHTECH-2021`；`criterionId = HT-007`；`source_version = 深科技创新规〔2021〕5号`；`source_page_or_section = 一、（一）6.其他相关费用`；`source_span = 此项费用总额不得超过可加计扣除研发费用总额的10%`；`adjudicated_expression = {"op":"manual_review","reason":"其他相关费用占可加计扣除研发费用总额的比例（不超过10%，财税〔2015〕119号）无对应事实键，须人工核查"}`。

**预期：**

- 叶子显示“人工核查”及 reason 文本；**不得**显示为“不满足”“空白”或“已通过”。
- 该叶子不引用 FactKey，不计入完整度分母；另列人工核查项。
- 原文定位显示 `source_version`、`source_page_or_section`、`source_span`；不能只显示对象引用或 URL 首页。

**依据：** `criteria_review.csv` HT-007；COMPLETENESS_SPEC §5.1/§5.5 的 `manual_review` 口径；E3A_BUSINESS_REVIEW §3.1—§3.2。

**依赖：** M1 的 criteria 结果按 `criterionId` 与评估结果匹配；M2 的树组件对 `manual_review` 有独立标签，不把它归入四态之一；原文定位字段随版本返回。

## 5. 趋势对账差额（D5）

> **已有内容：** METRICS_SPEC §6.1/§6.2 已给出逐日审核/运行表和手算；§9.2 已给出逐日对账数字。本节**不复制**完整表，只补事件级 UTC 时刻、日终半开区间、跨午夜运行和 `null`/0 状态对账，供 M1 用 SQL 结果直接比对。

### 5.1 基准与事件级输入

- `T0 = 2026-11-20 10:00:00 Asia/Shanghai = 2026-11-20T02:00:00Z`。
- 数据库存 UTC；日统计按 `Asia/Shanghai` 切日；区间为 `[当日 00:00:00, 次日 00:00:00)`。
- 下表是合成事件；“基线”表示 `actor_kind=baseline`、`review_revision=0`，**不计入** `reviewsCompleted`。

| 对象 | 事件 | 北京时间 | UTC | 归入北京日 | 预期 |
| --- | --- | --- | --- | --- | --- |
| V1 | `imported_at` | 11-01 09:00 | `2026-11-01T01:00:00Z` | 11-01 | `newDrafts=1` |
| V1 | 发布（上线前） | 11-02 15:00 | `2026-11-02T07:00:00Z` | 11-02 | 基线事件；`reviewsCompleted` 不计 |
| V2 | `imported_at` | 11-05 09:00 | `2026-11-05T01:00:00Z` | 11-05 | `newDrafts=1` |
| V2 | 审核员发布 | 11-21 09:00 | `2026-11-21T01:00:00Z` | 11-21 | `reviewsCompleted=1` |
| V3 | `imported_at` | 11-20 10:30 | `2026-11-20T02:30:00Z` | 11-20 | `newDrafts=1` |
| C1→V4 | 候选提交生成版本 | 11-20 11:00 | `2026-11-20T03:00:00Z` | 11-20 | `newDrafts=1` |
| V4 | 审核员发布 | 11-20 11:20 | `2026-11-20T03:20:00Z` | 11-20 | `reviewsCompleted=1` |
| V4 | 同键重放 | 11-20 11:25 | `2026-11-20T03:25:00Z` | 11-20 | 不新增事件、不重复计数 |
| C3→V5 | 候选提交生成版本 | 11-21 23:50 | `2026-11-21T15:50:00Z` | 11-21 | `newDrafts=1`；11-21 日终仍待办 |
| V5 | 审核员发布 | 11-22 00:10 | `2026-11-21T16:10:00Z` | 11-22 | `reviewsCompleted=1`；不计入 11-21 已发布集合 |
| V6 | `imported_at` | 11-22 09:00 | `2026-11-22T01:00:00Z` | 11-22 | `newDrafts=1` |
| C2 | 丢弃 | 11-20 13:00 | `2026-11-20T05:00:00Z` | 11-20 | 不生成版本，不计 |

**预期逐日（与 METRICS_SPEC §9.2 一致）：**

| 日期（北京） | `newDrafts` | `reviewsCompleted` | `pendingAtEndOfDay` | 覆盖 |
| --- | --- | --- | --- | --- |
| 11-01 | 1（V1） | `null` | 1（V1） | `reviewsCompleted=not_collected`（`T0` 前不可追溯） |
| 11-19 | 0 | `null` | 1（V2） | 同上 |
| 11-20 | 2（V3、V4） | 1（V4，`partial` 自 10:00） | 2（V2、V3） | `newDrafts=precise`；`reviewsCompleted=partial`；`pendingAtEndOfDay=precise`（日终在 `T0` 后） |
| 11-21 | 1（V5） | 1（V2） | 2（V3、V5） | 三者 `precise` |
| 11-22 12:00（未结束） | 1（V6，暂计） | 1（V5，暂计） | `null` + `dayOpen=true` | `newDrafts`/`reviewsCompleted=partial` |
| 11-22 结束后回看 | 1（V6） | 1（V5） | 2（V3、V6） | 三者 `precise` |
| 11-23 | 0 | 0 | 2（V3、V6） | 真实零，`available` |

**计算核对：**

- V5：提交 `15:50Z` = 北京 11-21 23:50，归 11-21；发布 `16:10Z` = 北京 11-22 00:10，归 11-22。11-21 日终（`16:00Z`）时发布事件尚未发生，所以 V5 仍计入 11-21 日终待办；11-22 日终不再待办。
- V1：基线事件只证明 `T0` 时已发布，不计入 `reviewsCompleted`；V1 在 11-02 15:00 发布，11-02 日终待办为 0。
- 11-20 `reviewsCompleted=1` 只覆盖 `T0` 10:00 后；10:00 前若有完成，采集不能补足，不能写成“全天 1 次”。
- 11-22 12:00 的 `pendingAtEndOfDay` 必须是 `null`，不能返回 2；`newDrafts`/`reviewsCompleted` 可暂计但带 `dayOpen=true`。

### 5.2 日终半开区间边界

| 时刻（北京） | UTC | 归入 | 预期 |
| --- | --- | --- | --- |
| 11-20 23:59:59.999999 | `2026-11-20T15:59:59.999999Z` | 11-20 | 属于 11-20；日终待办计入 |
| 11-21 00:00:00.000000 | `2026-11-20T16:00:00.000000Z` | 11-21 | 属于 11-21；不属于 11-20 日终 |
| 11-21 23:59:59.999999 | `2026-11-21T15:59:59.999999Z` | 11-21 | 属于 11-21；日终待办计入 |
| 11-22 00:00:00.000000 | `2026-11-21T16:00:00.000000Z` | 11-22 | 属于 11-22 |

**计算：** 11-20 日终区间 = `[2026-11-19T16:00:00Z, 2026-11-20T16:00:00Z)`；11-21 日终区间 = `[2026-11-20T16:00:00Z, 2026-11-21T16:00:00Z)`。统一用 `< 次日 00:00:00`，不用 `<= 23:59:59.999`，否则会漏掉更高精度时间戳。

### 5.3 运行生命周期与跨午夜

| 运行 | 开始（北京） | 结束（北京） | UTC 开始 | UTC 结束 | 预期归日/状态 | 耗时 |
| --- | --- | --- | --- | --- | --- | --- |
| R-old | 11-18 10:00 | 11-18 10:00（同值） | `2026-11-18T02:00:00Z` | 同值 | 11-18 成功；耗时不可信 | 排除出样本 |
| R1 | 11-20 10:10 | 11-20 10:15 | `2026-11-20T02:10:00Z` | `02:15:00Z` | 11-20 失败 | 300,000ms |
| R2 | 11-20 10:30 | 11-20 10:35 | `2026-11-20T02:30:00Z` | `02:35:00Z` | 11-20 成功 | 300,000ms |
| R3 | 11-20 11:00 | — | `2026-11-20T03:00:00Z` | — | 11-20 `unfinished` | 无 |
| R4 | 11-21 10:00 | 11-21 10:01 | `2026-11-21T02:00:00Z` | `02:01:00Z` | 11-21 成功（缓存命中仍计运行） | 60,000ms |
| R5 | 11-21 23:55 | 11-22 00:05 | `2026-11-21T15:55:00Z` | `2026-11-21T16:05:00Z` | 11-21 成功（跨午夜仍归开始日） | 600,000ms |
| R6 | 11-22 08:00 | 11-22 08:02 | `2026-11-22T00:00:00Z` | `00:02:00Z` | 11-22 成功 | 120,000ms |
| R7 | 11-22 09:00 | — | `2026-11-22T01:00:00Z` | — | 参数校验失败，未建立 run，不计 | — |

**预期逐日（与 METRICS_SPEC §9.2 一致）：**

| 日期（北京） | `total` | `succeeded` | `failed` | `unfinished` | 耗时样本/平均 | `availability` |
| --- | --- | --- | --- | --- | --- | --- |
| 11-18 | 1（仅已记录） | 1 | `null` | `null` | 0 / `null` | `partial`（旧系统只保存成功） |
| 11-19 | `null` | `null` | `null` | `null` | 0 / `null` | `not_collected`（无记录 ≠ 0） |
| 11-20 | 3（R1、R2、R3） | 1（R2） | 1（R1） | 1（R3） | 2 / 300,000ms | `partial`（自 `T0` 10:00） |
| 11-21 | 2（R4、R5） | 2 | 0 | 0 | 2 / 330,000ms | `precise` |
| 11-22 12:00（未结束） | 1（R6） | 1 | 0 | 0 | 1 / 120,000ms | `partial` / `dayOpen=true` |
| 11-23 | 0 | 0 | 0 | 0 | 0 / `null` | `available`（真实零） |

**计算核对：**

- R5 耗时 = `16:05Z - 15:55Z` = 10 分钟 = **600,000ms**；归 11-21，不计入 11-22 的 `total`。
- 11-21 平均耗时 = (60,000 + 600,000) ÷ 2 = **330,000ms**。
- 11-20 平均耗时 = (300,000 + 300,000) ÷ 2 = **300,000ms**；R3 未结束，无耗时；`durationSampleCount=2` 只覆盖 10:00 后。
- 11-18 的 R-old `created_at=finished_at` 不是 0ms，而是“未采集耗时”，排除出样本；`avgDurationMs=null`。
- 11-22 12:00 的 `pendingAtEndOfDay` 为 `null`；11-22 结束后回看才为 2（V3、V6）。
- R7 在建立 run 之前被拒绝，没有 `runId`，不计入 `total`。

### 5.4 `null` / 0 / `partial` / `not_collected` 对账

| 情形 | 预期返回 | 页面 | 反例 |
| --- | --- | --- | --- |
| 11-19 无运行记录 | `total=null`，`availability=not_collected` | “未采集” | 不能显示 0，不能推断历史失败率为 0 |
| 11-23 采集开启且无事件 | `0`，`availability=available` | “0” | 不能显示 `null` 或“未采集” |
| 11-20 自 `T0` 起覆盖 | 有值，`availability=partial` | “部分覆盖，自 10:00” | 不能写成“全天” |
| 11-22 12:00 当天未结束 | 暂计数 + `dayOpen=true`；`pendingAtEndOfDay=null` | “截至 12:00，今日尚未结算” | 不能返回 2 或伪装成已结算 |
| 11-01 `reviewsCompleted` | `null` + `reason=LEGACY_EVENTS_NOT_AUDITABLE` | “历史不可追溯” | 不能返回 0 |
| 查询失败 | 非 2xx 错误 | 错误态 | 不能返回全零 |

**依据：** METRICS_SPEC §2—§4、§6、§9；PR #67 的 D5 对齐项。

**依赖：** M1 的 `policy_review_event`、`collection_baseline`、运行生命周期和两个 metrics 端点尚未实现/未验收；`queued` 当前不落行；`interrupted` 首版只承诺单进程；API-12 的 `409 RUN_NOT_COMPLETED` 需 M1 落地。本文不把这些写成已支持。

## 6. 仍需确认与签收边界

### 6.1 M1

1. 完整度：VIS-01 是否能返回 `(FactKey, period_start, period_end)` 的七态；能否区分“行存在且值为 null”与“无行”；`d4.static.v1` 与分支可达性的分母差异。
2. 完整度：`asOf` 精度、`period_end` 日期型语义、`not_due` 是否按方案 C 实现（定稿前不实现）。
3. 结果分布：`/assessment-runs/{id}` 的 `assessments` 是否恰好等于本次分析、不含候选；`stale` 是否读取时重算。四态计数读同一 run 即稳定；`stale` 依赖读取时的失效判定（企业事实变更、时间边界：申报窗口关闭/政策到期，或政策版本/状态变化），**仅固定 `createdAt`/`factVersion` 不等于 `stale` 稳定**。要断言两次读取的 `stale` 计数一致，完整前提是：①读取同一 run 并固定相关输入；②控制两次读取的测试时钟，或确保两次读取之间没有跨越会影响该 run 的时间边界；③两次读取之间没有影响该 run 的失效事件或政策状态变化。`createdAt` 是 run 创建时间，不是读取时钟。**注意**：`AssessmentRun` 正式契约无 `generatedAt`（`additionalProperties: false`），任何“读取时点”额外时间戳字段都是新增契约字段，须在 `1.2.0-rc.1` 逐项确认，当前不指向接口返回 `generatedAt`。
4. 逻辑树：`get_run` 是否按 `assessment.policy_version_id` 取版本；是否提供节点级结果（若不提供，页面不得编造）。
5. 趋势：`policy_review_event`、`collection_baseline`、`run_lifecycle_from`/`review_events_from`、运行生命周期事务、`interrupted` 扫描、API-12 409；`queued` 不落行的契约描述。
6. 趋势：提供只读 SQL 结果（事件行、运行行、逐日计数）供 M3 按 §5 核对；未提供前不能把 VIS-AT-06/07 写成通过。

### 6.2 M2

1. 完整度：`filled_unverified`、`answered_unknown`、`not_answered`、`not_applicable`、`not_due` 五种显示不能合并；存在 `applicability_unknown` 时整图不显示总百分比；分母为 0 显示“不适用/无可统计字段”。
2. 结果分布：四态 + `stale` 单独标记；分布数不随列表筛选变化；空态不显示 0%；合成 `conflict` 必须标“合成”。
3. 逻辑树：版本切换后树、`criterionId`、原文定位随版本变化；`manual_review` 叶子不显示成“不满足”或空白；不编造节点状态。
4. 趋势：`null`/0/`partial`/`not_collected`/`dayOpen` 文案与图形区分；`unfinished` 不并入 `failed`；耗时显示样本数，无样本不画 0ms；基线日期单独标记。
5. 时间线（延期项）：多批次窗口、附件来源、`verified`/`derived`/`unverified`/`unknown`/`not_applicable` 状态不得混用。

### 6.3 负责人

1. D4 的 `not_due` 方案 C、季度期初/期末不分层、`d4.static.v1` 是否作为首版过渡；确认前 M1 不实现、样例不标通过。
2. D5 的“发布=人工审核完成”、`review_revision`、两个覆盖起点、运行上限、跨午夜回填、当天暂计数是否接受。
3. VIS-06/PR #69 的版本粒度（年度指南/通知是否独立版本化）、`published_at` 是否纳入、18:00 截止语义。
4. 三方签收：以上均未签收；本文只提供对账输入，不代替 M1/M2/负责人记录。

## 7. 本轮验证与修改文件

**已做：**

- 手算完整度样例 C：5/6 = 83.3%；静态引用候选 5/8 = 62.5%。
- 手算样例 E：12/13 = 92.3%；分层候选 12/15 = 80.0%。
- 手算结果分布：2+2+1+1=6；`stale=1`；四态+stale=7≠6。
- 核对独立反例样本 G：同一 run、固定 `createdAt`/`factVersion`、无失效事件/版本变化，仅测试时钟从北京 11-14 10:00 推进到 11-15 10:00，跨过窗口关闭（11-14 23:59:59）与政策到期（`effectiveTo=2026-11-14`）边界，`validity` 由 `current` 变 `stale`，`staleReasons` 为 `window_closed`、`policy_expired`。
- 手算趋势：11-20 平均 300,000ms；11-21 平均 330,000ms；R5 600,000ms；日终半开区间和 UTC 换算逐条核对。
- 核对 GXJS v1/v2 的 `source_version`、`source_page_or_section`、`source_span`、`criterionId` 与 `manual_review` 原因，均来自 `criteria_review.csv`。
- `not_due`、静态引用、合成 `conflict` 均明确标“候选/合成”，未写成已签收或验收通过。

**未做：**

- 未执行数据库查询/迁移/接口/浏览器/真实模型；未生成截图或 run ID。
- 未修改 `data/**`、契约、迁移、前端代码、评测输入；未把任何样例写成三方签收。
- 未验证 `conflict` 真实样本；`conflict` 仍为 0，只有单元测试覆盖。

**修改文件：**

- 新建：`docs/product/M3_E3_RECONCILIATION_1113.md`（本文件）。
- 更新：`docs/devlog/M3.md`（记录本次实际工作）。
- 未修改：`data/**`、`contracts/**`、`infra/**`、`apps/web/**`、`services/api/**`、`source_register.csv`、`criteria_review.csv`、`labels.csv`、冻结输入。
