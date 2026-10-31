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
| `review_status` | 复核状态 | 一律 `待双人复核`，经两人独立核对 + 仲裁后才能交 M1 |

## 2. 必交与条件交的区分

- **必交**：该类型下任何判断都要用到的登记类材料（目前只有营业执照一项，证明 `region/industry/establishedDate`）。
- **条件交**：只有政策条件里出现了对应事实键的门槛，才需要该材料。例如：
  - 政策含 `taxableIncome` 阈值 → 需要「企业所得税年度纳税申报表」；
  - 政策含 `assetsStart/End` 季度平均 → 需要「各季度资产负债表」；
  - 政策含 `employeeCountStart/End` 季度平均 → 需要「各季度从业人数统计」。

这条区分直接决定 `preparation.items[]` 里展示哪些条目、以及哪些事实可以进入补问清单之外的「待补材料」提示。

## 3. 材料 → 事实键映射（`proves_fact_keys`）

事实键只允许取 [`ANNOTATION_GUIDE.md`](./ANNOTATION_GUIDE.md) §4 事实字典与 `services/api/app/domain/rules.py` `FACT_UNITS` 的十键之一：`region / industry / establishedDate / taxableIncome / rdExpense / rdActivity / employeeCountStart / employeeCountEnd / assetsStart / assetsEnd`。映射口径：

| 材料 | 证明的事实键 |
| --- | --- |
| 营业执照 | `region`、`industry`、`establishedDate` |
| 企业所得税年度纳税申报表 | `taxableIncome` |
| 研发费用加计扣除优惠明细表/归集辅助账 | `rdExpense`、`rdActivity` |
| 研发项目立项文件与实施记录 | `rdActivity` |
| 各季度资产负债表 | `assetsStart`、`assetsEnd` |
| 各季度从业人数统计 | `employeeCountStart`、`employeeCountEnd` |
| 财务审计报告/财务报表 | `taxableIncome`、`rdExpense`、`assetsStart`、`assetsEnd`（按报告是否覆盖） |
| 研发费用专项归集表/专项审计报告 | `rdExpense` |

不直接对应事实键的材料（如申请书、信用报告）`proves_fact_keys` 留空，但仍出现在材料清单里——它们是申报要件，只是不驱动 `verified=true`。

## 4. 与 `preparation.items[]`、`verified=true` 的对接

- `preparation.items[]` 的每一项来自 CSV 一行：`title` → `title`，`description` → `description`，`proves_fact_keys` → `required_fact_refs`（每个键配 `period=assessment`，即判断所属年度；季度类事实由 M1 按规则口径展开为具体季度期间）。
- `required`/`condition_desc` 目前**没有对应字段**（契约 `PreparationTemplateItem`/`PreparationItem` 只有 `title/description/required_fact_refs[/missing]`）。两种处理方式由 M1 定：① 契约加 `required: boolean` 字段；② 在 `title`/`description` 里以「【必交】/【条件交】」前缀表达。本文件先以 CSV 列承载，M1 落库前须回告采用哪种。
- `verified=true` 的触发条件：某条事实键出现在某份「已复核材料」的 `proves_fact_keys` 里，且该材料已按官方来源完成两人独立核对与仲裁，才可把对应事实标 `verified=true`。合成演示材料同样遵守此规则，不得为演示效果把未复核材料标成已核验。

## 5. 待核实与未决

1. 资金政策的条件标注尚未全量完成（`criteria_review.csv` 目前只有 SME/RD 两税收政策），资金类材料的 `applies_to_policy_ids` 与 `condition_desc` 随资金条件标注补齐后回填。
2. 「必交/条件交」是否需要契约字段（见 §4），待 M1 回告。
3. 全部 12 行材料要求均为「待双人复核」，未经两人独立核对 + 仲裁不得交 M1 当作可发布真值。
