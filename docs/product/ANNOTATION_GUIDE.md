# 条款标注规范（M3）

> 版本 v1.0｜编写 2026-10-27。本文件是 `data/policies/criteria_review.csv`、`data/fixtures/enterprises.csv`、`data/evaluation/labels.csv` 的填写口径，也是 M1 导入规则、M2 展示原文依据的共同参照。所有规则以官方原文为准，模型输出只能作为待审核草稿，不能充当真值。

## 0. 铁律（先读这五条）

1. **先核来源，再填条件。** 每条条件必须能通过「官方 URL → 版本/文号 → 页码或段落 → 原文片段」回查；政策解读、自媒体、模型回答不能代替原文。
2. **一条原子条件一行。** 一个数值阈值、一个行业排除、一个期间判断各占一行；不要把多个条件拼进一格。
3. **两人独立标注，再仲裁。** `annotator_a`、`annotator_b` 各自独立填写、不先互看；分歧由第三人或指导教师按原文裁定，保留原意见。单人复核、模型判断、两人商量后的单一答案都不算双人标注。仲裁未完成的行标「待双人复核」，不能交 M1 当作可发布真值。
4. **未知是 `null`，不是 `0`。** 缺失事实留空或写 `null`，绝不能写 `0` 顶替；`"0"` 是已知的零值，两者语义不同。
5. **历史窗口标「已关闭」。** 申报窗口/有效期已过的政策是历史样本，不显示「立即申报」。

## 1. `criteria_review.csv` 字段释义

表头固定（不得任意改，M1 导入时映射 camelCase）：

```
policy_id,criterion_id,source_version,source_page_or_section,source_span,field,operator,value,unit,period,logic_group,negation,annotator_a,annotator_b,adjudicated_expression,review_status
```

| 列 | 含义 | 填写要求与允许取值 |
| --- | --- | --- |
| `policy_id` | 所属政策 | 与 `source_register.csv` 的 `policy_id` 一致 |
| `criterion_id` | 条件唯一标识 | 建议 `政策短名-序号`，如 `SME-001`、`RD-001`；同一政策内不重复 |
| `source_version` | 来源版本/文号 | 如「财政部 税务总局公告2023年第12号」；引用文未登记时写「文号（待登记来源）」 |
| `source_page_or_section` | 页码或条款 | 如「第五条」「第一条」「附件申报指南第3页」；页码与段落至少一个非空 |
| `source_span` | 原文片段 | 逐字摘录，能回查数字、比较方向、否定词和期间 |
| `field` | 事实 key / 指标 | 见 §4 事实字典：`industry/taxableIncome/employeeCount/assets/rdActivity/rdExpense/establishedDate` |
| `operator` | 操作符 | 比较符 `lt/lte/eq/ne/gte/gt`，或 `industry_exclusion`、`quarterly_average` |
| `value` | 阈值/列表 | 金额十进制字符串；行业排除为行业码列表，用 `\|` 分隔 |
| `unit` | 单位 | `code/CNY/person/boolean/date` |
| `period` | 所属期间 | `assessment`（本次判断所属年度）或显式 `YYYY-MM-DD/YYYY-MM-DD` |
| `logic_group` | AND/OR 分组 | 同一 `policy_id` 内，同组号的条件 AND 合并；OR 分支用不同组号并在备注说明 |
| `negation` | 是否否定 | `false` 为主；「不属于限制禁止行业」用 `industry_exclusion` 表达，不额外写否定 |
| `annotator_a` | 标注人 A | 独立填写，留空=尚未标注 |
| `annotator_b` | 标注人 B | 独立填写，留空=尚未标注 |
| `adjudicated_expression` | 仲裁后 AST | JSON 字符串，须 CSV 转义（内部双引号写成 `""`），操作符见 §3 |
| `review_status` | 复核状态 | `待双人复核`（默认）→ `已仲裁` → `可发布`；未仲裁不得写「可发布」 |

**注意：** `field/operator/value/unit/period` 是给人复核的扁平投影，M1 导入以 `adjudicated_expression` 的 JSON AST 为唯一程序口径；两者不一致时以 AST 为准并回改投影列。

## 2. 原文定位与核对要点

- `source_span` 逐字摘录，不做改写；若原文是「不超过300万元」，span 必须含「不超过」，不能写成「超过」或「≤300万」。
- 数字、比较方向、否定词、期间四类必须逐字核对：
  - **数字**：300 万元 = `3000000.00`；5000 万元 = `50000000.00`。
  - **方向**：`不超过/以下` → `lte`；`达到/以上` → `gte`；`超过/高于` → `gt`；`低于/不足` → `lt`。
  - **否定**：「国家非限制和禁止行业」是正面表述，用 `industry_exclusion` 表达负面清单，不再叠 `negation`。
  - **期间**：自然年度、季度、申报窗口、有效期四者不同，不得混用（见 §7 名词表）。
- 正文与附件条件矛盾时，两处都登记、`review_status` 标「待双人复核」并注明冲突，由人工仲裁，不能任选有利的一处。
- 无法从原文确认的口径写「待核实」，不能臆造。

## 3. AST 操作符（与实现说明 §2.2 一致）

只允许以下形状，`adjudicated_expression` 必须是合法 JSON：

| `op` | 参数 | 示例 |
| --- | --- | --- |
| `compare` | `factKey,period,comparator,value,unit` | `{"op":"compare","factKey":"taxableIncome","period":"assessment","comparator":"lte","value":"3000000.00","unit":"CNY"}` |
| `date` | `factKey=establishedDate,period,comparator,value(YYYY-MM-DD)` | `{"op":"date","factKey":"establishedDate","period":"assessment","comparator":"gte","value":"2020-01-01"}` |
| `quarterly_average` | `metric=employeeCount/assets,period,comparator,value,unit` | `{"op":"quarterly_average","metric":"employeeCount","period":"assessment","comparator":"lte","value":"300","unit":"person"}` |
| `industry_exclusion` | `factKey=industry,period,excluded[]` | `{"op":"industry_exclusion","factKey":"industry","period":"assessment","excluded":["H62"]}` |
| `and` / `or` | `children:[...]`（至少两个） | 政策级默认 AND，一般由 `logic_group` 表达，行内 AST 保持原子 |
| `not` | `child` | 仅在原文明确「不属于…但…」时用 |

- 数值一律十进制字符串，禁止用 JS float 预计算结果；金额最多 2 位小数。
- 布尔条件 `rdActivity` 的 `value` 必须是 JSON 布尔值 `true`/`false`（不带引号），不能写成字符串 `"true"`。
- `period` 用 `assessment` 表示判断所属年度，季度平均必须指向完整自然年度。
- `quarterly_average` 的 `field` 列填指标名 `employeeCount`/`assets`（对应 §4 的 `employeeCountStart/End`、`assetsStart/End` 八个季度起止输入）。
- 区间用 AND 的上下界表达，不另加操作符；比例以 `unit=ratio` 的十进制字符串表达。
- 冲突节点先单独检测并向上传播为 `conflict`，不得被 OR 短路掩盖。

## 4. 事实字典（`field` / `unit` / `period` 取值）

| key | value 与 unit | period |
| --- | --- | --- |
| `region` | 行政区划码字符串，如 `440300`；`code` | 画像年度 |
| `industry` | 已冻结行业码；`code` | 画像年度 |
| `establishedDate` | `YYYY-MM-DD`；`date` | 画像年度 |
| `taxableIncome` | 十进制字符串，金额最多 2 位小数；`CNY` | 自然年度 |
| `rdExpense` | 十进制字符串，非负；`CNY` | 自然年度 |
| `rdActivity` | JSON boolean；`boolean` | 自然年度 |
| `employeeCountStart` / `employeeCountEnd` | 非负十进制字符串；`person` | 自然季度起止日期 |
| `assetsStart` / `assetsEnd` | 非负十进制字符串，金额最多 2 位小数；`CNY` | 自然季度起止日期 |

季度平均值口径：各季度 `(期初+期末)/2`，再对完整四季度求平均；`quarterly_average` 需保留八个输入与中间值。从业人数含与单位建立劳动关系的职工及接受的劳务派遣用工。

## 4.1 地方通知待扩展事实键（预计算事实口径，待 M1 落库）

第二阶段地方通知（`SZ-GRANT-RD-SUBSIDY-2026`、`SZ-GRANT-SRDI-2025`）需要的事实键超出当前 10 键字典。按「**预计算事实**」口径处理：增量与占比由 M3 在 `enterprises.csv`/`labels.csv` 里给出已算好的数值，M1 只做 `compare`，**不新增 delta/ratio 算子**。以下键待 M1 扩展 `FACT_UNITS` 与 OpenAPI `FactKey` 枚举后启用：

| key | 含义与计算口径 | value 与 unit | period | 需求来源 |
| --- | --- | --- | --- | --- |
| `revenue` | 上年度营业收入总额 | 十进制字符串，金额最多 2 位小数；`CNY` | 自然年度 | SRDI 二（二）（三） |
| `equityFinancing` | 近2年新增股权融资总额（合格机构投资者实缴额） | 十进制字符串；`CNY` | 近2年（显式期间） | SRDI 二（三） |
| `rdExpenseIncrease` | 上年度研发费用增量 = 上年度 `rdExpense` − 前年度 `rdExpense`（M3 预计算） | 十进制字符串（可为负）；`CNY` | 上年度 | RD-SUBSIDY 第八条（二）1 |
| `rdExpenseRevenueRatio` | 上年度研发费用占营业收入比重 = `rdExpense` ÷ `revenue`（M3 预计算，如 3% 记 `0.03`） | 十进制字符串；`ratio` | 上年度 | SRDI 二（二） |
| `rdAddDeductionClaimed` | 近两年均已申报享受研发费用加计扣除 | JSON boolean；`boolean` | 近两年 | RD-SUBSIDY 第八条（二）1 |
| `creditRecord` | 诚信记录良好（无限制申请财政性资金惩戒、无超期未验收未退款） | JSON boolean；`boolean` | 申报时点 | RD-SUBSIDY 第八条（三） |

> 键名与 unit 为 M3 建议，须与 M1 定稿后再写进 `criteria_review.csv`。「从事特定细分市场≥2年」暂以 `establishedDate` + `date` 算子近似，是否另设 `specificMarketYears` 待定。

## 5. 补问的获取成本与敏感程度（整数档位）

`questioning.choose_next_question` 按「受影响待定政策数降序 → 获取成本升序 → 敏感程度升序 → key/日期字典序」排序；两个档位由 M3 定义、M1 承载，不由模型猜测。整数档位取 1–5，含义须可复现。

**获取成本 `acquisition_cost`（整数 1–5）：**

| 档位 | 含义 | 例子 |
| --- | --- | --- |
| 1 | 企业内部已有、随手可得 | `region`、`industry`、`establishedDate`、`rdActivity` |
| 2 | 财务报表/申报表直接可查 | `taxableIncome`、`rdExpense` |
| 3 | 需财务整理计算 | `employeeCountStart/End`、`assetsStart/End` |
| 4 | 需第三方出具或审计 | 研发费用专项审计报告、审计报告（本期暂无对应 key，预留） |
| 5 | 需外部权威部门或特殊证明 | 行业资质、限制行业证明（本期暂无对应 key，预留） |

**敏感程度 `sensitivity`（整数 1–5）：**

| 档位 | 含义 | 例子 |
| --- | --- | --- |
| 1 | 公开基本信息 | `region`、`industry`、`establishedDate` |
| 2 | 常规经营数据 | `employeeCountStart/End`、`assetsStart/End` |
| 3 | 财务数据 | `taxableIncome`、`rdExpense` |
| 4 | 经营细节/商业敏感 | `rdActivity`（研发活动内容/项目明细） |
| 5 | 高度敏感/个人隐私 | 个人身份、税务明细、统一社会信用代码（本期暂无对应 key，预留） |

**承载字段（已与 M1 对齐，2026-10-28）：** 字段名确认为 `acquisition_cost`、`sensitivity`（整数 1–5）。两项**不写进 OpenAPI，也不进 §4 事实字典**——是补问排序（FR-05）的服务端配置，唯一实现口径是 `services/api/app/domain/questioning.py` 里的 `DEFAULT_COST` / `DEFAULT_SENSITIVITY` 常量（`choose_next_question` 的参数 `costs` / `sensitivity`）。改档位 = 改代码常量一处 + 同步本节，不做「启动时读 CSV」。**档位只表示相对顺序、不表示量化成本/敏感度的绝对值**；`reason` 会原样展示「获取成本档位 N、敏感程度档位 N」，故本节档位表须与代码常量逐 key 一致。本节为唯一口径：M1 按本节 1–5 表重写 `DEFAULT_COST` / `DEFAULT_SENSITIVITY`，并把 `UNKNOWN_COST` / `UNKNOWN_SENSITIVITY` 置为 5（未知键垫底）；`rdActivity` 获取成本定为 1（企业自行知道）、敏感度 4，已定稿。

## 6. 两人独立标注与仲裁流程

1. 标注人 A、B 各自从原文独立填写 `source_version/source_page_or_section/source_span/field/operator/value/unit/period/negation`，互不沟通。
2. 核对 A、B 两列：一致则直接进入仲裁确认；不一致在 `notes`/评审意见里写明双方依据。
3. 仲裁由第三人或指导教师按原文裁定，`adjudicated_expression` 填最终 AST，`review_status` 改「已仲裁」；仅当引用校验与人工审核均通过，才可交 M1 发布并标「可发布」。
4. 仲裁记录保留原双方意见，不覆盖、不删除。

## 7. 容易混淆的名词

| 名词 | 准确含义 | 不要误解为 |
| --- | --- | --- |
| 有效期 / 申报窗口 / 已关闭 | 有效期=规则何时适用；窗口=何时能递交；已关闭=窗口/有效期已过 | 有效期内就能现在申报；历史样本显示申报按钮 |
| 季度平均 | `(季初+季末)/2` 后四季度平均，需八输入 | 年末人数或任一单季度人数 |
| 事实期间 / assessment | 事实按「key+期间」定位；`assessment` 指本次判断所属年度 | 拿别的年份/季度数据挪来用 |
| 双人标注 / 仲裁 / 真值 | 两人独立标签，分歧由第三方据原文裁决 | 系统预测、模型回答、两人商量后的单一答案 |

## 8. 与 CSV 的对应关系

- 本规范 §1 的字段顺序即 `criteria_review.csv` 表头顺序，M1 据此导入。
- `enterprises.csv` 一行一个 `key+期间`；`region/industry` 为每行复用的企业属性列，不单独占 `fact_key` 行；`period` 用 `YYYY-MM-DD/YYYY-MM-DD`；`verified=false` 直到有已复核的合成材料。
- `labels.csv` 是条件级四态真值与仲裁结果，`split` 分开发/留出，留出集看结果前冻结（见 ACCEPTANCE 与 M3_HANDOFF）。
- `material_requirements.csv` 是税/资金两类「已审材料要求」（必交/条件交 + 材料→事实键映射），字段口径与对接说明见 [MATERIAL_REQUIREMENTS.md](./MATERIAL_REQUIREMENTS.md)，不由本规范 §1 覆盖。
