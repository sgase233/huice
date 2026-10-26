# 条款标注规范（M3）

> 依据《惠策协同_三人分工与开发计划_截至11月15日.md》、[实现与接口说明](../IMPLEMENTATION_AND_INTERFACE.md) §2.2（AST）与 §2.3（事实字典）制定。最终字段以 `data/policies/criteria_review.csv` 表头为准。本文只登记口径与操作流程，不写业务代码。

## 1. 总原则

1. **先核来源，再填条件。** 每个条件必须能回到官方原文的 URL、版本、页码/段落与原文片段；正文与附件分开标注。政策解读、自媒体摘要、模型回答都不能代替原文。
2. **一条原子条件一行。** 一个条件只表达一个可求值的事实比较/排除/期间计算，不把多个条件拼进一行。
3. **两人独立标注，再仲裁。** `annotator_a` 与 `annotator_b` 各自独立填写、互不先看结论；分歧由第三人/指导教师按原文裁决，并保留双方原意见。未仲裁不得标为可发布。
4. **无法核实写「待核实」，不猜测。** 数字方向、否定词、期间、行业目录无法从原文确认时，明确标注待核实，不臆造。
5. **历史通知标关闭；合成数据与真实办理分开。**

## 2. criteria_review.csv 字段释义

| 字段 | 含义 | 填写要求 |
| --- | --- | --- |
| policy_id | 政策编号 | 与 `source_register.csv` 一致 |
| criterion_id | 条件编号 | 政策内唯一，如 `SME-01`、`RD-02` |
| source_version | 来源文号 | 如 `财政部 税务总局公告2023年第12号` |
| source_page_or_section | 原文定位 | 如「第五条」「第一条」，让他人能定位到条款 |
| source_span | 原文片段 | 短片段，含关键数字、比较词、否定词、期间 |
| field | 事实字段 | 用实现说明 §2.3 的 key：`region/industry/establishedDate/taxableIncome/rdExpense/rdActivity/employeeCountStart/employeeCountEnd/assetsStart/assetsEnd` |
| operator | 运算符 | 见 §3 运算符表 |
| value | 数值/枚举 | 十进制字符串；未知留空或 `null`，不写 0 代替 |
| unit | 单位 | `code/date/CNY/person/boolean/ratio` |
| period | 期间 | `assessment`（本次判断年度）或 `YYYY-MM-DD/YYYY-MM-DD` |
| logic_group | 逻辑组合 | `and`/`or`，表达条件间关系；政策级默认 `and` |
| negation | 是否否定 | `true`/`false`；「非限制和禁止行业」这类否定已由 `industry_exclusion` 表达时记 `false` |
| annotator_a / annotator_b | 两名标注者独立意见 | 独立填写；未填者写「待标注」 |
| adjudicated_expression | 仲裁后 AST | 带 CSV 转义的 JSON，见 §4 形状 |
| review_status | 审核状态 | `待标注 / 分歧待仲裁 / 已仲裁可发布` |

## 3. 运算符与 AST 形状（实现说明 §2.2）

| op | 参数 | 求值方式 |
| --- | --- | --- |
| `and` / `or` | `children: Expression[]`，至少两个 | AND 全满足才满足；OR 任一满足则满足 |
| `not` | `child: Expression` | 满足/不满足互换，未知仍未知 |
| `compare` | `factKey,period,comparator,value,unit` | `comparator=lt/lte/eq/gte/gt/ne`；金额用十进制字符串 |
| `date` | `factKey,period,comparator,value`（日期） | 按日期比较 |
| `industry_exclusion` | `factKey=industry,period,excluded[]` | 行业值在名单中为不满足；缺行业为证据不足 |
| `quarterly_average` | `metric=employeeCount/assets,period,comparator,value,unit` | 各季度 `(季初+季末)/2`，再对完整四季度求平均 |

- `period` 为 `assessment` 或显式 `{startDate,endDate}`；年度季度平均须指向完整自然年度。
- 地区/行业等字符串比较只允许 `eq/ne`；区间用 AND 的上下界表达，不另设区间运算符。
- 比例用 `unit=ratio` 的十进制字符串；新增事实字段先改字典和契约，不在 CSV 里自造。

## 4. 数值 / 否定词 / 期间核对

1. **数值方向**：「不超过」「超过」「不低于」「低于」必须逐字对照原文，方向写反（如把「超过」写成「不超过」）应由 M1 的引用校验拦下。
2. **单位**：金额单位统一 CNY；「万元」「元」换算时保留 2 位小数，不丢精度。
3. **否定词**：「非」「不」「禁止」「排除」要落到 `negation` 或 `industry_exclusion`/`not`，不靠文字暗示。
4. **期间**：事实期间与判断期间要一致；季度平均表达式必须指向完整自然年度，不能拿单季代替全年，缺季度记证据不足而非 0。
5. **冲突**：正文与附件条件矛盾时，两处来源都保留，标「待人工仲裁」，不任选有利一方。

## 5. 获取成本 / 敏感程度整数档位（供补问排序）

供实现说明 §2.5 的补问排序使用，整数档位含义须可复现。

| 档位 | 获取成本（acquisition cost） | 例子 |
| --- | --- | --- |
| 1 | 企业内部已有、随手可得 | 地区、行业、成立日期 |
| 2 | 财务报表/申报表直接可查 | 应纳税所得额、研发费用 |
| 3 | 需财务整理计算 | 季度从业人数/资产平均值 |
| 4 | 需第三方出具或审计 | 研发费用专项审计报告、审计报告 |
| 5 | 需外部权威部门或特殊证明 | 行业资质、限制行业证明 |

| 档位 | 敏感程度（sensitivity） | 例子 |
| --- | --- | --- |
| 1 | 公开基本信息 | 地区、行业、成立日期 |
| 2 | 常规经营数据 | 从业人数、资产总额 |
| 3 | 财务数据 | 应纳税所得额、研发费用 |
| 4 | 经营细节/商业敏感 | 研发活动内容、研发项目明细 |
| 5 | 高度敏感/个人隐私 | 个人身份、税务明细、统一社会信用代码 |

## 6. 双人标注与仲裁流程

1. 两名标注者各自独立阅读原文，填写 `field/operator/value/unit/period/logic_group/negation` 与 `source_span`，写入 `annotator_a`、`annotator_b`。
2. 两者一致 → 进入仲裁复核；不一致 → 列出各自原文依据，交第三人或指导教师裁定，保留双方原意见。
3. 仲裁结果写入 `adjudicated_expression`，`review_status` 置「已仲裁可发布」；未仲裁前一律「待标注」或「分歧待仲裁」。
4. 单人复核、模型判断、两人商量后的单一答案，都不算双人标注。

## 7. 待办

- M1 确认 `adjudicated_expression` 的 JSON 字段命名与 §4 形状完全一致后，本条与 `criteria_review.csv` 才算冻结。
