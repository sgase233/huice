# 已审材料要求（M3）

> 版本 v1.0｜编写 2026-10-28。本文件与 [`data/policies/material_requirements.csv`](../../data/policies/material_requirements.csv) 配套，是 M1 填 `preparation.items[]` 与事实 `verified=true` 的**唯一依据**；材料清单、必交/条件交、材料→事实映射都不得由模型编造，须按本文件口径填写，未仲裁前一律标「待双人复核」。

## 0. 为什么需要这份文件

M1 的在线链路里，`preparation.items[]` 目前恒为 `[]`，企业事实一律 `verified:false`（见 `services/api/app/agents/orchestrator.py` 的 `MATERIAL_LIST_WARNING` 与 `services/api/app/integrations/storage.py` 的 `_preparation_template`）。要解除阻塞，需要回答三件事：

1. **税/补助两类材料清单**分别有哪些材料；
2. **必交与条件交**怎么区分；
3. **哪份材料证明哪条事实**（材料 → 事实键映射）。

第 3 点是 `verified=true` 的前提：只有当某条事实「已被某份已复核材料证明」时，才允许把该事实标 `verified=true`；否则一律 `false`。

## 1. CSV 字段释义

表头固定（M1 导入时映射 camelCase，字段名不得任意改）：

```
policy_type,material_id,title,required,condition_desc,description,proves_fact_keys,applies_to_policy_ids,source_basis,review_status
```

| 列 | 含义 | 填写要求与允许取值 |
| --- | --- | --- |
| `policy_type` | 材料所属类型 | `tax`（税收，`tax_self_assessment`）或 `grant`（资金，`grant_application`） |
| `material_id` | 材料唯一标识 | `TAX-01`、`GRANT-01` 等，同一类型内不重复 |
| `title` | 材料名称 | 中文，能对应到具体材料或单据 |
| `required` | 必交/条件交 | `必交`（该类型下判断都需要的登记类材料）或 `条件交`（仅当政策含某类门槛时才需要） |
| `condition_desc` | 条件交的触发条件 | 条件交必填，说明「何时才需要这份材料」；必交留空 |
| `description` | 办理说明 | 说明该材料证明什么、怎么用；税收类突出「留存备查」，资金类突出「申报提交」 |
| `proves_fact_keys` | 材料证明的事实键 | 用 `;` 分隔的事实键，取值限于 §3 事实字典；不直接对应事实键时留空 |
| `applies_to_policy_ids` | 适用的政策 | 用 `;` 分隔的 `policy_id`，与 `source_register.csv` 一致；留空表示该类型全部政策适用 |
| `source_basis` | 官方依据 | 文号/条款，能回查；无法确认写「待核实」 |
| `review_status` | 复核状态 | `待双人复核`（默认）→ `已仲裁` → `可发布`；依据确实无法入库时用 `待核实（依据未入库）`，该状态不得交 M1 当作可发布 |

> **登记类材料例外（2026-11-04 定案）：** 营业执照等登记类材料本身不由政策正文规定，`source_basis` 可写「企业登记信息（登记类材料免引政策条款）」，不因缺文号条款判为待核实。

## 2. 必交与条件交的区分

- **必交**：该类型下任何判断都要用到的登记类材料（目前只有营业执照一项，证明 `region/industry/establishedDate/independentLegalEntity`）。
- **条件交**：只有政策条件里出现了对应事实键的门槛，才需要该材料。例如：
  - 政策含 `taxableIncome` 阈值 → 需要「企业所得税年度纳税申报表」；
  - 政策含 `assetsStart/End` 季度平均 → 需要「各季度资产负债表」；
  - 政策含 `employeeCountStart/End` 季度平均 → 需要「各季度从业人数统计」。

这条区分直接决定 `preparation.items[]` 里展示哪些条目、以及哪些事实可以进入补问清单之外的「待补材料」提示。

## 3. 材料 → 事实键映射（`proves_fact_keys`）

事实键只允许取 [`ANNOTATION_GUIDE.md`](./ANNOTATION_GUIDE.md) §4 事实字典与 `services/api/app/domain/rules.py` `FACT_UNITS` 的 18 键之一：`region / industry / establishedDate / taxableIncome / rdExpense / rdActivity / employeeCountStart / employeeCountEnd / assetsStart / assetsEnd / revenue / equityFinancing / rdExpenseIncrease / rdExpenseRevenueRatio / rdAddDeductionClaimed / creditRecord / specificMarketSince / independentLegalEntity`。映射口径：

| 材料 | 证明的事实键 |
| --- | --- |
| 营业执照 | `region`、`industry`、`establishedDate`、`independentLegalEntity` |
| 企业所得税年度纳税申报表 | `taxableIncome` |
| 研发费用加计扣除优惠明细表/归集辅助账 | `rdExpense`、`rdActivity` |
| 研发项目立项文件与实施记录 | `rdActivity`（依据留存备查资料清单，国家税务总局公告2018年第23号；非 119号 正文） |
| 各季度资产负债表 | `assetsStart`、`assetsEnd` |
| 各季度从业人数统计 | `employeeCountStart`、`employeeCountEnd` |
| 上年度经备案赋码财务审计报告（SRDI） | `revenue`、`rdExpense`、`rdExpenseRevenueRatio`（按报告是否覆盖；占比为预计算。审计报告不直接产出 `taxableIncome` 或季度期初 `assetsStart`） |
| 研发费用专项归集表/专项审计报告 | `rdExpense`（目前只有 HIGHTECH 第七条（二）明文要求；HIGHTECH 已到期，仅历史样本） |
| 近两个年度企业所得税年度纳税申报表 | `rdAddDeductionClaimed`、`rdExpense`、`rdExpenseIncrease` |
| 近2年新增股权融资凭证（验资报告/银行凭证） | `equityFinancing` |
| 企业信用报告（SRDI） | **不映射 `creditRecord`**（SRDI-006 已仲裁：经营异常/严重失信 ≠ 财政性资金惩戒）；仅作申报佐证 |
| 细分市场经营证明材料（专项合同/订单、产品收入分类明细、主营业务说明） | **不映射 `specificMarketSince`**（仅辅助说明，不驱动 `verified`；该事实走补问） |

不直接对应事实键的材料（如申请书、诚信承诺书）`proves_fact_keys` 留空——它们是申报要件或辅助材料，不驱动 `verified=true`。

> `specificMarketSince`（进入特定细分市场的起始日期）由企业主营业务材料佐证，属企业自述+材料辅助，通常仍需人工判断「是否属同一细分市场」；材料只证明时间线起点，不证明目录归类。**材料表不把它挂到 `specificMarketSince` 上**，避免误触发 `verified=true`。

## 4. 与 `preparation.items[]`、`verified=true` 的对接

- `preparation.items[]` 的每一项来自 CSV 一行：`title` → `title`，`description` → `description`，`proves_fact_keys` → `required_fact_refs`：**期间按条目类型分开处理**——引用了条件事实的条目，期间取自该条件在 AST 中写明的期间：显式固定期间保留其具体起止日期，`assessment` 相对期间随本次判断展开，季度引用按其自身规则展开；只有必交材料所证明的键未被任何条件引用时，才按本次评估期间 `(key, *period)` 补齐。契约 `FactRef.period` 必填 `{startDate,endDate}`，不接受字符串 `"assessment"`。
- `required`/`condition_desc` 的承载：M1 2026-11-05 定案采用**方案 ②，不动契约**——`title` 前缀仍为 `【必交】`/`【条件交】`（`domain/preparation.py` 的 `REQUIRED_PREFIX`/`CONDITIONAL_PREFIX`）；`condition_desc` 由 `_display_description()` 追加在 `description` 末尾，格式为 `（何时需要：<condition_desc>）`；`condition_desc` 为空时不加后缀。CSV 两列原样保留作为数据源与复核依据。若前端要按必交/条件交过滤或着色，后续以可选字段进 1.0.x。
- **期间**：`FactRef.period` 必填具体起止日期。API-03 的 `preparationTemplate.items` 当前恒为 `[]`（`storage.py::_preparation_template` 写死 `items=[]`）；期间与条件说明只在运行期 `preparation.items[]` 出现，期间展开规则见本节第一条。若将来要让模板本身表达「期间随评估」，需契约变更，届时另记，不与当前行为混写。
- **无事实键的材料**（GRANT-02/05/06；GRANT-09 不入库）：`required_fact_refs: []`，`missing` 按 `true` 处理（无法自动核对时不能显示为「资料已提供」）。这是保守处理，UI 的「资料缺失」措辞会偏；item `description` 必须写明「需人工核对，无法自动判断是否已提供」。
- **材料状态四态**：四态以 `PreparationItem.status` 为准：`facts_filled` / `facts_missing` / `manual_check` / `policy_closed`。`facts_filled` 只说明事实有值，不代表材料已提交或已核验。`missing` 保留为兼容字段，语义是「该提醒去准备」——`status ∈ {facts_missing, manual_check}` 时为 `true`，`policy_closed` 不算（政策已关闭，提醒补材料没有意义），因此不等于 `status != facts_filled`。历史响应的兼容边界：**只有 `materialId` 与 `status` 同时缺失**的旧条目，页面才显示「材料状态未确认，请人工核对」，不据 `missing` 推断已交或缺失；只缺其中一个字段，或 `status` 取到未知值时，不属于旧格式，报告数据会被请求校验拒绝，不会进入上述提示。`missing` 只保留「是否提醒去准备」这类既有语义，不作为状态展示的依据。
- **季度类键**：TAX-05 的 `assetsStart/End`、TAX-06 的 `employeeCountStart/End` 按 `quarterly_average` 展开为 4 个季度 × 期初/期末 = 8 个 `FactRef`，期间随运行期评估年度。
- **政策关闭**：政策关闭由 `PreparationItem.status=policy_closed` 表达；`PolicyDetail.status` 仍是政策级状态，两者并存，页面展示以 item 的 `status` 为准。
- `verified=true` 的触发条件：某条事实键出现在某份「已复核材料」的 `proves_fact_keys` 里，且该材料已按官方来源完成两人独立核对与仲裁，才可把对应事实标 `verified=true`。合成演示材料同样遵守此规则，不得为演示效果把未复核材料标成已核验。

## 5. 待核实与未决

1. 资金政策条件标注已覆盖 SRDI、RD-SUBSIDY、HIGHTECH 三项（2026-11-01），材料映射已按 18 键补齐；`applies_to_policy_ids` 与 `condition_desc` 已完成第二轮复核与仲裁（见第 10 条）。
2. 「必交/条件交」的契约承载已由 M1 回告：采用方案 ②，不动契约（见 §4）。
3. 复核状态以 §5 第 10 条为准；未完成仲裁的行不得交 M1 当作可发布真值。复核表见 [`data/evaluation/material_review_sheet.csv`](../../data/evaluation/material_review_sheet.csv)（A/B 与仲裁列已回填）。
4. **`verified` 当前全为 `false`（2026-11-02 定案，方案 B）。** 没有任何已双人复核的合成材料，因此不触发 `verified=true`；补合成材料并逐份复核的工作另行排期。`verified=false` 是如实状态，不是遗漏（见 ACCEPTANCE 覆盖状态）。
5. **材料复核第一轮已完成（2026-11-04）。** A：4 通过 / 11 不通过；B：11 项问题（GRANT-02/03/04/05 为 P0，TAX-01/TAX-04/GRANT-01/GRANT-08 为 P1，GRANT-06/07/09 为 P2；TAX-02/03/05/06 未给二元结论）。已按两份意见改为定稿文本：补齐「留存备查/申报提交」；GRANT-03 改为上年度经备案赋码审计报告；GRANT-04 只留 HIGHTECH 并标历史；GRANT-05 改为 2024-12 单一时点；GRANT-06 删除诚信承诺书、只留信用报告；GRANT-07 改为 RD-SUBSIDY 近两个年度企业所得税年度纳税申报表；GRANT-08 补三项要件；GRANT-09 降为辅助佐证。第一轮记录见 [`material_review_round1.csv`](../../data/evaluation/material_review_round1.csv)；定稿需 A/B **盲签第二轮**（B 用 [`material_review_sheet_b.csv`](../../data/evaluation/material_review_sheet_b.csv)，不含 A 结论）。签认 + 仲裁前 `review_status` 仍为「待双人复核」，不得写「可发布」。
6. **第二轮签认规则（2026-11-04 定案）。** A/B 必须签**定稿版**；B 只能拿不含 A 结论的盲签表，不预设结论；分歧进仲裁、保留双方意见。找不到第二人时，`review_status` 只能写「已仲裁（单人复核，偏离双人流程）」并记录原因，**不得写「可发布」**，对应事实 `verified` 仍为 `false`。
7. **两处覆盖代价（必须如实记录）。** ① RD-SUBSIDY 的 `creditRecord` 没有已复核材料（GRANT-06 只覆盖 SRDI）；该事实不置 `verified=true`，只能走补问，不得把信用报告硬挂到 RD-SUBSIDY。② GRANT-04 保留 HIGHTECH 的前提是 M1 能在材料/政策层带出 `status=closed`；做不到就把它移出当前映射、移入历史样本集。
8. **第二轮签认（2026-11-04）A/B 结果。** A 已更正为 **10 通过 / 5 不通过**；5 行为 **TAX-01、TAX-04、GRANT-01、GRANT-02、GRANT-09**（其中 4 行只差依据，GRANT-02 需动内容）。B **11 通过 / 4 修正后通过**（GRANT-03/06/08/09）。两表差异集中在：4 行依据待核实、GRANT-02 的 `applies` 与依据、GRANT-03 标题年份、GRANT-06 官方名称、GRANT-08 条件要件、GRANT-09 定位表述。第二轮记录见 [`material_review_round2.csv`](../../data/evaluation/material_review_round2.csv)；仲裁结果见第 10 条。
9. **本轮已同步的文档修正。** §2 必交事实键补 `independentLegalEntity`；§3 删除「信用报告/诚信承诺书 → `creditRecord`」，改为「企业信用报告不映射 `creditRecord`（SRDI-006）」；立项文件注明依据为 2018年第23号留存备查清单；细分市场材料注明不映射 `specificMarketSince`；财务审计报告与纳税申报表的键映射改为与定稿 CSV 一致。GRANT-02 的 `applies_to_policy_ids` 已收窄为 SRDI / RD-SUBSIDY / HIGHTECH，并按三个政策分别写依据。
10. **第二轮仲裁结果（2026-11-04）。** **13 行「已仲裁」、2 行「待核实（依据未入库）」**（TAX-04 等 2018年第23号原文；GRANT-09 等认定标准附件1）。登记类材料 TAX-01 / GRANT-01 按 §1 例外**通过**——这与 A 最新「4 行均待核实」的归口建议有出入，保留例外待 A 确认；若撤销例外，两行改回「待核实」。GRANT-02 收窄 `applies` 并逐政策写依据；GRANT-03 采用「上年度（2025 申报批次对应 2024 年度）」折中标题；**GRANT-06 保留现名**：2025 专精特新通知及认定标准附件1 均写「企业信用报告（在深圳信用网下载）」，B 所引「企业公共信用信息报告（国家标准版）」出自 2022/2024 创新型中小企业通知，不适用于本行；待 B 提供 2025 专精特新官方出处后再议；GRANT-08 条件补 2023–2024 与单笔≤30%；GRANT-09 定位按 B 修正。**未写「可发布」**。回填后的 A/B/仲裁列见 [`material_review_sheet.csv`](../../data/evaluation/material_review_sheet.csv)。
11. **M1 复核与回告（2026-11-05）。** M1 逐行核对：15 行 × 10 列、`proves_fact_keys` 全命中 18 键、`applies_to_policy_ids` 均存在、HIGHTECH 已 `closed`；**13 行可导入，TAX-04 / GRANT-09 不导入**（不生成材料清单、不置 `verified=true`）。`required`/`condition_desc` 采用方案 ②；无事实键材料的 `missing=true` 保守处理；期间、季度展开、政策关闭按 §4。TAX-01 / GRANT-01 的 `applies_to_policy_ids` 留空按「同 `policy_type` 下全部政策」处理，M3 已认可。
12. **M1 最终确认（2026-11-05，head 原 `9ed973d` / 清理签名后 `a59f4d7`）。** M1 核对通过，对本 PR 无反对意见；并更正：本轮无事实键、触发 `missing=true` 的是 **GRANT-02 / 05 / 06 三行**，GRANT-09 为「待核实（依据未入库）」、不导入。导入实现断言按集合关系写，不硬编码 13：
    - 导入条目集合 == CSV 中 `review_status=已仲裁` 的行集合；
    - TAX-04 / GRANT-09 负向断言：不出现在任何 `policy_type` 的模板中；
    - `proves_fact_keys ⊆ 18 键`；非空 `applies_to_policy_ids ⊆ source_register`，空则按 `policy_type` 展开（TAX-01 / GRANT-01）；GRANT-02 的非空列表原样使用；
    - `title` 前缀只有 `【必交】` / `【条件交】`，与 CSV `required` 一一对应；
    - `requiredFactRefs == []` 的条目运行期 `missing=true`（本轮 GRANT-02 / 05 / 06）；
    - 导入判定只读 `review_status`，不硬编码行号/ID；撤销登记类例外时两行自动不导入，有测试用例锁住。
    - 前端「需人工核对」而非「资料缺失」的文案由 M2 在实现 PR 中落地；`apps/web/**` 不由 M1 修改。
13. **云端库状态（2026-11-07）。** `material_requirement` / `material_requirement_policy` 两张表仍为空；运行期 `preparation.items` 为空，页面带「待 M3 审核材料导入后补齐」告警。导入命令 `python -m app.materials import` 只收 `review_status=已仲裁` 的行，需等本 PR 的 CSV 定稿后由 M1 在云端执行。四态在此之前只有代码与契约，没有真实清单可看。
