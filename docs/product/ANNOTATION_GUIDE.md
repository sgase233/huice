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
policy_id,criterion_id,source_version,source_page_or_section,source_span,field,operator,value,unit,period,logic_group,negation,annotator_a,annotator_b,adjudicated_expression,source_document,review_finding,review_status,adjudication_note
```

| 列 | 含义 | 填写要求与允许取值 |
| --- | --- | --- |
| `policy_id` | 所属政策 | 与 `source_register.csv` 的 `policy_id` 一致 |
| `criterion_id` | 条件唯一标识 | 建议 `政策短名-序号`，如 `SME-001`、`RD-001`；同一政策内不重复 |
| `source_version` | 条件所属政策版本的文号 | 与 `source_register.csv` 的 `version` 对齐，供导入映射；**不是引文出处**——引文实际所在文档写 `source_document`。如「财政部 税务总局公告2023年第12号」；引用文未登记时写「文号（待登记来源）」 |
| `source_page_or_section` | 页码或条款 | 如「第五条」「第一条」「附件申报指南第3页」；页码与段落至少一个非空 |
| `source_span` | 原文片段 | 逐字摘录，能回查数字、比较方向、否定词和期间 |
| `field` | 事实 key / 指标 | 见 §4 事实字典及 §4.1 扩展键（共 18 键） |
| `operator` | 操作符 | 比较符 `lt/lte/eq/ne/gte/gt`，或 `industry_exclusion`、`quarterly_average`、`manual_review` |
| `value` | 阈值/列表 | 金额十进制字符串；行业排除为行业码列表，用 `\|` 分隔 |
| `unit` | 单位 | `code/CNY/person/boolean/date/ratio` |
| `period` | 所属期间 | `assessment`（本次判断所属年度）或显式 `YYYY-MM-DD/YYYY-MM-DD` |
| `logic_group` | 分组标注（仅供人复核，引擎不读） | 引擎纯 AND 合并（`rules.aggregate()` 不分枝），**不读 logic_group**；原文「或」必须写进同一条 `adjudicated_expression` 的 `or` 节点。组号仅辅助人复核，不影响求值 |
| `negation` | 是否否定 | `false` 为主；「不属于限制禁止行业」用 `industry_exclusion` 表达，不额外写否定 |
| `annotator_a` | 标注人 A | 独立填写，留空=尚未标注 |
| `annotator_b` | 标注人 B | 独立填写，留空=尚未标注 |
| `adjudicated_expression` | 仲裁后 AST | JSON 字符串，须 CSV 转义（内部双引号写成 `""`），操作符见 §3 |
| `source_document` | 引文来源文档 | 与 `source_span` 的段对齐、`\|` 分隔，每段为该引文所在文档的文号（对应 `data/policies/sources/<文号>.txt`）。留空表示用政策本体来源正文（`<policy_id>.txt`）；仅当引文出自政策本体之外的文件时填写 |
| `review_finding` | 冲突标记 | `conflict`（已审核冲突）或留空（`none`）。标 `conflict` 时：`adjudicated_expression` 用 `manual_review` 表达式占位（契约必填、不能留空）、`source_span` 必须含**两处不同**的权威原文（`\|` 分隔）、`source_document` 标注每段出处，规则引擎固定返回 `conflict`，可发布 |
| `review_status` | 复核状态 | `待双人复核`（默认）→ `已仲裁` → `可发布`；未仲裁不得写「可发布」。`待核实` 表示条件暂无法写成可执行 AST，该行仅保留在 CSV 供复核、**不进库**——`adjudicated_expression` 留空，导入时会被 API-06 以 422 拒绝（这是护栏，不是被忍受的状态）；转「可发布」前必须先补齐 AST |
| `adjudication_note` | 裁决备注 | 仲裁结论、兜底说明、日期读法等；仅需要时填写，多数行为空 |

**注意：** `field/operator/value/unit/period` 是给人复核的扁平投影，M1 导入以 `adjudicated_expression` 的 JSON AST 为唯一程序口径；两者不一致时以 AST 为准并回改投影列。**例外**：合并条款（如 SRDI-002、RDSUB-001）的 `operator=and/or`、`manual_review` 行（如 SME-001、SRDI-004~008）与冲突行（`review_finding=conflict`）的 `field/value/unit/period` 均留空；合并行与 manual_review 行只以 AST 为准、不回改。冲突行的 AST 用 `manual_review` 占位（契约与库均要求非空，不能留空），求值时被 `review_finding=conflict` 短路，不参与实际判断。**2026-11-02 复核后本数据集暂无 `review_finding=conflict` 样本**：原 HT-007 引用的 195号 20% 超出本政策适用范围，已改判为 `manual_review`，理由见该条的 `adjudication_note`；补真冲突样本前，`labels.csv` 的 `conflict` 态无覆盖（见 §6.2）。

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
| `date` | `factKey=establishedDate/specificMarketSince,period,comparator,value(YYYY-MM-DD)` | `{"op":"date","factKey":"specificMarketSince","period":"assessment","comparator":"lte","value":"2023-10-13"}` |
| `quarterly_average` | `metric=employeeCount/assets,period,comparator,value,unit` | `{"op":"quarterly_average","metric":"employeeCount","period":"assessment","comparator":"lte","value":"300","unit":"person"}` |
| `industry_exclusion` | `factKey=industry,period,excluded[]` | `{"op":"industry_exclusion","factKey":"industry","period":"assessment","excluded":["H62"]}` |
| `and` / `or` | `children:[...]`（至少两个） | 政策级默认 AND；**原文「或」必须写进同一条 AST 的 `or` 节点**（引擎不读 logic_group，分行会被纯 AND 合并成空集） |
| `not` | `child` | 仅在原文明确「不属于…但…」时用 |
| `manual_review` | `reason`（必填非空，≤200 字） | `{"op":"manual_review","reason":"本条须人工核查：…"}`；求值恒为 `insufficient_evidence`、理由回显 reason，绝不判满足 |

- 数值一律十进制字符串，禁止用 JS float 预计算结果；金额最多 2 位小数。
- 布尔条件 `rdActivity` 的 `value` 必须是 JSON 布尔值 `true`/`false`（不带引号），不能写成字符串 `"true"`。
- `period` 取值：`assessment`（仅用于与本次判断年度一致的事实，如当年应纳税所得额）或显式日期对象 `{"startDate":"YYYY-MM-DD","endDate":"YYYY-MM-DD"}`。跨期/上年度/近两年等「期间专指」事实**必须写显式起止日期、不能写 assessment**——引擎按 `(key, period_start, period_end)` 精确查表，assessment 只会解析成本次评估期间、不会自动往前退一年，写错期间会恒为 insufficient_evidence。季度平均必须指向完整自然年度。
- `quarterly_average` 的 `field` 列填指标名 `employeeCount`/`assets`（对应 §4 的 `employeeCountStart/End`、`assetsStart/End` 八个季度起止输入）。
- 区间用 AND 的上下界表达，不另加操作符；比例以 `unit=ratio` 的十进制字符串表达。
- 冲突节点先单独检测并向上传播为 `conflict`，不得被 OR 短路掩盖。
- `manual_review`（rc.5）与「待核实」是两个不同状态：`manual_review` 是**有效、可发布、进库**的 AST，表达「本条按政策口径本就不由程序判、须人工核查」，求值恒 `insufficient_evidence`（AND 上层随之降级，绝不判满足）；「待核实」是 AST 留空、**不进库**（API-06 以 422 拒绝）。只有在确实拿不到可执行口径、又必须让整版政策发得出去时，才用 `manual_review`，且 `reason` 必须写明核查什么。

> **行业排除的码表与三条口径**（财税〔2015〕119号第四条负面清单 → GB/T 4754-2017 大类码、按前缀命中；三条口径：兜底项不落码、大类覆盖不到中/小类时 `excluded` 写细码、小微「国家非限制和禁止行业」为粗粒度近似）以 [IMPLEMENTATION_AND_INTERFACE.md §2.2](../IMPLEMENTATION_AND_INTERFACE.md) 为准。

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

## 4.1 地方通知扩展事实键（预计算事实口径，rc.3+rc.4 已合入 main，共 18 键）

第二阶段地方通知（`SZ-GRANT-RD-SUBSIDY-2026`、`SZ-GRANT-SRDI-2025`）需要的事实键超出原 10 键字典，现共 18 键。按「**预计算事实**」口径处理：增量与占比由 M3 在 `enterprises.csv`/`labels.csv` 里给出已算好的数值，M1 只做 `compare`，**不新增 delta/ratio 算子**。以下键已由 M1 扩展 `FACT_UNITS` 与 OpenAPI `FactKey` 枚举（rc.3 扩 6 键 + rc.4 扩 2 键，现已合入 main）：

| key | 含义与计算口径 | value 与 unit | period（事实侧与 AST 侧一致，均显式日期） | 需求来源 |
| --- | --- | --- | --- | --- |
| `revenue` | 上年度营业收入总额 | 十进制字符串，金额最多 2 位小数；`CNY` | 上年度显式日期，如 2025-01-01/2025-12-31 | SRDI 二（二）（三） |
| `equityFinancing` | 近2年新增股权融资总额（合格机构投资者实缴额） | 十进制字符串；`CNY` | 近两年显式日期，如 2025-01-01/2026-12-31（SRDI 2025 为 2023-01-01/2024-12-31） | SRDI 二（三） |
| `rdExpenseIncrease` | 上年度研发费用增量 = 上年度 `rdExpense` − 前年度 `rdExpense`（M3 预计算） | 十进制字符串（可为负）；`CNY` | 上年度显式日期，如 2025-01-01/2025-12-31（= 该年度 − 前一年） | RD-SUBSIDY 第八条（二）1 |
| `rdExpenseRevenueRatio` | 上年度研发费用占营业收入比重 = `rdExpense` ÷ `revenue`（M3 预计算，如 3% 记 `0.03`） | 十进制字符串；`ratio` | 与 revenue/rdExpense 同一期间（上年度显式日期） | SRDI 二（二） |
| `rdAddDeductionClaimed` | 近两年均已申报享受研发费用加计扣除 | JSON boolean；`boolean` | 近两年显式日期，如 2024-01-01/2025-12-31 | RD-SUBSIDY 第八条（二）1 |
| `creditRecord` | 诚信记录良好（无限制申请财政性资金惩戒、无超期未验收未退款） | JSON boolean；`boolean` | `assessment`（当前状态，同 region/establishedDate；判断后取消记录→PATCH→stale） | RD-SUBSIDY 第八条（三） |
| `specificMarketSince` | 进入特定细分市场的日期（**不是**经营年数） | `YYYY-MM-DD`；`date` | 画像年度 `assessment` | SRDI 二（一） |
| `independentLegalEntity` | 具有独立法人资格 | JSON boolean；`boolean` | 画像年度 `assessment` | RD-SUBSIDY 第八条（一） |

> **AST period 口径**：除 `creditRecord`（当前状态，用 `assessment`，同 region/establishedDate）外，其余 5 键均为期间专指事实，`adjudicated_expression` 的 `period` 一律写 `{"startDate":"YYYY-MM-DD","endDate":"YYYY-MM-DD"}`，并与事实侧 `(key, period_start, period_end)` 逐字一致，否则查不到事实、恒为 insufficient_evidence。具体年份按各政策申报年度核定（SRDI 2025 上年度=2024；RD-SUBSIDY 首个申报年度上年度=2025）。

> 「从事特定细分市场≥2年」已定稿：键名 `specificMarketSince`（`date` 类型，进入特定细分市场的日期，**不是**年数），用 `date` 算子 `specificMarketSince <= 申报年度倒推 2 年` 表达；另新增 `independentLegalEntity`（boolean，独立法人资格）。

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

**地方通知 6 键档位（已定稿）：**

| key | 获取成本 | 敏感度 |
| --- | --- | --- |
| `revenue` | 2 | 3 |
| `equityFinancing` | 3 | 4 |
| `rdExpenseIncrease` | 2 | 3 |
| `rdExpenseRevenueRatio` | 2 | 3 |
| `rdAddDeductionClaimed` | 2 | 3 |
| `creditRecord` | 3 | 4 |

**承载字段（已与 M1 对齐，2026-10-28）：** 字段名确认为 `acquisition_cost`、`sensitivity`（整数 1–5）。两项**不写进 OpenAPI，也不进 §4 事实字典**——是补问排序（FR-05）的服务端配置，唯一实现口径是 `services/api/app/domain/questioning.py` 里的 `DEFAULT_COST` / `DEFAULT_SENSITIVITY` 常量（`choose_next_question` 的参数 `costs` / `sensitivity`）。改档位 = 改代码常量一处 + 同步本节，不做「启动时读 CSV」。**档位只表示相对顺序、不表示量化成本/敏感度的绝对值**；`reason` 会原样展示「获取成本档位 N、敏感程度档位 N」，故本节档位表须与代码常量逐 key 一致。本节为唯一口径：M1 按本节 1–5 表重写 `DEFAULT_COST` / `DEFAULT_SENSITIVITY`，并把 `UNKNOWN_COST` / `UNKNOWN_SENSITIVITY` 置为 5（未知键垫底）；`rdActivity` 获取成本定为 1（企业自行知道）、敏感度 4，已定稿。

## 6. 两人独立标注与仲裁流程

1. 标注人 A、B 各自从原文独立填写 `source_version/source_page_or_section/source_span/field/operator/value/unit/period/negation`，互不沟通。
2. 核对 A、B 两列：一致则直接进入仲裁确认；不一致在 `notes`/评审意见里写明双方依据。
3. 仲裁由第三人或指导教师按原文裁定，`adjudicated_expression` 填最终 AST，`review_status` 改「已仲裁」；仅当引用校验与人工审核均通过，才可交 M1 发布并标「可发布」。
4. 仲裁记录保留原双方意见，不覆盖、不删除。
5. `待核实` 的行只能进复核清单，不能直接升「可发布」：必须先补出可执行、引用校验通过的 `adjudicated_expression`，再走双人标注 + 仲裁，才能标「可发布」。

### 6.1 当前标注状态（2026-11-01 更新：留出集已双人标注并仲裁）

**留出集（6 组合 / 26 条）已完成两人独立标注 + 仲裁。** `labels.csv` 中：

- `annotator_a_result` = 标注人 A 的原始判定；
- `annotator_b_result` = 标注人 B 的原始判定；
- `adjudicated_result` = 仲裁结论，分歧依据写入 `notes`；
- 双方原始意见保留，不覆盖（§6 第 4 条）。

仲裁统计：**A/B 分歧 11 条**，全部裁定为与 B 一致（即与 AST 语义 + `enterprises.csv` 实际事实一致）。分歧集中在两类：

| 分歧类型 | 条数 | 仲裁口径 |
| --- | --- | --- |
| A 假定企业"满足"某人工核查条件（如假定为创新型中小企业、高企、已完成科技统计）→ 判 `satisfied` | 7（SRDI-004~008、HT-002、HT-005） | 这些条的 AST 是 `manual_review`，**恒为 `insufficient_evidence`**，不得按假定判满足；企业也未提供对应事实 |
| A 以"行业整体排除"否决单个条件 → 判 `not_satisfied` | 2（RD-002、RD-003） | 条件级只看本条：`rdActivity=true`、`rdExpense=800000>0` → `satisfied`；行业排除由 RD-001 承担，政策级聚合才是不满足 |
| A 称企业缺人数/资产数据 → 判 `insufficient_evidence` | 2（SME-003、SME-004） | `enterprises.csv` 实际有四季度人数与资产，季均人数 16、季均资产 540 万，均在阈值内 → `satisfied` |

**开发集（24 组合 / 118 条）仍为单人推算**，尚未双人标注；不得当作评测真值对外报告。

**标注人提出的内容质量问题（已记录，待处理）**：

1. **RD-001 出处**：负面清单出自财税〔2015〕119号，非 2023 年第 7 号公告。`criteria_review.csv` 的 `source_document` 标 119 号（按 `source_span` 8 段对齐）；`source_version` 与 `labels.csv` 的 `policy_version` 均记政策版本（7 号），两者不矛盾。
2. **RD-002 与 RD-003 的 `source_span` 相同**（同句派生）。语义上分别对应"有研发活动"与"研发费用>0"，可接受；如需更强的引文区分，待与 M1 商定后调整并重跑闸门。
3. **HT-007 引文归属（2026-11-02 复核后改判）**：10% 出自财税〔2015〕119号，20% 出自国科发火〔2016〕195号。复核确认深科技创新规〔2021〕5号 **第三条** 明文采用 119号 的研发费用归集范围，而 195号 的 20% 在本政策中仅见于**第七条**（第三方研发费用审计/鉴证机构资质要求），不适用于归集口径——两者不是同一条件的冲突，原 `review_finding=conflict` 作废，改为单引 119号 的 `manual_review`（其他相关费用占比无事实键）。
4. **SRDI-001 年限版本**：两位标注人均提示 2026 新办法改为"3 年"；本项目 2025 申报适用 2022 暂行办法"2 年"，见 §9 备忘。

独立标注表 `holdout_annotation_sheet.csv` 保留为空白模板，供后续新增留出样本复用。

**开发集待办（2026-11-01）**：开发集 26 组合 / 122 条（含新增版本对照 4 条）已导出为 [`data/evaluation/dev_annotation_sheet.csv`](../../data/evaluation/dev_annotation_sheet.csv)（空白 `annotator_b_result/annotator_b_note`，不含 M3 推算结果），待第二标注人独立完成。填完后按 §6 仲裁并回填 `labels.csv`。

**⚠️ 2026-11-01 两次退回记录**：

1. **第一次**：标注人提交的是**条款内容描述**（复述"禁止行业清单""100%/200% 优惠内容"），未按四态填 `annotator_b_result`。
2. **第二次**：改填四态后，判断仍**与具体企业无关**——同一条件在五家企业给出相同结论。例：`SME-002`（应纳税所得额≤300 万）在 C001（280 万）与 C002（**450 万**）均判 `satisfied`，而 450 万应判 `not_satisfied`。这说明标注人是在**审条款是否忠实原文**，不是在**按企业事实判四态**。

**根因（M3 的表设计缺陷）**：原标注表只给 `source_span`，**没有给该企业的相关事实值**，标注人无从判断满足与否，只能按条款文本作答。两张表均已补 `enterprise_facts` 列（按条件所需事实键带出该企业的实际值、单位与期间；无事实键的条件标注"属人工核查项"），表头也写明四态枚举。

**下发标注表时必须同时说明三句话**：① 判的是"这家企业满足不满足"，不是"条款写得对不对"；② 只能填 `satisfied / not_satisfied / insufficient_evidence / conflict` 四个词之一；③ 所需企业事实已在 `enterprise_facts` 列，缺该值就是 `insufficient_evidence`。

**✅ 2026-11-01 开发集双人标注完成**：补齐事实列后重新下发，标注人给出的是按企业的四态判定。开发集 122 条：**A/B 一致 110 条、分歧 12 条**，分歧已逐条仲裁、全部维持 A，依据写入 `labels.csv` 的 `notes`：

| 分歧条目 | 条数 | A / B | 仲裁理由 |
| --- | --- | --- | --- |
| `SME-001` | 1（C002） | insufficient_evidence / not_satisfied | 本条只判「非限制和禁止行业」，数值条件已拆为 SME-002~004；B 把整段第五条当合成条件。**已收窄 `source_span` 至「从事国家非限制和禁止行业」**，从源头消除歧义 |
| `RD-002` | 5 | satisfied / insufficient_evidence | 本条判「是否开展研发活动」（`rdActivity=true`），不判费用金额；费用>0 由 RD-003 承担，B 将两条混同 |
| `HT-004` | 1（C027） | satisfied / insufficient_evidence | 本政策申报年度 2025 的上一会计年度 = **2024**，企业该期间 `rdAddDeductionClaimed=true`；B 误把上年度当作 2025 |
| `HT-007` | 5 | conflict / insufficient_evidence | 本条是经人工确认的条款冲突（`review_finding=conflict`），引擎固定返回 conflict、与具体企业无关；B 漏掉冲突优先级。**2026-11-02 复核改判：该 conflict 结论不成立**（深创〔2021〕5号 第三条采用 119号 归集范围），本轮仲裁作废，对应 6 条标签改判为 `insufficient_evidence` |

至此 **留出集 + 开发集均完成双人标注与仲裁**；`labels.csv` 可作为评测真值使用（仍标注为合成的、非真实税务结论）。

**该标注人提出并经裁定的三项结构问题**：

| # | 问题 | 裁定 |
| --- | --- | --- |
| 1 | `RD-002` 与 `RD-003` 的 `source_span` 完全相同，区分度不足 | **保留**。同句派生两个原子条件（RD-002 判研发活动存在、RD-003 判研发费用>0），符合"一条原子条件一行"；不作区别引文。若确需活动范围/排除清单，应另立条款并引财税〔2015〕119号原文。已记入 `RD-003` 的 `adjudication_note` |
| 2 | `GXJS-V1-001`（2008 年版）已被 2016 年版废止，是否保留 | **有意保留为历史版本对照样本**，正是 11/03 要求的"版本变化"覆盖；`source_register` 标 `closed`，`criteria` 的 note 已加效力声明"仅用于版本失效对照演示、不得作为现行规则发布" |
| 3 | `SRDI-001/002/004` 的认定标准源自《暂行办法》，本通知仅转致，来源如何记 | **`source_span` 严格取本政策文字**（已逐字核实存在于通知正文），上位依据写进 `adjudication_note`、不混入 `source_span`。三条已补上位依据说明 |

### 6.2 conflict 态样本与真冲突判定（2026-11-02）

`review_finding=conflict` 是 `labels.csv` 中 `conflict` 态的唯一来源：引擎对该条固定返回 `conflict`（与具体企业事实无关），政策级按「存在冲突即 conflict」聚合。**当前数据集暂无该样本**（原 HT-007 改判后归零）。

判定真冲突须同时满足：① 同一制度、同一适用对象；② 两份权威文本或同一文本的两处条款，对**同一条件**给出互斥要求（数值不同、方向相反、口径互斥）；③ 都适用于本政策（本政策明文援引，或就是政策自身条款）。**排除**：跨制度口径差异、版本废止（已有 GXJS 版本对覆盖）、期间或适用范围不同。

2026-11-02 对 `data/policies/sources/` 的 9 份来源快照逐条检索，候选与排除理由见 `docs/devlog/M3.md` 同日条目。**注意：仓库内来源多为节选而非全文**（119号、195号 快照各只有一句，其余几份也是节选），因此检索结论只能表述为「**在这 9 份节选来源内未发现构成真冲突的一组**」，不能写成「不存在真冲突」。要补回 conflict 样本，需先取回候选政策的全文再比对（冲突通常出现在正文条款与附则/申报指南之间）；能否取得全文待定，在此之前 `ACCEPTANCE.md` 的「冲突条款」项保持「未覆盖」。

**2026-11-02 全文补检：** 按 `source_register.csv` 的官方 URL 取回 8 份来源全文（2023年第7号、2023年第12号、深工信规〔2025〕8号 通知与细则、深科创规〔2026〕1号、深科技创新规〔2021〕5号、国科发火〔2016〕32号、财税〔2015〕119号），另取回财税〔2018〕64号 全文，逐条比对，仍未发现构成真冲突的一组；64号 第七条明文废止 119号 第二条「委托境外不得加计扣除」，属后文更替。国科发火〔2016〕195号 仅取到发文通知、附件《工作指引》正文未取。全文未归档进 `data/policies/sources/`（归档会改变闸门输入，待 M1 评估），因此结论限定为「已取得全文的来源内未发现」，不推及全部。

**当前 `conflict` 态仅由单元测试覆盖，没有真实数据支撑。** 在取得候选政策全文并确认真冲突前不补样本，也**不使用构造数据充数**——构造出来的冲突标签无法证明引擎在真实原文上判得对，反而会让人误以为该场景已验证。

唯一讨论过的候选——深科创〔2021〕5号 第六条（一）「高新技术企业**和**上年度高新技术企业培育入库企业」与第九条（一）「未获得过高新技术企业资格」——**经复核不构成冲突**：「和」连接两个名词短语、是两类申请人的并集，第九条紧接着在定义第二类申请人，二者是「条件条款 ← 定义条款」的引用关系，不是并列要求；合取读会使无主体可申请，属表述瑕疵（该用「或」）而非来源互斥；且 HT-002 本已是 `manual_review`，两种读法均落 `insufficient_evidence`，标 `conflict` 只损失演示价值。已在 HT-002 的 `adjudication_note` 记明，后续不再作为候选。

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
- `holdout_annotation_sheet.csv` 是留出集的第二标注人独立标注表（空白结果列），口径见 §6.1。
- `material_requirements.csv` 是税/资金两类「已审材料要求」（必交/条件交 + 材料→事实键映射），字段口径与对接说明见 [MATERIAL_REQUIREMENTS.md](./MATERIAL_REQUIREMENTS.md)，不由本规范 §1 覆盖。

## 9. 已确认口径备忘（2026-11-01 labels 审核确认）

以下口径经 `labels.csv` 审核确认，标注与复核时不得按右栏误解：

| 政策/名词 | 准确口径（本项目采用） | 不要误解为 |
| --- | --- | --- |
| SRDI 年限 | 2022《优质中小企业梯度培育管理暂行办法》（工信部企业〔2022〕63号）「从事特定细分市场 2 年以上」；本项目为 2025 年申报 | 2026 新办法（工信部企业〔2026〕2 号）「截至上年末 3 年以上」——2026 申报才适用 |
| SRDI 前置条件 | 「须先获创新型中小企业称号」已标注为 SRDI-004 manual_review | 漏掉该前置条件 |
| RDSUB | 《深圳市研发投入补助计划项目管理办法》（深科创规〔2026〕1号），属财政资金资助（grant），第八条（三）明文含「诚信记录良好」等要件 | 研发费用加计扣除税收政策（其要件无诚信排除项） |
| SME | 小型微利企业口径：应纳税所得额≤300 万 + 从业人数≤300 + 资产总额≤5000 万 + 非限制禁止行业（财政部 税务总局公告2023年第12号） | 工信部《中小企业划型标准规定》（工信部联企业〔2011〕300 号）——划型是 SRDI-005 的 manual_review |
| HIGHTECH 门槛 | 第六条（二）固定金额：上年度研发费用≥100 万（规上≥50 万） | 高企认定研发费用占比（5%/4%/3%）与高新收入≥60%——那是「是否为高企」的资格问题（HT-002 manual_review） |
| 高企认定 版本 | 同一政策线 `CN-TAX-GXJS` 两版：v1 国科发火〔2008〕172号（**6%**/4%/3%，档一"小于5000万"不含本数）、v2 国科发火〔2016〕32号（**5%**/4%/3%，档一"小于5000万（含）"）；v2 第二十三条明文废止 v1 | 把两版当两项不同政策（它们是同 policyId 的两个 version）；或以为 6%/4%/3% 仍现行 |
| 高企认定 近三年口径 | 原文为「近 3 个会计年度研发费用总额占同期销售收入总额」，本项目事实字典只有单年度 `revenue`/`rdExpenseRevenueRatio`，条款按**上年度单年近似**并在 note 标注待补 | 以为已按三年合计口径求值 |
