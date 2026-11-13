# 惠策协同：功能实现逻辑与接口协作约定

> 初稿：2026-10-23；当前状态更新：2026-11-11，E1 后端实现已合入，7 个 E1 操作已并入正式契约（`1.1.0-rc.1`）。成员沿用 M1（后端）、M2（前端）、M3（政策与验收）。
>
> 本文依据[需求分析](./brief/惠策协同_需求分析与功能范围.md)、[原开发计划](./brief/惠策协同_三人分工与开发计划_截至11月15日.md)、[策划案](./brief/华为ICT大赛赛题一_政策红利挖掘与申报多智能体_初步策划案.md)、[评审建议](./brief/评审建议_惠策协同方案_可行性与优化.md)及现有代码整理。后端已实现 **31 个操作**：已发布的 **23 个**（API-01—16 + AUTH-01—07）加 E2 的 **8 个**发现/候选操作（DISC-01—08，只在 `contracts/openapi.e2-draft.yaml` 里，见 §4.5），前端六类业务页已接入，已有隔离库/真实 HTTP/合成数据的阶段浏览器记录；最终云端联合与业务验收仍按实际证据确认。第 8 节保留各日期历史验证，不代表本次重跑。后续范围和排期见[开发计划](DEVELOPMENT_PLAN.md)，新增账号、发现候选及图表接口见[扩展接口设计](EXTENSION_INTERFACES.md)，该设计待签收，不能替代现行 OpenAPI。

## 1. 范围、依据和成功标准

**2026-10-24 规则更新：**项目负责人已明确确认允许 DeepSeek 替代 MaaS、其他 AI 编码助手替代 CodeArts，不再将 MaaS/CodeArts 接入列为项目验收必选项。最终材料可不列其他编码助手名称，统一说明“AI 辅助编码”，但不得把未使用的工具写成已使用。以[工作手册“模型与编码工具规则”](../WORKING_GUIDE.md)为准；不改变真实模型联调、契约复核与测试要求。

原交付范围为深圳合成企业的政策机会分析：2 项国家税收政策、3—5 项深圳资金通知、6—8 家企业和至少 30 个判断组合，FR-01～FR-09 为原验收 P0。2026-11-09 根据 #46—#48 将账号隔离、定时政策发现和指定图表加入扩展计划；自动发现已从旧 P1 进入本次扩展范围，但不自动发布。文件 OCR、金额试算、第二城市、提醒与批量报告仍后置。不代企业申报，不把历史通知显示成当前开放项目。

实现顺序：政策来源与标注 → 契约/事实字典 → 引用校验与规则求值 → 企业判断 → 补问/清单/版本 → 页面联调 → 评测部署。先完成确定性链路，再接 DeepSeek 解释和向量检索；模型不决定资格。

主要用例图见 [PlantUML 源文件](./diagrams/main-use-cases.puml)、[PNG](./diagrams/main-use-cases.png)、[SVG](./diagrams/main-use-cases.svg)；角色、用例关系及本地插件预览/导出方法见[图示说明](./diagrams/README.md)。

| 可验证标准 | 负责人/检查位置 |
| --- | --- |
| 23 个操作的字段、枚举、状态码、参数位置、权限和示例一致 | M1/M2；`contracts/openapi.yaml`、`src/api/types.ts`、`test_contract.py` |
| 数字方向抽错不能发布；未发布规则不能判断；缺事实不判满足 | M1/M3；`test_rules.py`、`test_agent_flow.py` |
| 补答只影响相关条件；跳过不改事实；0 与 null 区分 | M1/M2；规则测试和浏览器用例 |
| 政策或事实变化能指出依赖和失效原因，保留历史原文及结果 | M1；`test_invalidation.py` |
| 税收/资金清单不同；页面与报告展示相同依据和降级提示 | M2/M3；`critical-flow.spec.ts`、`ACCEPTANCE.md` |

来源优先级：原需求加负责人后续 issue 决定约束功能范围（扩展决定及链接见开发计划）；本文细化现行调用行为；OpenAPI 是已评审 HTTP 字段的单一真源；TypeScript 类型与后端 schema 必须跟随 OpenAPI；`AGENT_PROTOCOL.md` 只规定内部调度。旧计划的 14 个 API 保留原编号，API-15/16 供后台读取草稿，不能用公开接口泄漏未审内容。新增 AUTH/DISC/VIS 编号暂仅为设计追踪，不计入已实现端点数，不宣称已获全员签收。

## 2. 各功能怎样实现

### 2.1 FR-01：政策导入与版本

1. `AdminReviewPage → client.importPolicy → admin_policies.py → ingestion → storage/obs`。前端提交 `multipart/form-data`，`metadata` 是 JSON 字符串；附件用同名 `files` 重复添加，不能手写 multipart boundary。**第一个文件是政策正文（`kind=body`），其余按上传顺序为附件（`kind=attachment`）**；`files` 可为空，此时草稿 `sources: []`，正文留给后续抓取阶段补齐，导入不会凭空生成原文。
2. 新政策不传 `policyId`，后端分配 UUID；新版本传原 `policyId` 和新 `version`。`(policyId, version)` 唯一。已发布版本不能原地改元数据/规则，必须重新导入新版本，保留旧版；仅有草稿不让旧判断失效。
3. `metadata` 包含标题、类型、机关、地区、文号（可 null）、适用主体、发布日期、有效日期、申报窗口、官方入口和来源 URL。后端记录 `fetchedAt`、文件哈希和正文/附件关系。URL 必须通过服务端官方域名白名单检查；跳转后也检查目标，禁止访问本机、内网和非 HTTPS 地址。网络抓取失败返回错误，不凭空补原文。
4. 导入后为 `pending_parse`；抽取完成为 `pending_review`；人工发布后为 `published` 或按时间推导为 `closed`。内部保存“是否已发布”和有效日期，`closed` 是已审核但过期/窗口关闭的公开视图，不代表未审核。
5. M3 可以人工填规则，仍须经过引用校验与发布。官方 URL 不是校验通过的充分条件；原文和定位必须存在。

### 2.2 FR-02：条款、AST 与引用审核

`extract(version, modelVersion) → extraction → validate_citations → 草稿与 findings`。模型返回值先做结构检查；引用校验核对源版本、片段哈希、数值、单位换算、比较方向、否定、行业与期间。含糊内容返回 `needs_review`，不能只靠关键词相同放行。

`PUT criteria` 整体替换指定版本的草稿条件，保存结构合法但引用失败的草稿并返回 `validationFindings`，让审核员看到问题；结构不合法返回 422。`validationFindings` 里 `criterionId` 为 null 的是**政策级**发现（如两条条件合取为空），不挂在单个条款上。发布时重新校验，存在 `failed/pending` 条件、会挡发布的政策级发现（目前只有 `POLICY_UNSATISFIABLE`；抽取阶段的 `EXTRACTION_*` 是过程提示，不挡）或未确认人工审核均返回 422。不能信任前端传入的“校验通过”状态。待核实冲突可以作为**已审核的冲突记录**发布，带两处来源和 `reviewFinding=conflict`，规则引擎固定返回 `conflict`；这与“引用不支持表达式”不同，后者不能发布。

每个 `CriterionDraft` 为 `{id,title,expression,sourceSpans,reviewFinding}`；后端增加 `validationStatus`。`sourceSpans` 至少一个，冲突至少两个；含 `sourceId,sourceUrl,sourceVersion,page,section,quote,textHash`，`page` 与 `section` 至少一个非 null。`sourceId` 定位已保存正文或附件；哈希由后端依据保存文本计算并验证。

AST 仅接受以下形状，不能执行 Python/JS 字符串：

| `op` | 参数 | 求值方式 |
| --- | --- | --- |
| `and` / `or` | `children: Expression[]`，至少两个 | AND 任一不满足则不满足；全满足才满足。OR 任一满足则满足；全不满足才不满足；其余证据不足。**例外**：分档表的相邻两档在交界点同时接住企业、而较严一档不成立时返回证据不足（边界重叠），见下 |
| `not` | `child: Expression` | 满足/不满足互换，未知仍未知 |
| `compare` | `factKey,period,comparator,value,unit` | `comparator=lt/lte/eq/gte/gt/ne`；数值用 Decimal，禁止 JS float 预先算结果 |
| `date` | `factKey∈{establishedDate,specificMarketSince},period,comparator,value`（日期） | 按日期比较，禁止本地化日期字符串；rc.4 起不再只认 `establishedDate` |
| `industry_exclusion` | `factKey=industry,period,excluded[]` | 企业行业码以清单任一项为**前缀**即不满足（清单写大类、企业可填更细）；缺行业为证据不足。码表见下 |
| `quarterly_average` | `metric=employeeCount/assets,period,comparator,value,unit` | 各季度 `(期初+期末)/2`，再对完整四季度求平均；保留八个输入与中间值 |
| `manual_review` | `reason`（1–200 字） | rc.5 新增。**固定返回 `insufficient_evidence`**，reason 原样回显「本条须人工核查：…」；不带事实、不带期间，不产生依赖。只由标注方写入，抽取智能体不得生成 |

AST 中 `period` 是 `assessment`（本次判断所属年度）或显式 `{startDate,endDate}`；年度季度平均表达式必须指向完整自然年度。地区等字符串比较只允许 eq/ne。区间用 AND 的上下界表达，不另加操作符。比例以 unit=`ratio` 的十进制字符串表达；新增事实字段要先改字典和契约。所有冲突节点先单独检测并向上传播为 `conflict`，不得被 OR 短路掩盖。

**分档交界（2026-11-02，PR #21 合并后）**：分档表用 OR 并列表达时，相邻两档在交界值上会同时接住同一个企业（「不超过 5,000 万元 5%」与「5,000 万元至 2 亿元 4%」，下界都含 5,000 万元），而 AST 表达不出「按更严的一档判定」；较松一档若写成「小于 5,000 万元」这种严格不含本数的形式，交界点就不重叠——V1 的档一即如此，只在 2 亿元处重叠。引擎在某一支成立、另一支与它**边界值相同、上下界相接**且在同一指标上要求更严却明确不成立时，整条降为 `insufficient_evidence`（理由「边界重叠…需人工确认」）。以下情形**不**降级：两支都成立；不成立的是分档指标本身（企业在另一段区间）；边界值不等（区间重叠的并列选项）；没有同一指标、同一方向的更严对照；另有两条以上并列支成立（企业本来就有别的路径达标）。嵌套的 `or` 依次展开：成立支取其成立的子支，不成立支取全部子支；`and` 里已成立的 `or` 视为恒真项略过，未成立则整组不参与对照。**当前数据不会触发**：GXJS V1/V2 的比例条件已于 2026-11-02 改为 `manual_review`，整条恒为证据不足；分档样本要等可执行的比例事实（近三年累计口径）落地后再补。

**`manual_review` 的使用边界（rc.5，2026-10-31）**：它回答的是「这条按政策口径本就不由程序判」，不是「标注还没做完」。两者必须分开——标注没做完的行没有表达式、`review_status` 停在 `待核实`；写了 `manual_review` 的行是**已完成的结论**，理由要能说清人工去核什么，说不清就不该用它。

- 只用于：原文指向的目录/名单没有稳定编码（小微的「国家非限制和禁止行业」、「产品不属于禁止限制淘汰类」），或用现有事实表示不出来的条件。
- 引文照样逐字校验（`textHash` 与 span 比对不变），「判不了」不等于「引文可以随便写」。
- 发布闸门照常放行：`validationStatus` 说的是结构与引文没问题，与「判定为真」不是一回事；判断结果固定是 `insufficient_evidence`，落在 AND 里会让整体降级，落在 OR 里不影响另一支成立。
- 抽取智能体不得生成它——模型没有资格宣布「这条判不了」，那只能由标注方写。

**行业排除的粗粒度码表（2026-10-30，只用于 `RD-001`）**：`industry_exclusion.excluded` 只写 GB/T 4754-2017 的**门类字母 + 两位大类**码；企业侧 `industry` 事实可以记到同一编码体系的任意粒度，运行时按**前缀命中**（清单 `C16` 命中企业 `C1621`，不命中 `C17`）。财税〔2015〕119 号第四条负面清单对应的 9 个码：

| 原文行业 | 门类 | 大类码 | GB/T 4754-2017 名称 |
| --- | --- | --- | --- |
| 烟草制造业 | C 制造业 | `C16` | 烟草制品业 |
| 住宿和餐饮业 | H 住宿和餐饮业 | `H61` | 住宿业 |
| | H | `H62` | 餐饮业 |
| 批发和零售业 | F 批发和零售业 | `F51` | 批发业 |
| | F | `F52` | 零售业 |
| 房地产业 | K 房地产业 | `K70` | 房地产业 |
| 租赁和商务服务业 | L 租赁和商务服务业 | `L71` | 租赁业 |
| | L | `L72` | 商务服务业 |
| 娱乐业 | R 文化、体育和娱乐业 | `R90` | 娱乐业 |
| 财政部和国家税务总局规定的其他行业（兜底） | — | **不落码** | 见下 |

直接可用：`{"op":"industry_exclusion","factKey":"industry","period":"assessment","excluded":["C16","H61","H62","F51","F52","K70","L71","L72","R90"]}`。**只给 `RD-001` 用**：这张表是「不适用研发费用加计扣除的行业」，对研发加计扣除条款是精确的；`SME-001` 不能借它判定，见口径 3。

三条口径必须一起看：

1. **兜底项不要写进 `excluded`。** 「财政部和国家税务总局规定的其他行业」没有固定码，写进去永远匹配不上任何真实行业码，条件会静默变成「永远不属于排除行业」——比不写更糟。兜底判断只能人工做，结论记在 `adjudication_note`。
2. **大类粒度表达不了中类/小类的排除**（如只排除「卷烟制造 C162」而不排除整个烟草制品业 C16）。需要细化时把 `excluded` 写细，运行时不用改；前缀匹配保证两种粒度能共存。
3. **小微条款的「国家非限制和禁止行业」与 119 号清单不是一个集合，用在这条上方向是反的——所以 `SME-001` 改用 `manual_review`。** 119 号第四条说的是「不适用**研发费用加计扣除**的行业」，它把住宿和餐饮、批发和零售、房地产、租赁和商务服务、娱乐都排除了；而小型微利企业的「国家非限制和禁止行业」指向《产业结构调整指导目录》的限制类/淘汰类——**上述服务业并不在其中**。把这张表挂到 `SME-001` 上，一家三个量化阈值都达标、实为小微的餐饮或批发企业会被判成「不满足」，是 fail-wrong 而不是 fail-safe。

   - `RD-001` 用这张表是**正确**的（那些行业确实不能加计扣除），不要因为这条口径把 `RD-001` 一起改掉；
   - `SME-001` 的行业条件写 `{"op":"manual_review","reason":"…"}`，理由里写明「非限制和禁止行业指向《产业结构调整指导目录》，无 GB/T 码」。引擎对它固定返回证据不足，`SME-001` 所在政策对任何企业都不会再输出「满足」——这是有意为之：判不了就说判不了，比给一个大概率错的结论强；
   - 由此 `sz-grant-*` 这类政策的「满足」演示案例要改用条件能自动判的政策（研发加计扣除 `RD-001`–`RD-003`、小微的三条量化条件本身），不要硬凑小微企业全条。答辩被问到时答「该条已声明须人工核查，系统给的是证据不足」。

**政策级一律按 AND 合并，`logic_group` 之类的分组列不参与求值**（契约与实现都不读它，2026-10-29 核实）。原文的「或」必须写进同一条 `or` 节点；把两个分支拆成两条并列条件，合起来就是「且」，政策对任何企业都不可能满足——M3 早期 SRDI 行里的 `revenue>=1000万` 与 `revenue<1000万` 并列即此类。为此 API-06 保存时做跨条款检查：同一条款内各自合法、合起来合取为空的组合，报一条**政策级 finding**（`criterionId: null`、`code: "POLICY_UNSATISFIABLE"`、`sourceSpans` 带两侧引用）；API-07 发布前若仍存在该 finding 返回 422 `CRITERIA_NOT_VALIDATED`，`details.findings` 里同样带 `criterionId: null`。检查只看顶层原子 `compare`：`or`/`not`/`quarterly_average` 内部的限制不参与，期间只在写法完全相同时合并（`assessment` 展开成哪一段要等运行时才知道）。报出来的是标注结构错，不是政策本身错。

`date` 算子的 `value` 必须是绝对日期 `YYYY-MM-DD`，**不支持「asOf 前 N 年」这类相对阈值**：判断期间会变，而落库的表达式不会跟着变，同一份快照重算会得出不同结论。相对年限请在标注时换算成绝对日期，并在引文里保留换算依据。这也正是「年限」不做成数值事实的原因——`specificMarketYears` 这类键存的年数会随年份漂移，`specificMarketSince` 存起始日期则不会。

`date` 算子接受的 `factKey` 是一个白名单（`rules.DATE_KEYS`，目前两个：成立日期与细分市场起始日期），不在白名单里的键报 `EXPRESSION_FACT_KEY`；反过来，日期类键写进 `compare` 报 `EXPRESSION_OP`。引文层对日期条件的核对会同时认两种证据：原文里的绝对日期，或**时长型**相对表述（「两个会计年度（含）以上」「满3年」「近10年」「不少于5年」）。期间词（「上年度」「本年度」）不算时长证据——它说的是「哪一段」，不是「多久」，拿它当日期依据会把无关引文也算成支持。

结构上限按**整棵表达式累计**：节点总数不超过 200、嵌套不超过 8 层，超限在入库前返回 `EXPRESSION_TOO_LARGE`/`EXPRESSION_TOO_DEEP`。只按单个 and/or 的直接子项数限制是无效的——每层各 200 个子项、嵌套下去展开是天文数字，所以计数在整棵树上做，且发现超限即停止遍历。

上表只定义程序口径，具体阈值、适用期间、非完整经营年度计算方式由 M3 提供原文并复核。首版不支持的期间返回证据不足并说明原因，不能把缺少季度当 0 或自行用现有季度替代全年。

### 2.3 FR-03：企业事实与参数字典

创建企业使用 `{nameSynthetic,period,facts:[FactInput]}`，地区和行业通过 `facts` 中的 `region/industry` 提交，不重复设置第二套顶层字段。两者在画像年度内必须各有一个非空值；其余必要事实可暂缺。后端按 `(key,period.startDate,period.endDate)` 唯一保存事实，重复项拒绝。

`FactInput={key,value,unit,period,source}`；响应 `EnterpriseFact` 增加服务端 `verified:boolean`。`source=user_input/synthetic_material`，未核实是 verified=false，不另造第三种来源。普通用户输入不接受自行设置 verified=true；合成材料复核由 M1 按 M3 审核数据导入。普通已知用户输入可以进行初步计算，报告说明未核实；条款明确要求证据核验而尚未核验时为证据不足。

| key | value 与 unit | period |
| --- | --- | --- |
| `region` | 行政区划码字符串，如 `440300`；`code` | 画像年度；本版试点深圳 |
| `industry` | 已冻结标注采用的行业码；`code` | 画像年度；显示名称由 UI 文案提供 |
| `establishedDate` | `YYYY-MM-DD`；`date` | 画像年度 |
| `taxableIncome` / `rdExpense` | 十进制字符串，金额最多 2 位小数；`CNY`；rdExpense 非负 | 自然年度 |
| `rdActivity` | JSON boolean；`boolean` | 自然年度 |
| `employeeCountStart` / `employeeCountEnd` | 非负十进制字符串；`person` | 自然季度起止日期 |
| `assetsStart` / `assetsEnd` | 非负十进制字符串，金额最多 2 位小数；`CNY` | 自然季度起止日期 |
| `revenue` / `equityFinancing` | 十进制字符串，金额最多 2 位小数；`CNY` | 自然年度（`equityFinancing` 为近两年新增股权融资合计） |
| `rdExpenseIncrease` | 十进制字符串，金额最多 2 位小数；`CNY`；**允许负数**（本年减上年的增量，下降时填负值） | 自然年度 |
| `rdExpenseRevenueRatio` | 非负十进制小数；`ratio`（3% 存 `0.03`） | 自然年度 |
| `rdAddDeductionClaimed` | JSON boolean；`boolean`；**`true` = 该期间内每个年度都已申报**；`value=null` 表示尚未申报或不确定 | 费用所属年度：期间写成哪一年就是哪一年的申报；单年度与「近两年」（两个年度合成的区间）都由 `period` 表达 |
| `creditRecord` | JSON boolean；`boolean`；**`true` = 诚信记录良好**（无失信惩戒、无超期未验收未退款等限制申请财政性资金的情形） | 核验时点：条件里写 `assessment`，事实存为本次判断所在年度区间那一行 |
| `specificMarketSince` | `YYYY-MM-DD`；`date`；**记的是「开始从事该细分市场之日」，不是年数** | 画像年度 |
| `independentLegalEntity` | JSON boolean；`boolean`；**`true` = 具有独立法人资格**（企业自述，需营业执照复核） | 画像年度 |

> **布尔语义必须与问法同向（2026-10-29 修）**：`creditRecord` 的真值是「良好」，补问文案也必须问「是否良好」；若问成「是否存在限制情形」，用户答「是」会被存成 `true`，规则按「记录良好」反向判断。`independentLegalEntity` 同理（true = 具有法人资格）。布尔键的 period 必须写成具体日期区间——「申报时点」「近两年」不是区间，事实表主键是 `(key, period_start, period_end)`，写不进具体日期就存不下来；`creditRecord` 例外，它按核验时点（本次判断所在年度）存取，见下条。

> **`rdAddDeductionClaimed` 的期间是「费用所属年度」，「尚未申报」不能记成 `false`（2026-10-31，应答 M2 的 P1）**：税务上「提交申报的日期」与「费用所属年度」是两件事——企业可在 7 月或 10 月预缴时申报当年加计扣除，也可在次年汇算清缴时申报上一年度（国家税务总局、财政部公告 2023 年第 11 号）。所以这个键存的是**费用所属年度**：条件要哪一年，就把 `period` 写成哪一年，不需要额外的「申报类型 / 申报日期」字段。反过来，`false` 的含义是「该年度确定没有申报享受」，与「申报期还没到」不是一回事——后者必须记 `value=null`（未知，求值得证据不足），否则一件还没发生的事会被写成否定事实，条件直接判 `not_satisfied`。补问文案因此是**三态**（是 / 否 / 尚未申报或不确定→跳过），`test_questioning.py::test_rd_add_deduction_question_keeps_not_yet_filed_out_of_no` 锁住这条。至于「当年度已申报」在某一年的具体受理口径（当年预缴还是上一年度汇算），只有主管部门能定，标注方须在 `adjudication_note` 里写明依据。

> **`specificMarketSince` 为什么是日期而不是年限（2026-10-29，契约 rc.4）**：原文写「从事该细分市场满 3 年」，年限是相对量——同一份企业快照在 2026 年算是 2 年、在 2030 年重算就是 6 年，判出不同结论且不可复现。存起始日期后，条件写成 `{"op":"date","factKey":"specificMarketSince","comparator":"lte","value":"2023-10-13"}`（按基准日倒推的**固定值**；基准日的选择本身要有依据——例如取通知落款日 2025-10-13 倒推两年），与 `establishedDate` 判「满两年」同一口径。它**不等于**成立年限：细分市场可能晚于企业成立。补问文案要写明「填日期、不填年数」，否则用户会照着原文回一个数字，而数字过不了日期校验。

> **`creditRecord` 是核验时点的状态，不是期间的均值（2026-10-29，应答 M2 的 P1）**：诚信记录可能在判断之后被撤销，而 `(key, period)` 一字未变，事实表按主键精确匹配，**查不出「期间内状态变过」**——这是当前设计的已知边界，靠流程补：提交前必须重新核验并 PATCH 该事实；PATCH 会生成新的 `factVersion`、写一条失效事件，让原 run 变 `stale`（`staleReasons[].code = fact_changed`，`changedFactRefs` 指向 `creditRecord`），页面据此提示重算。因此「当前有效」的含义是**事实自判断后未被改动**，不等于「这一期间内状态恒定」。演示时用这一步说明有效期：PATCH 撤销记录 → 原 run 变 stale。`test_db_evaluation.py::test_revoking_a_credit_record_makes_the_old_run_stale` 锁住这条链路。若日后确实需要按时点回溯，才考虑给事实加 `verifiedAt` 并改精确匹配口径——那会破坏现有 `(key, period)` 主键语义，本期不做。

> **2026-10-28（契约 `1.0.0-rc.3`）**：事实字典由 10 键扩到 16 键，`FactUnit` 补 `ratio`，用于地方通知的研发投入补助/专精特新条款。**增量与占比按「预计算事实」处理**——由标注方给出算好的数值，规则引擎只做 `compare`，不新增 `delta`/`ratio` 算子。三处口径必须同步：`contracts/openapi.yaml` 的 `FactKey`、`app/schemas.py` 的 `FactKey` 字面量、`app/domain/rules.py` 的 `FACT_UNITS`；库里由 `infra/sql/005_fact_dictionary_extension.sql` 重建 `enterprise_fact_key_check`、`enterprise_fact_unit_check`、`enterprise_fact_key_unit` 三个约束。补问档位（§2.5）里这 6 个键的取值是 M1 提案，待 M3 在《条款标注规范》§5 逐键确认。画像归一化（`app/agents/profile.py`）另有三处口径要跟：`MONEY_KEYS`（新增的三个 `CNY` 键必须计入两位小数校验，否则会收三位小数、而序列化又按两位补齐，读回值与写入值不一致）、`SIGNED_KEYS`（`rdExpenseIncrease` 允许负值）、`domain/citations.py` 的 `_METRIC_FAMILY_BY_KEY`（新增金额键要参与引文数值核对；`rdExpenseRevenueRatio` 不在这张表里，它走百分比换算的 `ratio_bounds`，**同样要核对**，口径见下一条）。

> **2026-10-29（契约 `1.0.0-rc.4`）**：事实字典由 16 键扩到 18 键，新增 `specificMarketSince`（`date`）与 `independentLegalEntity`（`boolean`），专精特新的细分市场年限与法人资格条款要用。**只放宽枚举**：`FactUnit` 不变，`date` 算子由「只认 `establishedDate`」改为认 `rules.DATE_KEYS`。三处口径照旧要同步（`contracts/openapi.yaml` 的 `FactKey`、`app/schemas.py` 的字面量、`app/domain/rules.py` 的 `FACT_UNITS`），另有第四处容易漏——`app/agents/extraction.py` 的模型提示词键白名单：漏写在库里不会报错，表现成「模型抽到了却存不进去」。`test_openapi_alignment.py` 现在直接断言契约枚举、字面量与 `FACT_UNITS` 三者相等。库里由 `infra/sql/006_fact_dictionary_extension_rc4.sql` 重建 `enterprise_fact_key_check`（18 键）与 `enterprise_fact_key_unit`（`specificMarketSince` 配 `date`、`independentLegalEntity` 配 `boolean`）。画像归一化：`specificMarketSince` 进 `profile.DATE_KEYS` 与 `PROFILE_SCOPED_KEYS`（与 `establishedDate` 同口径），`independentLegalEntity` 进 `rules.BOOLEAN_KEYS`（只收 JSON 布尔）。补问档位：两者成本都是 1（翻营业执照即可），敏感度分别是 2（经营范围可能透露业务方向）与 1。

> **2026-10-31（契约 `1.0.0-rc.5`）**：新增 `manual_review` 算子，把「这条按政策口径本就不由程序判」写进数据模型——此前标注方只能在「硬写一个大概率错的表达式」和「退回待核实、整版政策发不出去（`CRITERIA_NOT_VALIDATED`）」之间二选一。求值固定 `insufficient_evidence`、reason 原样回显；落在 AND 里让整体降级，落在 OR 里不影响另一支；引文仍逐字校验；发布闸门照常放行。抽取智能体不得生成，只有标注方能写。同时修正 rc.4 的 `DateExpression.factKey`：schema 当时仍写死 `establishedDate`，与描述和服务端的 `rules.DATE_KEYS` 不一致，照 schema 写 `specificMarketSince` 的日期条件会被拒——rc.5 改成 `enum` 两个日期键，`test_openapi_alignment.py` 增加算子集合与日期键集合的对齐断言，两边只改一处会直接转红。**`apps/web/src/api/types.ts` 仍由 M2 生成**，需按 rc.5 重新生成一次（含新增 schema 与版本号）；`FactKey` / `FactUnit` 本次未变，不需要新迁移。

> **2026-11-03（契约 `1.0.0-rc.6`，应答 M2 的 issue #27）**：轨迹步骤的 `evidenceRefs` 改名为 `objectRefs`，元素类型由 `EvidenceRef`（政策原文引文）换成新的 `TraceObjectRef={type,id}`（`enterprise`/`policy`/`policyVersion`）。根因是同一个 `EvidenceRef` 被两类结构共用：轨迹只记「这一步动了哪个内部对象」，服务端从未在轨迹里写过引文；可回溯的原文引文一直在 `CriterionResult.evidenceRefs`（报告页也用它）。按旧契约校验的前端会把每条带 `{type,id}` 的步骤判成「契约错误」，整页轨迹不展示。服务端 `TraceStep` 的字段同时由 `list[dict]` 收紧为 `list[TraceObjectRef]`，未知 `type` 或非 UUID 的 `id` 直接报错，不再把任意结构透传给前端。**`trace_step.evidence_refs` 列名保留不改**（不需要新迁移）：语义上它一直存的是对象引用，`get_trace` 正是靠其中的 `policyVersion` 把入库/审核轨迹挂到 run 上。`test_report_trace_http.py` 增加断言：引用只含 `{type,id}` 且 `type` 在枚举内、响应体不含 `quote`/`textHash`。`apps/web/src/api/types.ts` 仍需 M2 按 rc.6 重新生成一次。

> **2026-11-04（`extraction` 轨迹步骤与模型用量记录）**：契约九阶段里的 `extraction` 此前没有写入点，现由抽取路径补齐（决策见 PR #33，方案 A）。写入点在 `PolicyRepository.save_criteria`：抽取成功后与条款、校验发现在**同一事务**内、`citation_validation` 步骤**之前**写一条 `stage="extraction"` 的轨迹，`objectRefs` 只含 `{type:"policyVersion", id}`，摘要为「抽取正文 N 字」/「得到条件 K 条、校验发现 M 条」，有 finding 即 `status=needs_review`，`elapsedMs` 为调用耗时。`modelOrRuleVersion` 记真实模型名（`MODEL_MODE=deepseek` 时取响应里的 `model`），规则抽取时记 `rule:extraction@1`（或请求体的 `modelVersion`）。三条边界：**幂等重放不写第二遍**（重放在任何落库之前就返回原响应，`test_extract_is_idempotent_for_the_same_key_and_body` 断言只落一次）、**手工保存 API-06 不写抽取步骤**（不传 `extraction_model`）、**保存冲突先于写库**（`_assert_editable` 在第一次写入前返回 409，事务里不留半条步骤）。契约结构不变，`TraceStep` 仍是 `additionalProperties: false`。
>
> 用量不进轨迹：`TraceStep` 没有 token 字段，本轮**不**给 `TraceStep` 加字段（契约里带 tokens 的只有 `ModelUsage`，rc.7 给它加了 `tokensUnknown` 用来表达「未知」，见第 6 条）。抽取的 tokens 走 `ExtractionResult.usage`（接口原本就返回）与一行日志；解释调用的 tokens 汇总进 `explanation` 步骤的 `outputSummary`（前缀「模型 tokens 入 N 出 M」；`MAX_SUMMARY=200` 会截断，多政策只记总数，不逐条列）。日志格式固定为 `model_call purpose=<extraction|explanation> outcome=<ok|error> usage=<known|unknown|none> model=<名称> prompt=<n|unknown|0> completion=<n|unknown|0> elapsed_ms=<ms|unknown> policy_id=<id> version=<版本>`（失败行再带 `code=<错误码>`），只记对象标识与用量，不记提示词、正文与密钥；`fixture`、规则抽取与幂等重放不记行，以免把零开销的路径算成模型调用。费用折算口径见 `infra/README.md` §4.3。
>
> **失败留痕与用量三态（2026-11-04 补，按 M2 复审）：**
>
> 1. **失败也记一行**。调用失败时 `outcome=error`；`usage=unknown` 表示上游是否计费无法从客户端确认（超时、连接中断、5xx、2xx 但解析失败，**以及 429 与上游 4xx**——2026-11-05 改判，状态码本身证明不了上游没处理这次请求），`usage=none` 只用于确认没发起调用的路径。未知**单列**，不并入 0 参与费用合计。成功但回答为空（解释路径）仍算调用成功，tokens 照记，只把措辞退回已存说明。
> 2. **失败步骤写独立事务**。`PolicyRepository.record_extraction_failure` 只挂既有 `policyVersion`（`objectRefs` 与成功步骤同构），不改草稿、不动 revision，摘要是「模型抽取失败」→「错误码 …；上游是否计费无法确认，用量按未知记录 / 未发起模型调用，没有用量」，`status` 按可重试性取 `retryable_error`（429、超时/连接、5xx、坏响应）或 `fatal_error`（上游 4xx，重试不会自愈）。可重试性与是否可能计费是两件事，fatal 的调用同样记未知。写失败步骤自身出错只记告警，**不替换**原来的 429/503，响应形状不变。两个判断标记挂在 `ApiProblem` 上，不进响应体。
> 3. **调用记录独立于草稿保存事务**。模型成功但 `save_criteria` 撞 409 时，草稿与成功步骤一起回滚，而**调用返回并完成日志写入之后**那行 `model_call` 仍在、可核对；不补记失败步骤（调用本身没失败）。这句只覆盖「调用已返回、日志已写出」之后发生的中断——请求发出后、返回前进程被杀的情形见第 7 条，那时可能一行都没有。
> 4. **API-14 可见性边界**。API-14 从分析 run 按政策版本关联入库轨迹：草稿未发布、没有关联 run 时失败步骤不保证立即可见；发布后读到的历史失败也不代表当前在线分析失败。
> 5. **日志管线**。uvicorn 的默认日志配置不管 `app.*`/`huice.*`（root 停在 WARNING、无 handler），应用侧 `logger.info` 会静默丢弃；`app/logging_setup.py` 在 `create_app` 时给这两个命名空间挂 stderr handler 并设为 INFO，systemd 收集到 journald。别用 `caplog.set_level` 的通过当作部署可见性的证据。
> 6. **成功但缺用量（2026-11-05 补，2026-11-05 晚按 M2 的 P1 改为契约表达）**。上游 2xx 却没给 `usage`、只给一半、或数字格式不可解析时，`LlmResult.usage_known=False`，tokens 只是占位 0：日志写 `usage=unknown prompt=unknown completion=unknown`，解释摘要计入「另有 K 次调用用量未知」而不并入已知合计，抽取路径同样按未知记。**响应侧不再让消费者猜**：契约 rc.7 给 `ModelUsage` 加了可选 `tokensUnknown`，这种情形返回 `tokensUnknown: true`（rc.7 自己产生的未知一律 `0/0`；重放 rc.6 历史记录时数值原样保留，可能是 `120/0` 这样的一半），消费者必须先看这个标记再决定能不能按 tokens 计费；`fixture`、规则抽取这类**没发起调用**的路径仍是真实的 0，不带标记。**服务端在已知用量时省略该字段**（`Field(exclude_if=...)`），所以常路径的响应与 rc.6 逐字段一致，按 rc.6 严格校验的旧消费者照常通过；只有「未知」这一异常会多出 `tokensUnknown: true`，旧严格消费者在那里会拒绝——这是有意的 fail-closed，比让它把占位 0 当真实用量好。因此**不能按纯加法变更宣传**：这是带行为语义的迁移，而且**不能后端先上**——现有前端允许额外字段、审核页直接显示 `usage.inputTokens/outputTokens`，后端先上会把「未知」安静地显示成「0 / 0」。顺序是前端先能表达「未知」（生成类型 + 展示与统计先判 `tokensUnknown`），后端再上 rc.7。**「没有该字段」不等于「已知」**：rc.6 后端在上游缺 `usage` 时同样用 0 兜底，字段缺失只说明服务器没给状态；前端在 rc.6 窗口与回滚期间应显示「旧版未提供用量状态」并暂停这部分合计，不能当作已确认的 0。回滚还会连带失去 `model_call` 日志与轨迹摘要（两者来自本 PR，rc.6 没有），已写进 `idempotency_record.response` 的 `tokensUnknown` 因只追加触发器无法清理，同键同版本重放在 rc.6 上会解析失败（500）。**历史重放两个方向都要看住**：rc.6 对两个计数是**各自** `or 0` 兜底的，`120/0`（只给了 prompt）、`0/45`（只给了 completion）、`0/0`（两个都缺）都常见，连 `120/45` 也不能凭非零认定完整——所以判据不是数值，是**记录来源**。迁移 `infra/sql/007_idempotency_usage_semantics.sql` 给 `idempotency_record` 加 `usage_semantics` 列（`'explicit'` = rc.7 起写入，未知一定带标记；`'legacy'` = 此前写入，含回退到 rc.6 期间写入的，`DEFAULT` 即 `'legacy'`），响应本体不动。重放时：`explicit` 原样返回（无标记即已知）；`legacy` 且 `mode=deepseek` 一律补 `tokensUnknown: true`、数值原样保留（可能是 `120/0` 这样的一半），`fixture`/`saved` 的真实 0 不补标；只改本次响应视图、不写回只追加表。反过来 rc.7 的**未知用量**记录被 rc.6 重放则 500，已知用量的记录形状与 rc.6 一致、照常可重放。不要用「换一个 Idempotency-Key 重来」当默认处置——那会重新发起抽取与模型调用，可能重复计费，先查 `idempotency_record`、轨迹与日志确认这次调用是否已发生。完整约定见 `contracts/README.md` 第 11 条。
> **M2 前端接入（2026-11-06）**：审核页校验 `tokensUnknown` 的类型；为兼容 rc.6、回滚及历史重放，`deepseek` 响应缺少该字段时显示「用量状态未确认」，即使数值非零也不把它当作已确认用量。字段为 `true` 时显示「用量未知」，不展示占位数值；显式 `false` 才展示已知模型用量。`fixture` / `saved` 且无未知标记时可展示零。当前界面没有用量合计。此保守处理也适用于 rc.7 已知用量省略标记的响应；要精确展示需另行对齐可区分已知及旧响应的契约。
>
> 7. **记录覆盖的边界（2026-11-05 补）**。日志与失败步骤都在调用返回或抛错**之后**才写。请求已发出而进程在返回前被杀（OOM、强杀、掉电）时，这次调用可能完全无记录。覆盖它需要调用前持久化「开始记录」并事后对账，本轮不做；对账时把「日志缺失」与「确认未调用」分开看，不要用「事务回滚兜住了」下结论。

> **比例条件的引文核对（2026-10-29，应答 M2 PR #10 的 P1）**：`rdExpenseRevenueRatio` 不再「跳过数值核对」，改为单独走 `citations.ratio_bounds`——把原文的百分比换算成小数（「不低于 3%」→ `0.03`、`gte`，「5%以下」→ `0.05`、`lte`，`‰` 与「千分之」按 1000 除），再与 AST 的 `compare` 比数值和方向。与金额阈值分开是因为换算基准不同：混在一起会把「3%」按「万元」换算成 300，让写成 `0.30` 的条件反而通过。识别只认三种写法（前置方向词 + `百分之/千分之`、前置方向词 + `%`、数字 + `%` + 后端词），且**仅在百分比前 24 字内出现「比重/占比/比例/百分比/占」时才认定为比例条件**——原文没有这类线索时报 `CITATION_THRESHOLD_NOT_FOUND`，挡住发布并要求人工复核，而不是拿别处的百分比（如「研发费用增长不低于 10%」）凑数。已知边界：中文数字（「百分之三」）不解析，会落到「找不到阈值」，同样是挡住而非放行。

所有事实 `value=null` 均表示未知；省略事实等价于尚未提供。空字符串、字符串 `"null"`、NaN 均拒绝。数值的通用词法允许最多 6 位小数，按上述字段进一步校验；`"0"` 是有效已知值。`period={startDate,endDate}` 使用日期，包含两端；开始不能晚于结束。同一 key 不同年度是不同事实，不互相覆盖。

编辑接口 API-10 的路径仅传 key，**目标期间在 body.period 中确定**，完整提交 value/unit/period/source；null 用于清除值，不物理删除历史记录。每次成功变更生成 `factVersion`（完整画像规范化快照的 SHA-256；数组按 key/期间排序，Decimal 规范化），相同内容不产生新版本。不从前端接受哈希。ETag 采用带双引号的 factVersion，前端必须保留原样。

### 2.4 FR-04：候选与确定性判断

1. `OpportunitiesPage` 调 API-02 获取已审核候选。地区包含国家级与深圳；允许列出行业相关但不满足的政策用于解释，检索相关性不代表资格。类型/状态/日期用于检索，不能以相似度绕过硬门槛。
2. API-11 校验企业、1—7 个不重复政策 ID、`asOf` 和 `period`；固定企业快照及每项政策的最新已发布版本。`asOf` 是带时区时间，判断政策日期时换算为北京时间；不凭 asOf 自动选任意历史版本。本版重放依据通过旧 run 读取，重新计算使用当前已发布规则。
3. `orchestrator → rules.evaluate_policy(policySnapshot, enterpriseSnapshot, period, asOf)`。只读取引用校验通过且已人工审核的数据。未审政策整体返回 422，不在一次运行内悄悄跳过。
4. 条件级先检测已审核的冲突，再执行硬门槛和 AST。政策级默认 AND：存在冲突为 conflict；否则任一条件不满足为 not_satisfied；否则存在必要事实未知为 insufficient_evidence；全部满足才 satisfied。OR 分支中无关的缺失事实不应阻止已经成立的有效分支。缺失事实只报「仍挡住结论」的那些：条件级与政策级一样，结论一旦定下（satisfied / not_satisfied / conflict）就不再带缺失事实，只有 insufficient_evidence 才给出，且只来自尚未定性的子条件——`or` 里已成立那一支之外的、`and` 里已判不成立那一支之外的缺失都不计入。
5. 关闭政策可用于历史演示，不得伪装成开放机会：以 asOf 求四态结果，同时以读取时刻判断 `validity`；已关闭/已过期明确标 stale，显示原因并隐藏申报动作。晚于有效期的当期评估还应有时间门槛不满足。
6. 创建不可变 run，保存规则版本、政策快照、事实快照、依赖、确定性解释与来源，再允许模型润色。缓存命中也分配新 runId，并注明复用来源；不覆盖旧 run。

### 2.5 FR-05：补问与局部重算

`questioning.choose_next_question(assessments, skippedFactRefs)` 对 unresolved 的“key+period”去重，按“受影响的待定政策数降序、获取成本升序、敏感程度升序、key/日期字典序”确定稳定顺序。成本/敏感度由 M3 在标注约定中给整数档位，不由模型猜测。函数返回当前全局第一问（rank=1）或 null；仅把同一问题放入受该 FactRef 影响的 policy.nextQuestion，其他政策为 null。前端按 FactRef 去重后一次显示一问，不自行重新评分；完整排序理由写脱敏 trace 供验收比较。

> **补问文案只引用事实期间，不写相对年份（2026-10-29 修）**：`domain/questioning.PROMPTS` 按 key 静态生成，函数本身只知道 `factRef` 的 `periodStart/periodEnd`，不知道「现在是哪一年」。文案若写成「本年度较上年度」，而本次期间实际是「上年度」那一行，用户就会照字面填「本年 − 上年」，把量级差一年的数记进上年度；错年份的值仍是合法十进制数，精度/符号/范围校验全过，判错也不会报错（fail-wrong，不是 fail-safe）。因此所有文案统一写「该期间」，增量类另写明公式（`rdExpenseIncrease`：＝该期间的研发费用 − 上一期间的研发费用，下降填负数），使 M3 预计算的值与用户自己算的值一致。`test_questioning.py` 有守卫用例，文案出现「本年度/上年度/前年度/近两年/本次判断/该年度/该季度」即失败。

`NextQuestion` 回调只给输入值或 skip：回答后调用 `PATCH fact → 收到新 Enterprise → POST assessment-runs`；重算 body 传 `supersedesRunId`。后端验证旧 run 属于同一企业、同一组政策和期间，按依赖图仅重算受改动影响的条件，其他条件可复用；仍生成新 ID。政策版本变更则重算对应政策。补问计数按实际成功更新/明确跳过记录，不能按渲染次数计数。

跳过不调用 PATCH，前端本次页面会话保存已跳过的 FactRef；下次重算可传 `skippedFactRefs` 供排序排除，事实仍未知，结果不得改成不满足。不增加回答/跳过专用端点。

### 2.6 FR-06/08：准备清单、解释和报告

每项结果带 `preparation={type,handlingMode,officialEntryUrl,deadline,items[]}`；item 含 `materialId,title,description,requiredFactRefs,missing,status`（契约 `1.0.0-rc.8` 起；`status` 取值自 rc.9 起为 `facts_filled/facts_missing/manual_check/policy_closed`）。税收 handlingMode=`tax_self_assessment`，资金=`grant_application`；真实具体办理说明来自已审核材料要求，模型只改写文字。类型不同必须分栏；关闭窗口不能有立即申报动作。

**清单条目从哪来（2026-11-06 实现）。** 材料要求由 M3 的 `data/policies/material_requirements.csv` 提供，导入器 `python -m app.materials import` 只收 `review_status=已仲裁` 的行（TAX-04、GRANT-09 仍是「待核实（依据未入库）」，不入库），按 `source_register.csv` 的文号把业务代号解析成库里的政策版本，写进 `material_requirement` / `material_requirement_policy`（迁移 `008`）。判断链路只读这份表：

- **适不适用**：必交材料无条件列出；条件交材料只在本次判断真的引用到它证明的事实键时列出。没有事实键的条件交（申请书一类，条件只有文字）判断不了，按「需人工核对」列出而不是省略——静默消失会让人以为不用交。
- **要哪些事实**：按本次判断期间展开；季度类键（`assetsStart/assetsEnd`、`employeeCountStart/employeeCountEnd`）展开成四个季度的期初期末共八个引用，与 `rules.referenced_facts` 同一套口径，不另写一份。
- **四态 `status`（事实口径，rc.9 起）**：`facts_filled`（要的事实都有取值）、`facts_missing`（有事实键但缺值）、`manual_check`（没有事实键，或条件无法由程序判断）、`policy_closed`（政策已到期或窗口已关闭，材料再齐也交不了）。**名字说的是程序查到什么，不是材料交没交**：服务端只读企业事实，从不读材料本身，所以 `facts_filled` 不等于「材料已提交/已核验」。`missing` 是保守口径：`manual_check` 也算 `true`，展示以 `status` 为准。rc.8 里这两个取值叫 `provided` / `missing`，因名字比能证明的多说了一层，在 rc.9 改名（M2 在 #40 的 P1）。标题前缀 `【必交】`/`【条件交】` 承载必交属性（契约没有独立字段，口径见 `docs/product/MATERIAL_REQUIREMENTS.md` §4），条件文案由 `domain/preparation.py` 的 `_display_description()` 追加在 `description` 末尾（`f"{material.description}（何时需要：{material.condition_desc}）"`，`condition_desc` 为空时原样返回）。
- 库里没有材料（政策没挂材料、或清单尚未导入）时 `items` 为空并保留告警「申报材料清单待 M3 审核材料导入后补齐」；有清单时告警消失，报告逐条标出四态。
- `PolicyDetail.preparationTemplate.items` 仍是 `[]`：模板没有企业与判断上下文，给不出 `status`；带四态的清单只出现在判断运行里。

结果的 `explanationMode=deepseek/saved/fixture`，`warnings[]` 说明解释降级/样例模式。模型失败时规则结果照常返回，使用已存确定性说明；不能把整次判断伪装成模型成功。抽取端模型不可用仍返回 503 并保留草稿。

`ReportPage` 先读 API-12 的 JSON 展示，再通过 API-13 下载 UTF-8 Markdown；不能把下载内容当 JSON 解析。报告含原 run 的生成时间、企业/政策/规则版本、条件与证据快照、两类清单、风险文案；每次下载前追加“截至 downloadedAt 的有效性提示”，正文历史依据不变。旧文件脱离系统后不会自动更新，页首提示再次申报前返回系统核验。

### 2.7 FR-07：失效、并发和缓存

`storage` 在同一数据库事务内提交事实/政策发布变更与失效事件。草稿修改不影响现有结论；发布新版本使依赖该 policyId 旧版本的 assessment 失效。事实改动按 key+period 查依赖，仅影响相关条件/政策；没有读到该事实的政策保持 current，不按整企业粗暴失效。run 保存完整 factVersion，条件另存依赖快照，以便识别这种差别。

写入与读取两端的匹配口径必须一致：事件除了 `fact_key` 还落 `fact_period_start/fact_period_end`（迁移 `004`），读取时按 **key + 期间** 精确比对。同键不同期间（年度与季度、前后两个年度）各是一条事实，改动其中一条只让读那一条的条件变 stale，`changedFactRefs` 也不列没被改的期间。`004` 之前写入的事件没有期间列，读取时按整键保守匹配——宁可多标 stale，不可漏标；`invalidation_event` 只追加、无法回填，所以这类历史事件所在 run 会**永久**保持整键匹配（多标，不会漏标）。云端库当前 0 个 run、0 条事件，不存在这类历史行。

历史判断正文与旧事实/政策快照不可变；`validity,staleReasons` 是按变更事件和当前时间生成的读取视图，不回写四态结果。`staleReasons` 包括 `policy_changed/fact_changed/window_closed/policy_expired/valid_until_passed`，附 changedCriterionIds/FactRefs 和中文原因。GET run、报告与缓存命中都重新计算该视图；只换 Redis 键不能代替数据库失效记录。

`validUntil` 是现有已知条件下的最早未来失效时刻，可取适用期结束次日零点、窗口关闭等边界的最小值；没有可靠边界时 null。政策/事实变化仍可提前使其失效，不把政策结束日期无条件复制为结论有效期。已过边界的当前读取标 stale。

缓存键包含 `enterpriseId,policyId,policyVersion,factVersion,ruleVersion,period,asOf`；不漏判断时点、期间和规则版本。不把一个时间的结果套给另一个时间。Redis 不可用可绕过后重算，数据库不可用返回 503。

`integrations/cache.py` 按上述口径实现：键是七字段规范化 JSON 的 SHA-256（前缀 `huice:eval:v1:`，`asOf` 统一到 UTC 微秒、无时区按 UTC 解释）；缓存值只投影政策级四态、条件级结果/理由/事实引用/季度计算与依赖，不含 runId、`validity/staleReasons/validUntil`、解释文本和条件 UUID——恢复时按**当前政策版本**的条件顺序回填 `criterionId`，数量、版本或「条件级 → 政策级」聚合结论任一不自洽即判未命中并重算。未配置 `REDIS_URL` 用直通替身；配了即用 `redis` 8.1.0（已入 `requirements.lock`）真实读写，缺客户端、连接/读写报错或值损坏一律记一次告警后绕过，不向请求抛错；数据库故障仍由仓储抛 503，不会被缓存掩盖。

所有写请求以数据库中的当前版本执行 compare-and-swap；仅先查询再无条件更新会丢失并发修改。ETag 使用强标签，不接受 `*` 或弱 ETag；缺 If-Match 返回 428，版本不一致沿用旧计划返回 409，禁止前端自动覆盖重试。

### 2.8 FR-09：智能体与轨迹

入库链：管理路由 → ingestion → extraction → citations → 人工审核。在线链：assessment 路由 → orchestrator → profile 快照 → 已审规则检索 → rules → questioning → explanation → trace/storage。四个角色可共享同一 Python 进程与 DeepSeek 客户端。

`AgentContext` 只有上下文（trace/request ID、asOf、period、版本图、预算）；业务参数由独立 `AssessmentInput` 传入，不能把 enterpriseId/policyIds 塞进未定义的 context 字段。内部 snake_case 转外部 camelCase 只在 schema 边界做。

`TraceStep` 包含 `sequence,stage,status,inputSummary,outputSummary,objectRefs,policyId,policyVersion,modelOrRuleVersion,elapsedMs,errorCode,humanAction`；`objectRefs` 是 `{type,id}` 形式的内部对象引用（`enterprise`/`policy`/`policyVersion`），**不是引文**——可回溯的政策原文引用在 `CriterionResult.evidenceRefs`，两者不共用字段。多政策公共步骤的 policyId/version 可 null。状态仅 `ok/needs_review/retryable_error/fatal_error`。线上 run 应链接所用政策版本的入库、审核轨迹，再列在线轨迹；不能只保留模型最后一句话。summary 不得含密钥、完整提示词或企业资料。

### 2.9 评测输入快照（`EVALUATION_FREEZE` §5.2）

`app/evaluation/` 只做「把库里的东西说清楚」，不产出任何评测指标。四份 CSV 的哈希只能识别文件，证明不了它们就是导入进库的那一份，所以映射从**数据库本身**导出：

```bash
# 四份输入本来就不在一个目录：标注在 data/evaluation、条件与登记表在 data/policies、企业在 data/fixtures
python -m app.evaluation inputs --out snapshot.json \
    --dataset-file data/evaluation/labels.csv \
    --dataset-file data/policies/criteria_review.csv \
    --dataset-file data/policies/source_register.csv \
    --dataset-file data/fixtures/enterprises.csv
# 核对时不再点名文件：签认清单记着路径，自己会去读那一份
python -m app.evaluation verify snapshot.json
```

数据集只认真实要冻结的那几份：`--dataset-file` 可重复、相对路径按当前工作目录（或 `--dataset-dir`）解析，**不按目录扫全部 CSV**（M3 复核）——`material_review_sheet.csv`、`*_annotation_sheet.csv` 与评测输入同目录，但材料复核一改就会让 `verify` 误报。`codeCommit` 仍在清单里，但它只是**注记**：云端没有 `.git`，那个值只能手打，既不能证明那台机器上跑的就是那个提交，两侧都 `null` 时更是什么都没证明。代码身份改由下面的部署文件指纹回答。

**清单记签认路径，`verify` 按签认路径读（M2 在 #39 三审的 P1）**：原先每份输入只记文件名的哈希，`verify` 再拿文件名去拼 `--dataset-dir`——三份输入分散在三个目录时，「从仓库根用三个相对路径签认、在别处核对」会去找根目录下的 `labels.csv`，某处存在同名文件还会把它顶替成签认对象。现在 `dataset` 记的是 `[{"path": …, "sha256": …}]`：项目内的文件记**相对项目根的 POSIX 路径**（`data/evaluation/labels.csv`），项目外才记绝对路径；`verify` 一律按这些路径读，**不接受** `--dataset-file`/`--dataset-dir`（传了就报错：「核对哪些文件由签认清单决定……后传的文件不能顶替签认的对象」）。两种歧义直接拒：同一个文件被签认两次；两个不同目录的同名文件一起签（映射按文件名区分四份输入，后者会覆盖前者）。

**部署文件指纹（`codeFingerprint`，M2 在 #39 同意、要求单独 PR 落地）**：清单顶层记 `codeFingerprint`/`codeFiles`/`codeScope`——对**部署机上实际存在的代码文件**算一遍规范化哈希（`app/evaluation/code_fingerprint.py`）。范围固定为 `services/api/app/**/*.py`、`contracts/openapi.yaml`、`services/api/requirements.lock`，排除 `__pycache__` 与 `*.pyc`；每项取 `(相对项目根的 POSIX 路径, 文件字节 sha256)`，按路径排序后用与清单其他哈希相同的 `canonical_json`/`sha256_hex` 再取一次 SHA-256。**缺文件、空集合一律报错**，不允许「扫到 0 个文件」算出一个稳定值——那等于什么都没证明，却长得像证明过。`python -m app.evaluation fingerprint`（`--json` 出逐文件明细，`--root` 指定另一棵树）不连库，任何人把某个提交 checkout 出来复算就能把「提交 ↔ 指纹」对上。

三件事分开证明，谁也不冒充谁：**磁盘上的代码是什么**由指纹回答；**这份代码是不是某个提交**要 checkout 复算；**进程里跑的是不是它**指纹答不了。第三条只能交叉核对：`inputs --process-pid` 记下服务进程启动时间（读 `/proc` 的第 22 字段与 `btime`，不用 `ps` 文本），并把**写于进程启动之后**的代码文件全部列出；有这种文件就直接停下并请先重启（盘上新的、进程里大概率是旧的）。这个参数**只属于 `inputs`**：`verify` 不接受它，也不比对 `processStartedAt`——签认后重启是正常操作，比它会让每次核对都失败；因此不能用 `verify --process-pid` 证明进程身份（2026-11-11 按 M2 复审移除，此前 `verify` 收下该参数却从不读它）。这个 mtime 比对只是**辅助证据**：时间戳可以被保留或改写（`cp -p`、解包、还原备份都会），所以「没有更新的文件」不等于「进程里跑的就是盘上这份」，进程身份要另行核实。

**指纹算原始字节，不做换行归一化（M2 在 #42 的判断）**：`git archive` 在 `core.autocrlf=true` 的 Windows 上会把文本转成 CRLF，同一个提交在 Windows 检出与 Linux 部署上字节不同、指纹也就不同——这是**有意保留**的，因为 `.sh` 的换行会直接改变执行行为，归一化等于替核对方决定「这点差异不算」。跨系统核对时用 `git -c core.autocrlf=false archive` 导出同一份 LF 内容再算，并在记录里写明用的是哪种导出方式；将来若确有跨平台语义等价比较的需求，另加一个单独命名、版本化的归一化指纹，不改 `codeFingerprint` 的含义。

**证明范围只有 `CODE_SCOPE` 那三项**（`services/api/app`、`contracts/openapi.yaml`、`services/api/requirements.lock`）：**不覆盖**迁移 SQL、部署脚本、静态资源（前端构建产物）、已安装依赖与环境变量。因此指纹一致只说明「这三处的代码字节一致」，**不能**据此声称「整棵部署树等于某个提交」或「运行进程等于某个提交」；正式冻结仍以 #36 的独立标注、映射、输入与结果证据为准。

`verify` 对代码身份只说到这里：指纹一致时打印「磁盘代码与签认一致（指纹 …，N 个文件）；但『这份代码对应哪个提交』与『进程里跑的是不是它』仍未证明」，签认时没记启动时间的再加一句「两者之间没有交叉证据」。**不会**因为指纹一致就说成「运行代码身份已证明」（M2 在 #39 的原话：`codeCommit=null` 不应只因磁盘指纹一致就笼统改成已证明）。清单没有 `codeFingerprint`（2026-11-06 之前签认的）时仍可核对库与数据集，但要明说「磁盘代码身份未证明」，并提示用当前版本重新签认；`manifestVersion` 仍是 `3`，这四个字段是可选增补，旧清单不因缺它而报「版本不同」。

**正式核对要求四份齐全**（`labels.csv`、`criteria_review.csv`、`enterprises.csv`、`source_register.csv`）：少给一份直接非零退出并点名缺的那份，不许「对上三份就算过」。确实只想核对数据库时单独加 `--no-dataset`——这样写出的清单 `datasetMapping` 为 `null`，`verify` 必须带同一个开关，成功时明写「一致（仅数据库）……此结果不能当作数据集已签认」；反方向也拦：带数据集映射的清单加 `--no-dataset` 核对会被拒，两个模式不能互相冒充。`--no-dataset` 与 `--dataset-file`/`--dataset-dir` 同时给也直接报错。

**内容对齐映射（`datasetMapping`，M2 在 #39 复核；#51 复审后收紧为 `content-alignment@3`）**：整文件哈希只证明「文件没变」，证明不了「这些文件就是导入进库的那一份」——两套互不相关、但各自稳定的 CSV 照样能过。所以 `inputs` 还把 CSV 的行与库行**逐条**对上，结果写进清单的 `datasetMapping`（实现在 `app/evaluation/dataset_map.py`）：

- **政策归属只认登记表**：`source_register.csv` 的 `policy_id → 版本` 是唯一依据。`criteria_review.csv` 每行先按 `policy_id` 找登记行，再用 `source_version` 在**同一代号**的多个版本里挑（`CN-TAX-GXJS` 的 2008/2016 两版）；`source_version` 记的若是登记表之外的文件（《财税〔2015〕119号》、申报通知），归属仍由 `policy_id` 决定，但条件必须在那一版里逐条对上。登记行再与库做三方核对：登记表的 `version`/`title` 必须分别等于库里的 `document_number`/`title`，对不上即「库里那份不是按这份登记表导入的」。**表达式相同不再能单独证明归属**——另一份政策恰好有唯一相同的表达式时，那条只会被记为未解决并点名它出现在哪个版本，不会判给它；`source_version` 恰好是另一份已登记政策的文号，直接报「来源文号与政策代号不一致」。
- **条件是政策版本内表达式规范化 JSON 哈希，要求一对一**：同一条库条件被两个 CSV 条件代号认领、一条 CSV 行在同一版里匹配到多条、或库里有条件没被任何 CSV 行认领，都报。
- **企业**按 `name_synthetic`；**事实**按 (key, 期间, unit, source, verified, 值哈希)，值按 `unit` 解析成与库同一类型（CNY/person/ratio → `Decimal`，date → ISO 日期，boolean → true/false，code → 原串）再算同一个哈希。
- **`labels.csv` 逐行三方一致**：`policy_id` 经登记表指向的版本，必须就是同一行写的 `policy_version`；(企业代号, 条件代号) 还要在该版本下落到库里的具体行。只比文号一列，标签挂到另一份政策的同代号条件上也会「对得上」。

库里没有业务代号列——`policy_criterion.criterion_id`、`enterprise.id` 都是导入时生成的 UUID，CSV 的 `CN-TAX-SME-2023-012`/`E-ENT-001` 在库里查不到——所以映射产物是一份**可核对的对照表**（代号 → UUID），`verify` 重算一遍再比对。对齐口径带版本号 `content-alignment@3`：规则一改，旧清单在 `verify` 里报「映射不同（可能是数据集换了、库被改过，或对齐规则版本变了）」，而不是含混的「数据变了」。有未解决项时 `verify` 一律非零退出；`inputs` 只把逐条问题打到 stderr（不改退出码），便于先看清再改数据。**「未解决」包括**：四份文件缺一份（没加载到行内容）、某份文件只有表头没有行（没有任何条件/企业/用例对齐出来）、政策代号没登记或登记版本在库里找不到、登记表与库的文号/标题不一致、双向覆盖缺口、值与核验状态对不上——空映射不能当成核对通过。值本身也要口径一致：CSV 的空 `fact_value` 与库里的 `NULL` 同为「未知」，都记 `valueHash: null`；未知不等于 `0`，有用例锁住这两点。

`inputs` 写出（`manifestVersion` 为 `3`：`2` 加 `documentNumber` 与 `datasetMapping`，`3` 把 `dataset` 从「文件名 → 哈希」改成「路径 + 哈希」的列表；更早版本的清单在 `verify` 里报「清单版本不同」，`dataset` 不是列表的则直接要求重新签认）：政策版本 → 条件的 `criterionId`/AST 哈希/审核状态、企业 → 事实值的哈希与**事实版本**（直接调 `agents/profile.fact_version_of`，与 `assessment_run.fact_version` 同一算法）、`appVersion`/`ruleVersion`、数据集每份文件的签认路径与 SHA-256、以及非敏感配置的哈希。`verify` 拿已签认的清单对当前库重算并逐项列差异，非零退出即「不一致」，**停止评测，不得用运行结果反推快照**。

- 四条查询跑在**同一个 `REPEATABLE READ, READ ONLY` 事务**里（M2 复核 P2）：两次查询之间别的连接提交的写入既不会混进这次清单，也不会把只读事务拖成半新半旧。用例同时查 `transaction_isolation`/`transaction_read_only`，并用一条中途提交的写入证明它没被混进来、换一次采集又看得到。
- 比较是**按字段表逐个走**的（M2 复核 P1）：清单里记下的字段——政策 `documentNumber`/`reviewStatus`/`importedAt`/`revision`/`criteriaSetFingerprint`、条件 `reviewFinding`/`validationStatus`、企业 `factVersion`、事实 `unit`/`source`/`verified`/`valueHash`、顶层 `configKeys`——都参与比较，报「签认 …，当前 …」。有一条用例对清单的每个叶子字段做单点变异，断言 `verify` 都能报出；以后给清单加字段却忘了加进比较，它会先转红。

- 表达式哈希是条件 `expression` 的规范化 JSON（排序键、`Decimal` 去尾零、日期 ISO）SHA-256，与引文 `quoteHash` 不是一回事；事实值只记哈希，值本身不进清单。
- 配置哈希只覆盖 `CONFIG_FINGERPRINT_KEYS` 白名单；`ADMIN_TOKEN`、`DEEPSEEK_API_KEY`、`DATABASE_URL`、`REDIS_URL`、OBS AK/SK 永不进哈希输入（名字命中也直接报错），有用例锁住「改令牌哈希不变」。
- 连接串只从环境变量 `DATABASE_URL` 读（不进 argv、不进 shell 历史），失败只报变量名。云端用 `systemd-run -p EnvironmentFile=/etc/huice/api.env` 带服务同款环境执行。
- **§5.2 第 4 项（结果导出）**按 M3 的口径实现为 `freeze-report`（`app/evaluation/report_freeze.py`）：把 API-13 下载到的 `report.md` 裁成不含「截至下载时刻的有效性」的冻结产物，原始报告连同下载时间与两侧哈希另存审计。

  ```bash
  python -m app.evaluation freeze-report report.md --out report.frozen.md
  ```

  按下载时刻现算的内容一并删干净：`- 下载时间：…` 行、整个有效性段、判断结论表的「有效性」列，以及每个政策段的「有效性 / 失效原因 / 有效期至」。剔除只按固定标记整行/整段删除，**不做语义猜测**：输入缺标题、缺有效性段（已裁过或手工改过）、表格列数对不上都直接报错，不生成产物。审计信息（`sourceSha256`、`frozenSha256`、`downloadedAt`、`droppedLines`）以一行 JSON 打到 stderr，`--out -` 时 stdout 只有冻结正文。
- **映射的边界**：只对 `labels.csv`、`criteria_review.csv`、`enterprises.csv`、`source_register.csv` 四份做逐条对齐（四份都必须给），其余文件仍只算哈希；缺列、CSV 不是 UTF-8、表达式不是合法 JSON、取值与 `unit` 对不上都直接停下报错，不猜列名、不猜类型。企业靠 `name_synthetic` 对齐，合成名称重复即报歧义。映射证明的是「这批 CSV 的内容都在库里」，不证明库里没有别的来源——那由逐字段比较与事实覆盖检查兜住。
- **已知缺口**：冻结正文里的条件级结果仍是 Markdown 项目符号（四态 + 理由 + 逐字引文 + 计算），人能读、机器只能半结构化解析；`trace` 按契约只有阶段级摘要，不作条件级证据。要机器可解析的条件级导出，需评估 JSON 导出或方案 B（均不新增表），本轮不做，记录在此。

### 2.10 消融对照：单智能体基线 vs 现方案

代码在 `services/api/app/ablation/`（不连库的纯文件模块 + 一条临时库链路），与 `data/` 的评测数据、`infra/sql` 的迁移一起构成一次可复现的对照实验。目的：证明「多阶段 + 规则求值 + 引用校验」比「一个大模型一次判完」强，并且**同一批输入、同一套标注、同一套指标**。2026-11-06 的跑数结果、失败样例与局限见 `docs/devlog/M1-11-12-评测报告.md`。

五个子命令各自产出文件，任何一步失败都不影响前一步：

```bash
cd services/api
python -m app.ablation extraction  --out runs/extraction.jsonl   # 现方案的冷启动抽取
python -m app.ablation baseline    --out runs/baseline.jsonl     # 单智能体基线
python -m app.ablation pipeline    --out runs/pipeline.jsonl     # 现方案（临时库 + assess()）
python -m app.ablation metrics --baseline runs/baseline.jsonl --pipeline runs/pipeline.jsonl
python -m app.ablation review-sheet --baseline runs/baseline.jsonl --pipeline runs/pipeline.jsonl --out runs/review.csv
```

`pipeline` 的库地址只认 `HUICE_EVAL_DATABASE_URL`（或 `--dsn`），**不读 `DATABASE_URL`**：脚本会在其中建临时 schema、跑完全部迁移两遍、结束时删掉，因此必须指向可建 schema 的空闲库；与 `DATABASE_URL` 相同的地址会被拒绝。`asOf` 默认取运行当天的北京时间正午，命令行 `--as-of` 可覆盖，两个方案必须用同一个值。

指标口径（`app/ablation/metrics.py` 的模块文档为准，这里只列结论）：

- **分母 146 条**：`labels.csv` 148 条去掉 `国科发火〔2008〕172号` 那 2 条（C031/C033）——接口按 `asOf` 只解析到 2016 版，判不了的不算方案错。dev 120 / holdout 26 分列。
- **四态一致率**：与已仲裁结果相同的条数 ÷ 分母；标注只有三值，方案给 `conflict` 一律算不一致并单列条数。
- **硬门槛误判**：标注「不满足」却判「满足」的条数 ÷ 11 条，附 Wilson 95% 区间。11 条样本下 0 例只能说「实测 0 例」，报告不得写成「零风险」。
- **无依据断言率**：给出确定结论（`satisfied`/`not_satisfied`）却既无引文也无事实引用的比例。这是基线也能算的「幻觉等价物」；**幻觉条款率只对现方案算**（基线没有抽取阶段），分母是模型实际给出的条款数（收下的 + 因引文不实/表达式不合法被丢弃的）。
- **引用可证实率**：引文能在出处原文里逐字找到（NFKC + 去空白后比较）的比例。`RD-001` 的出处只有节选，**不进分母**——本仓没有原文不能算模型幻觉。
- **时效识别**：已过适用期（`effectiveTo` 早于运行日）的 case 里，方案是否告知「已过期 / 申报窗口已关闭」。现方案的标记取自 `get_run` 的**读取时**有效性视图（`versioning.resolve_validity`，用户页面上看到的那一版），不是 `assess` 写入时的 `staleReasons`（按设计只看记录之后发生的事件，永远不含过期）。
- **token**：冷启动（政策入库抽取）与边际（每个 case）分开报；用量未知的调用不补 0，单列条数。基线每个 case 最多 1 次重试，重试次数与 `max_tokens` 写进 JSONL 表头。
- **人工复核**：每方案按方案结论四态分层抽 30 条（每个非空类别至少 1 条，`not_satisfied` 至少 5 条——硬门槛误判只有人工看得见），种子固定，写入 CSV；引文是否可证实由程序先判一次，复核人只判「结论对不对」并计时。
- **抽取覆盖率是下限**：只有抽取结果的引文包含标注原文片段才算命中，抽到同一条件但引文取短了也记未命中。模型单次抽样波动明显（2026-11-06 同一提示词三轮：32% / 36% / 40%），报告要写多轮的区间，不能报单轮精确值。
- **费用**：单价取自 `infra/README.md` §4.3（2026-11-06 取数，`deepseek-flash` 高峰价：输入未命中 2 元/百万 tokens、输出 8 元/百万）。客户端不解析响应里的缓存命中 token 数，一律按未命中价折算，故金额是**上限**；分母、分子都从 JSONL 的 `usage_prompt`/`usage_completion` 直接相加，不估算。

**逐段出处与输入签名（2026-11-08，M2 在 #51 / #36 复审的 P1）**：

- `criteria_review.csv` 的 `source_document` 按 `|` 分段，与 `source_span` **逐段一一对应**；声明了出处就只用那一份正文校验，不再按「哪份文本先包含这句」顶替。段数对不上、声明的那份没有独立正文、或原文里找不到该句，该条判不可验证（导入器记 blocked），**不跨来源搜索**。`source_document` 整列为空时才退回本版正文；只在 `source_version` 里出现、`source_document` 没声明的出处不导入附件。导入器与消融加载器（`app/ablation/dataset.py` 的 `span_source`）用同一套规则，同一份数据在两边得到同一结论。
- 输入签名覆盖**实际读到的文件**，不是固定四份 CSV。`Dataset.manifest()` 含四份 CSV、被条件引用到的政策正文（`policies/sources/<文号或代号>.txt`）、每条 `source_document` 声明的出处原文，以及 `source_document` 为空时实际回退读取的 `version_document` 原文；列出的文件缺失即报错。导入器 `--manifest` 按 `build_plans` 同一口径确定必需项（要灌版本的正文、逐段声明的出处原文、这些出处要求的 `reference_sources.csv` 映射行），缺任何一项非零退出、**不输出缩短的清单**；没有被任何条件引用的来源不凭空要求。`--manifest` 只读文件、打印相对路径与 SHA-256，不连库、不写库。

已知取舍，写进评测报告的「局限」段而不是藏起来：

1. 基线拿得到条件清单（代号 + 原文片段），否则对不上 `criterionId`，这是**对基线有利**的取舍。
2. 灌库时把三个「先执行、后发文」版本的发布日压到生效日，只为满足 `policy_version_dates` 的 `effective_from >= published_at`；改的是临时库，不是 CSV。
3. `applicable_entities` 登记表没有这一列，灌库时统一填「企业」占位；它只进政策详情与准备清单模板，不参与条件求值。
4. 同一政策只灌一个版本；进指标的 case 若横跨同一政策的多个版本，`pipeline.seed` 直接报错而不是按发布时间戳抢答。

## 3. HTTP 共同约束

- 前缀 `/api/v1`；JSON camelCase；实体/请求/追踪 ID 为 UUID。政策 version 为人工版本字符串，事实哈希和规则版本不强制 UUID。日期 `YYYY-MM-DD`，时间带时区 ISO 8601；数据库 UTC。
- 成功响应直接返回对象，不另包 `{data:...}`。列表 `{items,nextCursor}`，默认 limit=20、1—50；cursor 不透明，过滤条件改变须清空 cursor。空列表 200，不是 404。
- 所有公开政策读取只含已审核版本。`version` 指定不存在/未发布版本时 404，不能回退到其他版本。后台 API-16 可读取未发布版本。
- 非 2xx 统一 `{"error":{"code":"...","message":"中文可执行说明","details":{}},"traceId":"UUID"}`；FastAPI 默认 detail 数组必须转换。响应头 `X-Trace-Id` 与 body.traceId 一致；成功也返回追踪头。422 的 details 可放 fieldErrors/findings；409 放 currentVersion；不得含堆栈、密钥或连接串。
- 状态码：400 请求语法/查询参数错误；401 未认证；403 已认证但权限不足；404 不存在；409 版本/幂等冲突；413 文件过大；415 文件类型不支持；422 业务或 body 校验失败；428 缺并发头；429 限流；500 未预期内部错误（脱敏）；503 必需依赖不可用。
- **认证分两层（E1，#46）**：外层是**门禁**，内层是**身份/角色/归属**。`security.py` 只表达门禁（`require_demo_session`），身份与角色在 `identity.py`，对象归属在 `access.py`。判定顺序固定：门禁 → 请求体解析与字段校验 → 身份 → 角色 → 对象归属 → CSRF/Origin。门禁由路由类 `GatedRoute` 在**解析请求体之前**执行（`APIRouter(dependencies=[...])` 或写在 handler 里都会被 422 抢先），因此 restricted 下缺 demo cookie 时，请求体缺字段、类型不对、为空或不是 JSON 都先得到 403 `DEMO_SESSION_REQUIRED`；local 模式没有门禁，同样的请求才是 422。前端据此区分三种失败：403 `DEMO_SESSION_REQUIRED`（门禁失效，回演示入口重取）、422 `VALIDATION_ERROR`（表单字段问题）、401（会话失效，跳登录页）。
  - 门禁：`DEMO_ACCESS_MODE=restricted` 时业务接口都要 `huice_demo_session`（`HttpOnly/Secure/SameSite=Lax`，载荷 `v2.<过期秒>.<sid>.<HMAC>`；E1 前签发的 `v1.<过期秒>.<HMAC>` 仍验签通过但**只读**）；豁免只有 `GET /health` 与 `POST /auth/logout`；`local` 模式免。
  - 身份：会话 Cookie `huice_session` → 运维 `Authorization: Bearer <ADMIN_TOKEN>` → demo cookie，按真值表取**第一个「提供了且有效」**的凭证：只有未提供才轮到下一个，提供了但无效一律 401（`TOKEN_INVALID` / `SESSION_INVALID`），绝不降级到 demo。注册/登录/登出不消费会话 Cookie（浏览器删不掉 HttpOnly Cookie）。
  - 角色：凭证有效但角色不够 → 403 `ROLE_FORBIDDEN`。运维 Bearer 在 E1 后**只读**：可读后台政策与全部 run 轨迹，任何业务接口与写操作都 403。
  - 对象归属：他人企业与 run 一律 404，且与「不存在」**同码同文案**（`ENTERPRISE_NOT_FOUND` / `RUN_NOT_FOUND`），否则 404/403 的差别本身就是「他人对象是否存在」的探针。企业读：本人 / 审核员全部 / demo 仅无主；企业写：仅本人（审核员也只能改本人）；run 读同企业读口径。
  - CSRF/Origin：会话写请求要 `X-CSRF-Token`（登录返回的 `csrfToken`，等于 `sha256("huice-csrf:" + 会话令牌)[:32]`）；注册/登录/登出只校验 Origin；demo 写只校验 Origin（+Referer）；Bearer 不校验。两样都缺时先报 `ORIGIN_MISMATCH`。
- 本地模式仅监听 loopback。**运维通道**（`Authorization: Bearer <ADMIN_TOKEN>`）只用于脚本/curl 读后台政策与轨迹：M1 在受控机器手动输入，M2 的 client 仅保存在内存，退出/刷新清空，不写 localStorage、URL 或 VITE 变量；E1 后浏览器里的后台写操作改用**审核员会话**，不再把 `ADMIN_TOKEN` 打进前端。没有凭证不展示后台数据。
- 云端 restricted 模式网关先验证 `huice_demo_session` 会话 cookie，再交给后端做身份判定；后端自己校验 cookie 的 HMAC 签名与有效期（签发脚本 `infra/deploy/issue-session.py`，同时写 `demo_quota` 起始行）；签名密钥是独立的 `DEMO_SESSION_SECRET`（restricted 模式必填），**不由 `ADMIN_TOKEN` 派生**，因此持有 Bearer 的人不能自行签发会话，两层门禁相互独立。网关仍需负责下发并拦掉绕过网关的请求，不能仅隐藏按钮。OpenAPI 里 `security` 只写身份，门禁逐操作写在 `x-e1-gate`（`contracts/openapi.yaml` 与运行时 `/openapi.json` 两份一致，含 `x-e1-gate-model` 的口径与 `evaluation-order`；门禁先于请求体解析）；7 个 E1 操作自 `1.1.0-rc.1` 起已并入正式契约，`openapi.e1-draft.yaml` 保留为设计记录（门禁/身份真值表与请求样例）；local 模式免演示 cookie。健康检查无认证且不含连接详情。**rc.10 起发布的 `security` 与该判定逐条对齐**（M2 在 PR #61 的 P1）：业务接口 `UserSession` 或 `DemoSession`，后台读 `UserSession` 或 `AdminBearer`，后台写只有 `UserSession`（Bearer 只读），轨迹三类身份都可读——契约不再承诺后端一定会 403 的入口。
- 跨源开发 fetch 使用 `credentials:'include'`，CORS 只允许确切前端源并允许 credentials。允许头 `Content-Type,Authorization,If-Match,Idempotency-Key,X-CSRF-Token`；暴露头 `ETag,X-Trace-Id,Content-Disposition`。M1 处理 OPTIONS；M2 不能以 no-cors 规避。

## 4. 端点与参数传递（路径均省略 /api/v1）

### 4.1 公开政策与企业

| ID / 方法路径 | 参数位置与必填约束 | 成功响应 |
| --- | --- | --- |
| API-01 `GET /health` | 无 body | 200 `{status:"ok",version}`；未就绪 503 |
| API-02 `GET /policies` | query：status=published/closed、type=tax/grant、region、asOf、cursor、limit 均可选；不传 status 时含已审开放和关闭样本；**region 按「该区划 + 全国性政策」匹配**——传 `440300` 同时返回 `region=CN` 的全国性政策，传 `CN` 只返回全国性政策（2026-10-29 修，见下） | 200 `PolicyList`；支持历史展示 |
| API-03 `GET /policies/{policyId}` | path UUID；query version 可选 | 200 `PolicyDetail`，sources/criteria/preparationTemplate |
| API-08 `POST /enterprises` | JSON `EnterpriseInput`，见 2.3。会话写要 CSRF + Origin；demo 写只要 Origin，并占用本次演示会话的一个建样名额 | 201 `Enterprise` + ETag；账号已绑企业 409 `ENTERPRISE_ALREADY_BOUND`（`details.enterpriseId` 给已绑的那家；两个并发请求同时到达时同样 409，由 `owner_user_id` 部分唯一索引兜底）；演示配额用完 429 `DEMO_QUOTA_EXCEEDED`（`details.limit`） |
| API-09 `GET /enterprises/{enterpriseId}` | path UUID | 200 `Enterprise` + ETag |
| API-10 `PATCH /enterprises/{enterpriseId}/facts/{factKey}` | path ID/key；If-Match 头必填；JSON `FactUpdate` 不含 key，period 必填；仅企业本人可写（审核员、demo、Bearer 不行） | 200 完整 `Enterprise` + 新 ETag |
| AUTH-06 `GET /enterprises`（E1 新增） | query cursor/limit 可选；服务端按身份过滤：企业会话只看本人、审核员看全部、demo 只看无主样本、Bearer 403 | 200 `EnterpriseSummaryList` = `{items:EnterpriseSummary[],nextCursor}`，`EnterpriseSummary={id,nameSynthetic,period,ownership}`；`ownership` 为 `self/other/unowned`。列表不带 `facts`：一次拉全量事实会让导航页变成画像下载 |

`Enterprise={id,nameSynthetic,period,facts:EnterpriseFact[],factVersion}`。API-02 只给政策元数据，不含企业判断；M2 按 policyId 将 API-11/12 的 assessments 合并到列表。未分析时显示“尚未分析”，不得猜测四态。

> **演示建样名额与建企业同事务（2026-11-11，#61 review 的 P2）**：`normalize_profile` 的 422 校验在占额之前，格式错误不消耗名额；通过校验后，占额（`INSERT … ON CONFLICT (sid_hash) DO UPDATE … WHERE created < limit AND expires_at > now() RETURNING created`，单语句原子，并发下只有最后一个名额的唯一赢家）与 `enterprise` 的 INSERT、`profile` 轨迹在**同一个数据库事务**里（`PolicyRepository.create_enterprise(demo_quota=…)`）。任何一步失败——配额用尽 429、唯一索引撞车、外键失败、进程崩溃——整个事务回滚，名额一起退回，不会出现「扣了名额、样本没建成」。不再有「先占后建、失败再退」：退额本身失败或进程在两步之间退出时，那种写法仍会白扣一个名额。用例：`tests/test_e1_access.py`（非法 body 不占额、写库失败名额不变、配额用尽不建企业）+ `tests/test_db_demo_quota.py`（真库的同成同败、过期不占额、8 并发只放行 2 个）。

> **region 过滤按「该区划 + 全国」（2026-10-29 修）**：行政区划码只覆盖地方，国家层面的政策没有对应区划码，`source_register.csv` 统一记 `CN`（`storage.NATIONAL_REGION_CODE`）。若按等值过滤，深圳企业查 `region=440300` 会看不到《研发费用加计扣除公告》这类全国性政策——候选集合数量对但内容缺，而且缺的是最不该缺的那部分。因此改为「`region = 传入值` 或 `region = CN`」，只放宽不收紧；传 `CN` 本身只返回全国性政策。目前不支持「只看地方」的严格过滤，若 M2 需要再加显式参数，不加就默认放宽。前端展示时应把 `region=CN` 显示为「全国」，不要显示成区划码。

### 4.2 后台政策（全部管理权限）

| ID / 方法路径 | 参数位置与必填约束 | 成功响应 |
| --- | --- | --- |
| API-04 `POST /admin/policies/import` | multipart metadata 字符串 + files；每文件 ≤10 MiB、最多5个；第一个文件为正文、其余为附件，可传0个文件；仅 PDF/UTF-8 text（含原始 HTML，展示须转义），按真实内容而非扩展名判断 | 201 `PolicyDraft`（`status=pending_parse`、`criteria: []`）+ ETag；相同政策版本 409 |
| API-05 `POST /admin/policies/{policyId}/extract` | JSON `{version,modelVersion?}`；If-Match 和 UUID Idempotency-Key 必填 | 200 `ExtractionResult`（含更新后的 draft、criteriaDrafts、validationFindings、usage）+ 新 ETag |
| API-06 `PUT /admin/policies/{policyId}/criteria` | JSON `{version,criteria:CriterionDraft[]}`；If-Match 必填；criteria 至少一条，ID 不重复 | 200 `{draft,criteria,validationFindings}` + 新 ETag |
| API-07 `POST /admin/policies/{policyId}/publish` | JSON `{version,reviewConfirmed:true}`；If-Match 必填 | 200 `PolicyDetail` + 发布后 revision ETag；失败保留草稿 |
| API-15 `GET /admin/policies` | query status/type/cursor/limit 可选；含 pending_parse/pending_review | 200 `{items:PolicyDraft[],nextCursor}`；每个政策版本一项 |
| API-16 `GET /admin/policies/{policyId}` | query version 可选；缺省最新导入版本，不等于最新已发布版 | 200 `PolicyDraft` + ETag；含原文、规则、findings、审核信息 |

`PolicyDraft` 包含 metadata、id、version、status、revision、sources、criteria、validationFindings；revision 为每次草稿变更生成的 UUID，与人工 version 不同。前端读取 API-16 得到 ETag，写请求传入同一标签。新导入版本有独立 revision。

API-05 同一 policyId/version/Idempotency-Key 和相同 body 的已成功请求返回原结果，不重复计费；body 不同返回409；同 key 正在执行返回409。幂等缓存命中先于 If-Match 检查，但仅能在当前 revision 仍等于该请求生成 revision 时重放，否则409并要求重新读取，避免把旧草稿当最新。失败不自动用新 key 重试；同 key 可在没有成功产物时受预算约束重试。并发抽取/人工修改冲突时不得覆盖人工结果。已发布版 extract/PUT 返回409；发布同版不新造版本。

审核页先 API-15 → API-16；导入返回 draft 后可直接编辑。保存/发布冲突时保留本地输入，提示重新加载并人工合并，不能默默覆盖。元数据修改使用 API-04 新版本，不增加“直接改已发布元数据”入口。

### 4.3 判断、报告、轨迹

| ID / 方法路径 | 参数位置与必填约束 | 成功响应 |
| --- | --- | --- |
| API-11 `POST /assessment-runs` | JSON `{enterpriseId,policyIds,asOf,period,supersedesRunId?,skippedFactRefs?}` | 200 `AssessmentRun`；同步完成，不返回未定义 taskId |
| API-12 `GET /assessment-runs/{runId}` | path UUID | 200 `AssessmentRun`，读取时刷新有效性视图 |
| API-13 `GET /assessment-runs/{runId}/report.md` | path UUID；Accept=`text/markdown` | 200 `text/markdown; charset=utf-8`，Content-Disposition 为安全文件名的 attachment |
| API-14 `GET /assessment-runs/{runId}/trace` | path UUID；企业会话（本人 run）/ 审核员会话 / 运维 Bearer | 200 `{steps:TraceStep[]}`，sequence 升序 |
| AUTH-07 `GET /assessment-runs`（E1 新增） | query `enterpriseId`（可选，越出可见范围 → 404 `ENTERPRISE_NOT_FOUND`）、cursor、limit；可见范围同企业读口径 | 200 `RunSummaryList` = `{items:RunSummary[],nextCursor}`，`RunSummary={id,enterpriseId,asOf,createdAt,status,reportAvailable}`；`status` 为 `queued/running/succeeded/failed` |

API-11 不复用 runId；用户重复点击由前端禁用提交按钮，网络失败后不得自动创建重复运行。同步超时由 M1 记录，MVP 不引入未定义异步队列。所有 API 都须处理统一错误；报告接口失败时 Content-Type 回到 application/json。

`AssessmentRun={id,enterpriseId,period,asOf,createdAt,policyVersionMap,factVersion,ruleVersion,supersedesRunId,assessments}`。`supersedesRunId` 响应始终有值或 null。

`PolicyAssessment={policyId,result,validity,staleReasons,criteriaResults,missingFacts,nextQuestion,validUntil,preparation,explanation,explanationMode,warnings}`。`CriterionResult={criterionId,result,reason,factRefs,evidenceRefs,calculation}`，calculation 可 null，季度计算必须给输入与中间值。`FactRef={key,period}`；missingFacts 是 FactRef 数组，结果不是 insufficient_evidence 时恒为空数组（个别条件缺事实不影响已定结论），结果如此时只含仍挡住结论的事实。`NextQuestion={factRef,prompt,reason,rank}`；不存在为 null。

四态枚举仅 `satisfied/not_satisfied/insufficient_evidence/conflict`；有效性仅 `current/stale`；closed 是政策状态。stale 展示优先，但原四态与原因仍可见。schema 中规定的可空字段应显式传 null；不能把空数组、缺字段、null 任意互换。

### 4.4 账号与会话（E1 新增）

契约在 `contracts/openapi.yaml`（`1.1.0-rc.1` 起并入），设计记录见 `contracts/openapi.e1-draft.yaml`，实现见 `services/api/app/routes/auth.py`；判定顺序见 §3。

| ID / 方法路径 | 参数位置与必填约束 | 成功响应 |
| --- | --- | --- |
| AUTH-01 `POST /auth/register` | JSON `{username,password,enterprise?,reviewerSignupKey?}`；`username` 按**发送的原始值**校验 `^[a-z0-9_.-]{3,32}$`——调用方先 trim、转小写再发（`ACME.Two` → `acme.two`），服务端再做的幂等规范化只是容错、不是契约承诺，按契约发的合法值原样入库。企业用户必须带 `enterprise`；带**有效** `reviewerSignupKey` 时不建企业。只有 Origin 校验，不消费会话 Cookie | 201 `{user:{id,username,role,enterpriseId}}`，不自动登录；重名 409 `USERNAME_TAKEN`，密钥错 403 `ROLE_FORBIDDEN` |
| AUTH-02 `POST /auth/login` | JSON `{username,password}`；只有 Origin 校验。请求里**有效**的会话会被撤销并换新（不留两个活会话）；无效/过期的直接忽略，不返回 401 | 200 `{user,expiresAt,csrfToken}` + `Set-Cookie: huice_session=…; HttpOnly; SameSite=lax; Path=/api`（非 local 加 `Secure`），`Cache-Control: no-store`；账号不存在与密码错同为 401 `INVALID_CREDENTIALS` |
| AUTH-03 `POST /auth/logout` | 无 body；只撤销**当前出示的**会话，缺 CSRF 时 403 `CSRF_REQUIRED` 且无副作用；无效会话按「无会话」处理 | 204 + 清 Cookie。门禁豁免（否则 demo cookie 过期后连登出都做不了） |
| AUTH-04 `GET /auth/me` | 只要会话：带**有效** demo cookie 或运维 Bearer 但没有用户会话 → 403 `ROLE_FORBIDDEN`（凭证有效但不够，不是「会话过期」；local 与 restricted 同结论，restricted 还要先过门禁） | 200 `{user,expiresAt,csrfToken}`（`csrfToken` 与 AUTH-02 相同，首版不轮换），`no-store`；会话失效 401 `SESSION_INVALID` |
| AUTH-05 `POST /auth/password` | JSON `{currentPassword,newPassword}`；CSRF + Origin 必填 | 204；成功后撤销该账号**全部**会话并清 Cookie。旧密码错 401 `INVALID_CREDENTIALS` |

共用的两处工具：密码哈希 `hashlib.scrypt`（格式 `scrypt$n$r$p$salt$hash`），CSRF 值 `sha256("huice-csrf:" + 会话令牌)[:32]`。

登录/注册限流按 IP，三条约束（`app/identity.py` 的 `LoginLimiter.attempt()`，名额在**进入慢哈希之前**原子占下）：

1. **在途名额**：同一 IP 同时最多 5 个认证请求在跑，第 6 个立刻 429（`Retry-After: 1`）。没有这一条时，并发洪峰会在首个失败落账前全部通过检查，一起压满 scrypt 的 CPU（M2 在 PR #61 的 P1）。
2. **失败窗口**：60 秒内 5 次失败后拒绝，直到窗口滑过。
3. **连续失败**：累计 20 次失败锁 900 秒。

只有 401/403（密码错、注册密钥错）算失败；数据库故障等 5xx 只释放名额，故障期间不会把整片 IP 锁死。**成功不清零失败记录**，只释放自己的名额——否则握有一个有效账号的人可以靠反复登录把失败计数刷掉，再接着猜别的账号。纯字段校验（422）在限流块之外，不消耗失败次数。计数在进程内：`huice-api.service` 是单 worker，重启清零；`nginx-huice.conf` 另有一层每 IP 20 r/s（突发 40）的粗粒度 `limit_req` 兜瞬时洪峰，它看不到密码对不对，替代不了上面的按失败次数锁定。

**保底审核员**：E1 起后台写操作只接受审核员会话（Bearer 只读），而注册接口建审核员要配置 `REVIEWER_SIGNUP_KEY`。全新部署用 `infra/deploy/create-admin.py` 由持 root 的人在 ECS 上写库建第一个审核员（只从环境读 `DATABASE_URL`/`ADMIN_INIT_USERNAME`/`ADMIN_INIT_PASSWORD`，不覆盖既有账号），流程见 `infra/deploy/README.md` §3.4。

### 4.5 政策发现与候选（E2 新增）

**契约位置**：`contracts/openapi.e2-draft.yaml`（`0.2.0-draft.1`）。这 8 个操作已在跑，但**尚未并入** `contracts/openapi.yaml`——草案评审通过前不构成对外承诺，前端类型也不据此生成。自检两条：`python contracts/check_e2_draft.py`（草案结构、权限、错误码、运行时模型、样例校验）与 `pytest services/api/tests/test_e2_draft.py`（草案与服务实际发布的操作/组件/常量逐项对照）。

**实现位置**：`app/discovery/{fetch,metadata,extraction,runner}.py`（抓取、元数据启发式、结构化抽取、一轮运行）、`app/integrations/discovery.py`（仓储）、`app/routes/discovery.py`（HTTP）、迁移 `010_policy_discovery.sql`、部署件 `huice-discovery.{service,timer}`。

| ID / 方法路径 | 参数位置与必填约束 | 成功响应 |
| --- | --- | --- |
| DISC-01 `GET /admin/discovery/settings` | 审核员会话或运维 Bearer | 200 `{enabled,sourceUrls,intervalMinutes,timeZone,nextRunAt,limits}` + `ETag`；`limits` 回显服务端允许范围，前端据此禁用非法输入 |
| DISC-02 `PUT /admin/discovery/settings` | 替换语义，`If-Match` 必填（强 ETag）；`enabled` 为真时至少要一个来源；每个来源必须是 HTTPS 且落在 `OFFICIAL_HOST_SUFFIXES`（gov.cn 及其子域）内，逐条解析 DNS 排除内网地址；周期 360—43200 分钟，最多 20 个来源，时区需可解析 | 200 同 DISC-01（新 `ETag`）；缺 `If-Match` 428 `MISSING_IF_MATCH`，弱校验/`*` 400 `INVALID_IF_MATCH`，修订号过期 409 `VERSION_CONFLICT`，字段问题 422 `VALIDATION_ERROR`（`details.fieldErrors[].field` 精确到 `sourceUrls[0]`） |
| DISC-03 `GET /admin/discovery/runs` | `cursor`/`limit`（1—50，默认 20） | 200 `{items,nextCursor}`；每条含 `status`（`running/succeeded/partial_failed/failed`）与 `terminalReason`（8 个终态码） |
| DISC-04 `GET /admin/policy-candidates` | `status` 过滤 + `cursor`/`limit` | 200 `{items: CandidateSummary[],nextCursor}`；`missingFields` 是**准确**的待补清单 |
| DISC-05 `GET /admin/policy-candidates/{candidateId}` | 路径参数 | 200 `CandidateDetail`（元数据、正文、条件、取证字段、用量）+ `ETag`；不存在 404 `CANDIDATE_NOT_FOUND` |
| DISC-06 `PATCH /admin/policy-candidates/{candidateId}` | `If-Match` 必填；`metadata` 是**整体替换**（缺的字段按空处理），`criteriaDrafts` 可选 | 200 同 DISC-05；日期/申报窗口顺序不合法 422；状态不可编辑或修订号过期 409（`CANDIDATE_STATE_CONFLICT` / `VERSION_CONFLICT`） |
| DISC-07 `POST /admin/policy-candidates/{candidateId}/submit` | `If-Match` + `Idempotency-Key` 必填 | 201 `PolicyDraft` + `ETag`；缺幂等键 400，同键异议 409 `IDEMPOTENCY_KEY_REUSED`，元数据未补齐/无条件 422，已提交/已丢弃/已过期/抽取失败 409 `CANDIDATE_STATE_CONFLICT`（附 `currentStatus`，已提交时带 `submittedPolicyId`/`submittedVersion`） |
| DISC-08 `POST /admin/policy-candidates/{candidateId}/discard` | `If-Match` 必填；body 可选 `{reason}`（≤500 字） | 200 `CandidateDetail`（`status=discarded`）；再次丢弃 409 |

**运行时口径**（契约 `x-e2-runtime-model`，由 `tests/test_e2_draft.py` 与代码常量互校）：

1. **唤醒 ≠ 运行**。timer 每 15 分钟唤醒一次，CLI 只看 `discovery_settings.next_run_at`；没到点直接退出且**不写运行记录**（否则列表会被空转淹没）。周期按「现在 + interval」推进，不是「距上次运行」。
2. **并发上限 1** 有两道：同一 systemd unit 不会被并发启动；库层 `pg_try_advisory_lock(hashtext('huice.discovery'))`，**锁与所有数据库写入共用同一条专用连接**，进程退出即释放。拿不到锁时本轮返回 `locked` 且不推进 `next_run_at`。进程崩溃留下的 `running` 行由下一轮持锁后收成 `failed/lock_lost`。
3. **轮换与启动在一个事务里**：置上一轮未提交候选为 `expired` → 插运行行 → 推进 `next_run_at`。已提交（`submitted`）与已丢弃（`discarded`）是终态，不会被轮转带走。
4. **预算两级**：单候选 8,000 token（`int(len(text)/1.5)+64` 估算，估算前就超的不发模型请求，候选直接落 `extraction_failed/EXTRACTION_BUDGET_EXCEEDED`）；整轮 50,000 token。发调用前按「正文估算 + 系统提示余量 1,000 + 输出上限 2,048」预留，只按输入判断会让最后一次调用把整轮顶过上限；装不下就停止后续候选并记 `partial_failed/budget_exceeded`。
5. **抓取护栏**：单页 15 秒、响应 ≤2 MiB、同站间隔 ≥2 秒、并发 1、单轮最多 50 条新候选 / 200 篇正文、重定向 ≤5 跳且**逐跳**复检主机。正文只解析 HTML、纯文本与 PDF：先嗅探 doctype/`%PDF` 魔数（所以把 HTML 标成 `application/octet-stream` 的站点仍能解析），其余类型——DOCX、压缩包、图片——按 `unsupported_content` 判正文失败，不把二进制解成乱码再喂模型。**框架页/入口页不当正文**：规范化后不足 `MIN_ARTICLE_CHARS`（60 字）的页面不算正文——没有可列取链接的来源页按来源级失败记 `unsupported_list`，不硬造一条空白候选；抓下来的「正文」页若也不像正文，计一次正文失败（同样归 `unsupported_list`）。
6. **失败与「没有新政策」可分辨**：`succeeded/completed`（有新候选）、`succeeded/no_new_policy`（跑了但没新的）、`failed/fetch_failed|timeout|oversize`、`partial_failed/partial_failed|budget_exceeded`、`failed/lock_lost` 是互不相同的组合，不需要解析 `detail` 文案。**正文级失败单独计数**（`failedArticles` + 每条来源的 `articleFailures`）：栏目页拿到、正文一篇没下来按来源失败记并让整轮落 `failed`；部分失败记 `partial_failed`，不会退化成「没有新政策」。
7. **附件首版不抓取**：页面上识别到的附件 URL（只收 https，与 `OfficialUrl` 一致）照记在 `attachmentUrls`，列表另给 `attachmentCount`；**http:// 等不能进候选的附件链接**单独记在 `unsupportedAttachmentUrls`（列表给 `unsupportedAttachmentCount`）——过滤后的 `attachmentUrls` 为空**不等于**这一页没有附件，两个计数任一大于 0 都要提示。附件**不下载、不解析，附件内容也不能作为候选草稿的引用来源**——条件只写在附件里时，抽取侧看不到它。`validationFindings` 里那条 `ATTACHMENT_NOT_EXTRACTED` 在**候选侧**由这两列派生——DISC-06 保存 `criteriaDrafts` 会整体重算这一列，存进去的副本第一次编辑就没了，派生版编辑不掉。DISC-07 提交时它作为**来源级 finding**（`criterionId` 为 null）随草稿落库；此后 API-05 重新抽取、API-06 保存条款整体替换 findings 时，`PolicyRepository.save_criteria` 按 `PRESERVED_SOURCE_FINDING_CODES` 从库里保留它，只重算条件级结论，过时的条件错误照常清除，反复保存也不会重复累积。该码**不阻断发布**（`POLICY_BLOCKING_CODES` 不含它）：没有逐附件处置记录时，阻断只会让带附件的政策永远发不出去；所以提示也不等于附件已核对。需要附件里的条款时，走人工导入，且只接受**可复制文本的 PDF 或 UTF-8 文本**——DOC/DOCX/ZIP 需先离线转换。因此 S03/S04/S05 这类「条件主要在附件」的来源暂不纳入 E2 自动发现验收。
8. **判重两级：整页哈希确认重复就跳过，正文指纹只提示、不再吞掉候选**。确认重复只看整页规范化后的 `content_hash`（本轮已见、同 URL 未变化、候选指纹表命中），命中记 `duplicates` 且不建候选。跨 URL 才比**正文指纹** `body_fingerprint`——按 `。！？；` 与换行切句，收 ≥40 字的长条款，以及 ≥12 字、**带数字**的短句（截止日、金额、人数、比例常单独成句，只比长句会让「2026 改 2027」看不出来），排除 ICP/网站标识码等页脚标记与各站写法不一的元数据（发文字号、成文日期、纯日期行），去重排序后取 SHA-256；`normalize_text` 把各类空白统一成单空格，句子不足 2 句时指纹为空、只靠整页哈希。**指纹命中不再 `continue`**：照常建候选、照常抽取，运行记录记 `suspectedDuplicates`（与确认重复的 `duplicates` 分开），候选带 `suspectedDuplicateOf` 指向指纹命中的那条、`suspectedDuplicateSourceUrl` 给出来源页，列表和详情都能看到「疑似与这一页重复」。由审核员比对后决定丢弃还是分别提交——不合并、不覆盖任何已有候选或政策。句子集合是无序的，条款顺序调整同样只提示、不跳过。改成提示而不是跳过，是因为 #64 用「长条款逐字相同、只把『适用范围：深圳市』改成『广东省』」复现过旧行为：两份都不带数字，指纹相同，后一份会被静默吞掉，审核员看不到差异。**同一 URL 内容变了不走指纹判重**：那是新版政策，按新候选落库并标 `versionChanged`——短句里的日期、金额、适用范围变化本就该由人工确认，不能靠指纹兜底。仍有的限制要说明：①「疑似重复」只在能指到一条候选时才非空，内容早已提交/丢弃、只剩指纹行时该页仍会建候选但没有提示；②疑似重复也要跑一次抽取，会占用整轮 token 预算，跨站大量转载时先看到 `budget_exceeded` 而不是大量重复候选；③同站多篇正文只差一个短标签时同样会命中指纹，现在表现为疑似重复而不是合并（用例 `article_pages` 因此把差异放进长条款）。

**候选与政策库的关系**：`policy_candidate` 与 `policy` 不共表，候选阶段只写暂存区；DISC-07 才在**一个事务**里建草稿（`create_policy_version`）→ 改挂引用片段版本并重校验（`save_criteria`）→ 标记候选已提交 → 写幂等记录，全部一起提交或一起回滚；正文落盘也在同一事务内（重放路径在落盘前就返回，不会留孤儿对象）。所以「未提交候选不进入政策库」是结构上的事实，不靠前端不显示。幂等作用域是 `policy-candidate-submit`（与抽取的 `extract` 分开），指纹是 `sha256(candidateId:revision)`。**查键、锁候选、写记录在同一把键锁下**：`pg_advisory_xact_lock(hashtext(scope), hashtext(key))` 串行化同键请求，后到的等前者提交再读记录——同键同候选并发也是重放**同一份**草稿（不是 409），同键换候选是 409 `IDEMPOTENCY_KEY_REUSED` 且只建一份草稿。作用域按**操作**划分、不含操作者（与 API-03 的 `extract` 一致）：候选是全体审核员共享的资源，另一个审核员撞上同一个键时，同候选同修订号拿到同一份草稿，换候选则明确 409，不会悄悄建第二份。

**元数据抽取只摘原文写着的**（`app/discovery/metadata.py`）：版本号与适用主体永不代填；区划只按来源域名查登记表；发布日期只认带标签的日期，生效日期只认「自……起施行」句式。抽到的字段不会出现在 `missingFields` 里——抽取侧与接口侧共用 `FIELD_COLUMNS` / `missing_fields_of`，避免「清单说缺、库里其实有」。

**已核对的本地结果（2026-11-13）**：`pytest` 全量 1323 项通过、13 项跳过（含 `-m db`，用本地 PostgreSQL 16 跑真库用例），其中 E2 `test_discovery_fetch.py` 42 项、`test_discovery_runner_db.py` 31 项、`test_discovery_http.py` 24 项、`test_discovery_metadata.py` 11 项、`test_e2_draft.py` 8 项；`contracts/check_e1_draft.py`、`check_e2_draft.py` 与 `generate_types.py --check` 均通过。框架页、跨站同文、同 URL 短句变化、http-only 附件四组用例在对应旧实现上先红后绿（`70e00d5` / `00c02f1`）；把 `runner.py`/`routes/discovery.py`/`schemas.py` 回退到 `24daee0` 后，本轮新增的跨站短句差异、条款顺序、候选保留与列表/详情提示四条用例失败，恢复后全绿；整页哈希完全相同仍自动去重那条在旧实现上也通过，用作回归护栏。**云端尚未部署 E2、云端库尚未执行 010**。

## 5. 一次完整联调如何传参

以下 ID/事实仅用于结构演示；政策须先通过后台导入、校验和发布，不能直接把示例 ID 当现有数据。

```json
{
  "nameSynthetic": "合成企业A",
  "period": {"startDate":"2026-01-01","endDate":"2026-12-31"},
  "facts": [
    {"key":"region","value":"440300","unit":"code","period":{"startDate":"2026-01-01","endDate":"2026-12-31"},"source":"user_input"},
    {"key":"industry","value":"C39","unit":"code","period":{"startDate":"2026-01-01","endDate":"2026-12-31"},"source":"user_input"},
    {"key":"taxableIncome","value":null,"unit":"CNY","period":{"startDate":"2026-01-01","endDate":"2026-12-31"},"source":"user_input"}
  ]
}
```

1. API-08 得到 enterprise.id 和 ETag；API-02 得到已发布 policyId。
2. API-11 提交 `{"enterpriseId":"创建返回的UUID","policyIds":["列表返回的UUID"],"asOf":"2026-10-23T10:00:00+08:00","period":{"startDate":"2026-01-01","endDate":"2026-12-31"}}`。若该政策依赖 taxableIncome，返回 evidence 不足与 nextQuestion.factRef。
3. 回答收入值 `"2800000.00"`。对 `/enterprises/{id}/facts/taxableIncome` 发 PATCH；头 If-Match **原样使用 GET/POST 响应 ETag**；body `{"value":"2800000.00","unit":"CNY","period":{"startDate":"2026-01-01","endDate":"2026-12-31"},"source":"user_input"}`。
4. 收到新版本后 API-11 使用原参数并加 `supersedesRunId=旧run.id`；后端重算相关条件。若人数等还未知，仍可能证据不足，不能保证答一个问题就满足。
5. API-12 查看旧 run：历史四态不改，相关 assessment 的 validity=stale，原因 fact_changed；查看新 run 显示新快照。API-13 下载；API-14 需额外管理授权。
6. 缺 If-Match 返回428；另一成员先改了事实则409。前端显示错误 message 和 traceId，保留输入并重新 GET，不覆盖对方变更。

管理流程：`import → GET admin detail → extract(If-Match, Idempotency-Key, version) → PUT criteria(新的 If-Match, version) → publish(新的 If-Match, version, reviewConfirmed=true)`。每一步成功更新 revision；不能拿最初导入的 ETag 一直传下去。

## 6. 模块调用与前端组件边界

以下是待实现函数的目标签名，内部可用 dataclass/Pydantic，但不得私自改变含义：

| 文件 | 调用约定 | 输出/约束 |
| --- | --- | --- |
| `agents/orchestrator.py` | `assess(input: AssessmentInput, context: AgentContext)` | `AssessmentRun`，调 rules/questioning/explanation 并保存 run |
| `agents/profile.py` | `normalize_profile(input: EnterpriseInput)` | 已验证但不猜值的事实；不自行认证来源 |
| `domain/rules.py` | `evaluate_policy(policy, enterprise, period, as_of)` | 条件结果、四态、依赖与计算过程，不调模型 |
| `domain/citations.py` | `validate_criteria(criteria, stored_sources, version)` | 每条 validationStatus 和 findings，服务端计算 |
| `domain/questioning.py` | `choose_next_question(assessments, skipped_fact_refs)` | 一个 question 或 null，稳定排序 |
| `domain/versioning.py` | `invalidate_dependents(change)`、`resolve_validity(snapshot, events, now)` | 原子变更事件和读取时有效性，保留历史 |
| `agents/explanation.py` | `explain(evaluations, approved_preparation, context)` | 引用说明、两类清单与模式，不能产生最终四态/自行选问题 |
| `integrations/storage.py` | 政策/企业快照、条件依赖、run、失效事件和轨迹读写 | 一个 DB 事务处理版本校验与变更，无 SQL 拼接 |
| `integrations/cache.py` | `get/set` 版本化求值缓存 | 命中后仍检查有效性；只复用计算，不复用 runId |
| `api/client.ts` | `listPolicies/getPolicy/createEnterprise/getEnterprise/updateFact/createRun/getRun/downloadReport/getTrace/listAdminPolicies/getAdminPolicy/importPolicy/extractPolicy/saveCriteria/publishPolicy` | 页面唯一 HTTP 出口；JSON/文本分流；暴露 ETag/traceId |

前端函数路径/查询值均 URL 编码；client 写请求分开传 body 与 headers，不把 version 或 ETag 放错位置。GET 不发送 body。所有返回先校验关键结构，未知枚举展示契约错误，不默认为满足。

组件 Props：`StatusBadge({result,validity,policyStatus})`；`EvidenceRow({criterionResult,sources,facts})`；`NextQuestion({question,onAnswer(value),onSkip(),pending})`；`PreparationList({preparation,policyStatus,validity})`。组件不访问数据库/模型服务、不推导政策阈值；页面负责调 client 和管理请求状态。Report/Trace 页面以 runId 读取，不以当前 enterpriseId 重新拼装旧报告。

## 7. 本次代码核对与修改记录

检查发现所有 Python/TS/TSX/SQL/Compose 都是用途注释，package.json 没有依赖或启动脚本。因此不能评价尚不存在的业务实现，也不能宣称接口已可请求。本次只把阻碍协作的契约和骨架说明修正为可实现的同一套约定：

| 原问题 | 本次处理 |
| --- | --- |
| openapi.yaml 只有三行注释，没有参数/响应定义 | 补成 OpenAPI 3.1 草案，覆盖 16 个端点与共享 schema；保留待实现声明 |
| 前端 types.ts 无类型，各成员容易各写一套字段 | 补齐按 OpenAPI 对齐的 TypeScript 数据结构；未实现 HTTP client 或页面 |
| 后台只有写接口，无法从待审列表/详情恢复审核 | 增 API-15/16；明确公开与后台的数据权限 |
| version 与 If-Match 混用，extract/publish 目标版本不明 | 分开 policy version、draft revision、factVersion，写接口显式携带目标版本 |
| orchestrator 注释声称 context 包含未定义业务字段；explanation 与 questioning 都能产问题 | 业务 input 与 context 分开，问题仅由 questioning 排序，explanation 负责文字 |
| “run不可变”与“标记旧run失效”冲突；缓存键漏期间/时点 | 历史快照与有效性读取视图分开，补依赖事件与完整缓存键 |
| 多期间事实仅 factKey 定位不充分；季度数据格式未定 | body.period 明确定位，补事实字典与季度八输入约束 |
| 准备清单/模式/冲突来源无完整响应字段 | 补统一响应对象、冲突多引用、解释降级字段 |

配套更新 README、AGENT_PROTOCOL 和直接相关注释；增加 `contracts/generate_types.py`，从 OpenAPI 生成前端类型，防止手工重复定义漂移。未改旧策划/需求/分工原文。契约检查测试可独立执行，后端业务/数据库/云端/浏览器验收仍待成员实现。首次编写时尚未建立 Git 仓库；后续仓库操作以项目工作手册和实际 Git 记录为准。

## 8. 验证清单与交付步骤

本次静态检查：OpenAPI 可解析、引用可解析、16 个 method/path 完整、请求响应示例符合 schema、TS 字段与 schema 对齐、涉及路径存在。命令和实际结果见本节末尾验证记录；不能把这些算成业务通过率。

M1 实现后补：API TestClient 的成功/失败/错误头、multipart 超限/伪文件、未授权后台读取、If-Match 缺失/过期、抽取幂等、发布冲突、按 period 更新、Decimal 边界、季度缺一输入、OR/NOT/冲突传播、局部失效、模型降级、数据库失败和缓存隔离。**截至 2026-10-26 均已覆盖**：缓存隔离见 `tests/test_cache.py`（七字段换键、时点等价、值损坏/版本不符/结论不自洽按未命中、命中仍新分配 runId 且有效性重算、事实变更换键、缺客户端与读写报错绕过、数据库故障 503 不被掩盖）。真实 Redis 上的键写入、TTL 与命中见 `tests/test_cache_redis_live.py`（`HUICE_TEST_REDIS_URL` 门控，本地 Redis 7.4 实测 3 项通过）；**未覆盖**：云端部署后的缓存命中——云端 API 尚未部署。其余见下方第三阶段与第四阶段记录。

M2 实现后补：画像 → 列表 → 详情 → 补问 → 旧结果失效 → 新报告；后台列表/重入编辑/并发冲突；报告错误 JSON 与成功 Markdown；Cookie/CORS/ETag 可读；不明枚举可见报错；closed/stale 没有申报按钮。M3 为每项记录真实运行 ID、输入、预期、实际、截图与复测结论，指标只填实测。

实现交接：M3 先交 source_register/criteria_review/样本；M1 实现契约与规则并给真实脱敏响应；M2 可用严格相同字段的合成 mock 开发，接真 API 时关闭 mock 并标明模式；三方核对关键中文语义后冻结，不将此次草案视为自动签收。

### 本次实际验证记录（2026-10-23）

本机工具为 Python 3.13.5、PyYAML 6.0.2、jsonschema 4.23.0；这是静态检查工具环境，**项目运行基线仍按原计划使用 Python 3.12**，尚未验证3.12部署。缺少工具时可由 M1 安装这两个测试依赖，后续统一纳入锁定文件。

从 `huice/` 执行：

```powershell
python contracts/generate_types.py --check
python -m unittest discover -s services/api/tests -p test_contract.py -v
```

已执行：类型一致性检查通过；8 项静态测试全部通过，覆盖16个端点清单、本地引用解析、67个schema的结构检查及合成示例、TS生成一致性、事实零值/未知/精度/来源边界、AST和来源定位、判断请求限制、管理权限/并发头与报告媒体类型。检查使用 JSON Schema 2020-12 校验器，不等同于完整 OpenAPI 专用校验器；额外语义（如开始日期小于结束日期、真实官方域名、数据库并发）仍须后端实现和集成测试。

本节只覆盖静态契约检查：业务 API、规则求值、数据库与 Redis/OBS、浏览器与 TypeScript 编译**不能由静态测试代替**，其执行结果见下文「M1 后端实现验证记录」。MaaS 不在范围内（队长 2026-10-23 决定），模型调用改用 DeepSeek。

### M1 后端实现验证记录（2026-10-23，第二阶段）

运行环境：Windows 11（10.0.26200）、CPython 3.12.10（`services/api/.venv`）、Git 2.54.0.windows.1、Node v24.15.0（与计划的 Node 22 LTS 不符，记录为偏差，M1 后端不受影响）、WSL 3.0.3.0（默认版本 2）、Docker Desktop 4.96.3（CLI 29.9.2、Compose v5.6.1）。依赖版本见 `services/api/requirements.lock`（47 项，由 `pip freeze` 生成；在 41 项基础上加入 `esdk-obs-python==3.26.6` 及 5 个传递依赖，用于 OBS 对象存储）。

从 `huice/services/api` 执行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

已执行并通过：166 项用例通过、12 项数据库用例跳过（见下）、75 项契约子测试通过（含当日稍后补入的 16 项 OBS 对象存储用例与 3 项 OBS 配置用例：键命名空间、endpoint 归一化、非 2xx 状态与 SDK 异常统一映射 503、异常消息不含桶名与 AK/SK、客户端惰性创建与关闭后重建；以及 16 项 DeepSeek 模型客户端用例：`/chat/completions` 请求形状与 Bearer 头、base URL 归一化、usage 解析、429→`UPSTREAM_RATE_LIMITED`、其余状态/坏 JSON/缺字段/网络异常→503 且消息不含密钥与 base URL、`fixture`/`deepseek` 模式分派、缺 `DEEPSEEK_API_KEY` 时启动即失败）。覆盖配置加载（缺变量只报变量名、模式必填、CORS/ADMIN_TOKEN 基线）、统一错误信封与 `X-Trace-Id`、404/500 脱敏、CORS 预检与暴露头、Bearer 401/演示 cookie 403、API-04 全部分支（metadata 缺失/坏 JSON/字段与枚举/日期与窗口倒置/官方 URL/文件数量/10 MiB 边界/空文件/二进制/坏 PDF/409/503 清理）、文件摄取单元规则、API-15/16 读取与游标签名，以及服务实际暴露的4个接口与契约的 operationId/summary/tags/security 对齐。

真实服务冒烟：按 `README.md` 启动 uvicorn（读取 `huice/.env`）后 `curl.exe -i http://127.0.0.1:8000/api/v1/health` 返回 503、`{"error":{"code":"DEPENDENCY_UNAVAILABLE",...}}`、`X-Trace-Id` 与 body `traceId` 一致（本机无数据库，502/503 属预期）；进程能完成 startup，说明配置加载与中间件装配正常。依赖锁定复现：在 `%TEMP%` 干净 venv 中 `pip install -r requirements.lock` 后 `pip check` 输出 `No broken requirements found.`。

契约：`python contracts/generate_types.py --check` 一致；`test_contract.py` 8 项通过（`types.ts` 无需改动）。为让 `format: date-time` 检查真正生效，dev 依赖补入 `rfc3339-validator==0.1.4`；此前该环境下“日期时间缺时区”用例会误判为通过。

**数据库与 Redis 实测（2026-10-23 当日补齐）**：本机先完成 WSL2（3.0.3.0，默认版本 2）与 Docker Desktop 4.96.3 安装，用 `infra/local/docker-compose.yml` 起 PostgreSQL 16.15 + pgvector 0.8.7 与 Redis 7.4。`infra/sql/001_init.sql` 连跑两次退出码均为 0（第二次仅 `NOTICE ... already exists`），得到 13 张业务表、13 个业务触发器（云端 RDS 于 2026-10-24 复测为 `public` 下 `pg_trigger` 全量 61 行，其中 48 行为外键内部触发器；本地库尚未重测，旧记录的「24 行」为不同查询口径）、`schema_migration` 记 `001_init`；`redis-cli PING` 返回 `PONG`；`pytest -m db -q` 在真实库上 **12 项通过**（导入落库行数、新版本复用 policyId、重复版本无部分写入、分页与派生 `closed`、已发布版本与其子表不可改删、只追加表拒改）。

端到端冒烟（真实库）：API-04 导入返回 201、`ETag` 与 body `revision` 一致、`status=pending_parse`、来源 `kind=body`；API-15 列表 200、无 `ETag`、顺序为 `imported_at DESC`；API-16 带 `version` 与不带 `version` 均命中同一 `revision`；未知 `version` 返回 404 `POLICY_NOT_FOUND` 且不回退。冒烟数据已清理。首轮 `pytest -m db` 曾 10 项失败，暴露一个真实缺陷（`_fetchone()` 误用于无 `RETURNING` 的 `INSERT ... ON CONFLICT DO NOTHING`，已改用 `_execute()`）与两处用例问题（用例间数据串号、把「同版本即冲突」写错，后者已按契约语义修正为同一 `policyId` 下重复才 409）。原始记录见 `docs/devlog/M1.md` 与 `infra/local/README.md`。

**云端资源（2026-10-23 当日补齐状态）**

| 资源 | 状态 | 证据 |
| --- | --- | --- |
| ECS | 已开通并登录 | `huice-api-01`，Ubuntu 24.04.4、Python 3.12.3、内网 `192.168.0.86`，22 端口实测可连 |
| RDS for PostgreSQL | **已实测（2026-10-24 复测；2026-10-26 补齐迁移）** | 16.13，内网 `192.168.0.80`，库 `huice`；`001_init.sql` 执行成功，13 张表 / 13 个业务触发器（`public` 下 `pg_trigger` 全量 61 行，其中 48 行为外键内部触发器），`vector` 0.8.0（本地 0.8.7）；`schema_migration` 三行齐全（`001_init` 10-23 17:37、`002_evaluation_and_idempotency` 10-26 23:47:03、`003_trace_step_details` 10-26 23:47:13，`applied_at` 显示 `+08` 即实例默认 `Asia/Shanghai`；`004`—`006` 已于 2026-11-02 补齐、`007_idempotency_usage_semantics` 已于 2026-11-05 补跑，`008_material_requirements` 亦已在库（来源 PR #37 的 `351c037`，本文件无其执行记录），`schema_migration` 八行；`usage_semantics` 列 `NOT NULL DEFAULT 'legacy'`、`idempotency_record_usage_semantics_check` 就位，既有 1 条记录落 `legacy`；云端代码已于 2026-11-06 换为 rc.7（`112d0a7`），此前为「rc.6 + 提示词热补丁」，先迁移后升级）；`trace_step` 六列与 `trace_step_status_check`/`trace_step_stage_check`、`trace_step_no_update_delete` 均已核对；`policy` 与 `policy_version` 仍 0 行 |
| Redis（DCS） | **已实测** | `huice-redis` 主备 1GB，小版本 `7.2.11.5`，内网 `192.168.0.38:6379`，`AUTH`→`OK`、`PING`→`PONG`；2026-10-24 复测时实例未开启（端口拒绝），2026-10-26 在 ECS 上复测恢复：端口可连、`--askpass PING` 返回 `PONG`、白名单已含 ECS `192.168.0.86`。判断链路的版本化求值缓存已实现（2026-10-26），`redis==8.1.0` 已入 `requirements.lock`，部署时填好 `REDIS_URL` 即真实读写；云端 API 尚未部署，该项在云端的命中待部署后复测 |
| OBS | **已实测（2026-10-27，真实桶）** | 桶 `huice-policies`（私有，`cn-south-1`）；`ObsObjectStore`（`esdk-obs-python` 3.26.6）在真实桶上 `-m obs` 5 项通过：写入→独立客户端读回字节、删除后 404、两次写入键不冲突、导入正文真正落桶、不可写桶 503 且不泄露凭证（命令与输出见 `infra/README.md` §4.3） |
| DeepSeek | 已接入，**2026-10-27 用真实 Key 联调通过**（`tests/test_deepseek_live.py` 3 项：补全返回文本/用量、错误 Key→503 不回显、整链 `explanationMode=deepseek` 而结论仍由规则给出） | `app/integrations/llm.py`（`fixture`/`deepseek` 两模式，OpenAI 兼容 `/chat/completions`）；契约 rc.2 中 `ModelUsage.mode`/`explanationMode` 的 `maas` 已改为 `deepseek`；`MODEL_MODE` 默认值仍为 `fixture`，由 `.env` 切换 |
| CodeArts / Skills | **不作为要求** | 账号据本人告知已开通、未接入；队长 2026-10-23 同意开发记录以实际使用的工具（Claude Code，见 `docs/devlog/M1.md`）为准 |

**仍未执行**（未执行即不计作通过）：

- 云端 API 部署（`infra/deploy/` 的 systemd/nginx 文件已就绪，尚未在 ECS 上执行），因此云端健康检查、`app→RDS` 内网链路尚未有实测证据
- 云端部署时切到 `OBJECT_STORE_MODE=obs` 并复测一次导入（真实桶联调已于 2026-10-27 在本地完成 5 项，见上表与 `infra/README.md` §4.3）
- `restricted` 模式的网关侧会话下发与绕过拦截：后端已改为校验签名与有效期的 cookie（2026-10-24 按 M2 审查 R1 修复），但网关那层尚未配置和验收
- 前端 TypeScript 编译与浏览器验收（属 M2）
- 11/15 要求的「从空环境复现部署」，需先用已推送的提交重做一遍

### M1 后端实现验证记录（2026-10-26，第三阶段：条件 AST、引用校验、发布与基础匹配）

运行环境同前（Windows 11 `10.0.26200`、CPython 3.12.10、`services/api/.venv`、本地 PostgreSQL 16.15 + pgvector 0.8.7）。

接口实现状态：契约 16 个端点**全部实现**——API-01（健康检查）、API-02/03（公开政策列表与详情）、API-04/05/06/07（导入、抽取、保存条款、发布）、API-08/09/10（企业画像与 `factVersion` CAS）、API-11/12（判断运行与读取）、API-13（Markdown 报告）、API-14（轨迹）、API-15/16（后台草稿读取）。API-13/14 只读既有历史：报告是 run 快照的转写并在正文前单列「截至下载时刻」的有效性，轨迹先列入库/审核步骤再列在线步骤。

从 `huice/services/api` 执行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q          # 439 passed，含 75 项契约子测试
.\.venv\Scripts\python.exe -m pytest -q -m db    # 33 passed（真实 PostgreSQL 16.15）
```

覆盖的验收口径：

- **行业排除 → 不满足**：`industry_exclusion` 命中排除名单时结果 `not_satisfied`，`criteriaResults` 带引用与计算过程（`test_agent_flow.py`、`test_rules.py`、`test_db_evaluation.py`）。
- **缺事实 → 证据不足**：`value=null` 与「事实未提供」都得到 `insufficient_evidence`，`missingFacts` 给出 `FactRef`，全局 `nextQuestion` 只挂在受影响政策上且 `rank=1`（`test_agent_flow.py`、`test_questioning.py`）。
- **未发布规则不能参与判断**：API-11 整体 422 `POLICY_NOT_PUBLISHED`，run 不落库（`test_agent_flow.py`、`test_db_evaluation.py`）。
- **失效视图**：改事实只让读到该事实的条件/政策变 `stale`（`fact_changed` + `changedCriterionIds`/`changedFactRefs`）；发布新版本使依赖旧版本的 run 变 `stale`（`policy_changed`）；历史四态与快照不变（`test_invalidation.py`、`test_db_evaluation.py`）。
- **并发与幂等**：`factVersion` 走 compare-and-swap（缺 `If-Match` 428、`*`/弱标签/畸形 400、过期 409 + `currentVersion`）；抽取按 `Idempotency-Key` 复用已成功结果（`test_enterprise_http.py`、`test_admin_review_http.py`）。
- **契约对齐**：全部 16 个端点与契约的 operationId/summary/tags/security 逐项比对（`test_openapi_alignment.py`），`generate_types.py --check` 一致、`test_contract.py` 8 项通过。
- **报告与轨迹**：报告为 UTF-8 Markdown + `Content-Disposition: attachment`，四态/证据/清单原样转写并单列下载时刻的有效性（`test_report_trace_http.py`）；run 未成功时报告 409 `RUN_NOT_COMPLETED`（`details.status`）；轨迹按身份三通道（企业会话看本人 run、审核员与运维 Bearer 看全部、demo 看无主 run，他人 run 404），先列入库/审核步骤、再列在线步骤，`sequence` 重排为严格递增，`policyId`/`policyVersion` 由版本引用回填（`test_report_trace_http.py`、`test_db_evaluation.py`）。

本阶段连真库才暴露、并已修复的缺陷（详见 `docs/devlog/M1.md`）：

1. `infra/sql/002_evaluation_and_idempotency.sql` 用了 `ALTER TABLE trace_step DROP TRIGGER IF EXISTS ...`，不是合法语法，整份迁移回滚。改为独立的 `DROP TRIGGER IF EXISTS trace_step_no_update_delete ON trace_step;`，并验证连跑两遍通过。同日新增 `003_trace_step_details.sql`（`trace_step` 的输入/输出摘要与政策关联列、阶段枚举收口），同样连跑两遍通过。
2. `create_policy_version` 写入 `trace_step.status='succeeded'`，与 002 后的 `AgentStatus` 约束冲突，已改为 `ok`。
3. `PolicyRepository._fetchone()` 未使用 `dict_row`，按列名取值时抛 `TypeError`，影响所有读库路径（导入后的读回、企业画像、run 读取）。
4. `ValidityView.add` 输出 `criterionIds`/`factRefs`，而契约与 Pydantic 模型（`extra="forbid"`）要求 `changedCriterionIds`/`changedFactRefs`，会使 API-12 直接 500。
5. `orchestrator.assess` 调 `save_run` 时漏传必需的 `policy_version_ids`（运行期 `TypeError`）。

**如实记录的偏差与待办**：

- `preparationTemplate.items` 恒为 `[]`（政策详情没有企业上下文，四态给不出来），企业事实一律 `verified:false`：材料要求本身已按 M3 审核过的 CSV 导入（`review_status=已仲裁`），未由模型或样例编造；带四态的条目只在判断运行的 `preparation.items` 里出现。
- `supersedesRunId` 已校验同企业/同期间/同政策组并记录关联，但重算仍是完整的，尚未按依赖图跳过未受影响条件（契约语义已满足，性能优化后置）。
- 金额在线上固定两位小数（`"2800000.00"`、`"0.00"`），而 `factVersion` 的哈希会去掉尾随零——同一个内容不会因写法不同而换版本。
- `closed`、`validity`、`staleReasons`、`validUntil` 都是读取时视图，不回写历史结果。

### M1 后端实现验证记录（2026-10-26，第四阶段：Redis 版本化求值缓存）

运行环境同前。`app/integrations/cache.py` 从占位改为实现，`orchestrator.assess` 接入逐政策取/存，`main.py` 装配并在 lifespan 关闭。

从 `huice/services/api` 执行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q                                  # 433 passed / 45 skipped（34 db + 3 redis + 5 obs + 3 deepseek 门控，2026-10-27 第六阶段口径）
$env:HUICE_TEST_DATABASE_URL = "<本地库连接串>"
$env:HUICE_TEST_REDIS_URL = "redis://127.0.0.1:6379/0"
.\.venv\Scripts\python.exe -m pytest -q                                  # 470 passed / 8 skipped（含 34 db + 3 真实 Redis）
.\.venv\Scripts\python.exe -m pytest -q -m redis                         # 3 passed
```

覆盖的验收口径（`tests/test_cache.py` 21 项 + `tests/test_cache_redis_live.py` 3 项）：

- **键不漏项**：七字段任一变化（企业、政策、政策版本、事实版本、规则版本、期间、判断时点）即换键；同一时刻的不同时区写法得到同一键，真实时刻不同则换键。
- **命中只复用求值**：第二次请求 runId 不同、`validity` 仍按当前时间重算、条件结果与首次一致；轨迹 `evaluation` 摘要写明「其中 k/n 项复用缓存求值」。
- **缓存值不承载历史**：投影里没有 runId、`validity/staleReasons/validUntil`、解释文本和条件 UUID；恢复按当前版本条件顺序回填，数量/版本/聚合结论不自洽一律按未命中重算。
- **Redis 不可用即绕过**：缺客户端、读或写报错、端口拒绝（真实客户端连 `127.0.0.1:1`）都只记一次告警（不含连接串）后重算，不再反复撞同一故障；未配置 `REDIS_URL` 时使用直通替身。
- **数据库不可用仍是 503**：仓储失败时不进缓存、不被缓存掩盖。
- **真实 Redis**（`test_cache_redis_live.py`，本地 compose 的 Redis 7.4）：首次判断写入 1 个 `huice:eval:v1:*` 键且 `0 < TTL ≤ 300`，缓存值里没有 `runId`/`validity`/`explanation`；二次请求命中、不开新键、runId 仍不同；事实变更后新增第 2 个键。

如实记录的偏差与待办：

- `redis==8.1.0` 已按负责人 2026-10-26 的决定加入 `pyproject.toml` 与 `requirements.lock`（48 项），并在干净 venv 中 `pip install -r requirements.lock` + `pip check` 复现通过；`redis` 为纯 Python 包，无新增传递依赖。
- 云端 Redis 连通性已于 2026-10-26 在 ECS 上复测：端口可连、`--askpass PING` 返回 `PONG`、控制台显示小版本 `7.2.11.5`、白名单已含 ECS `192.168.0.86`（2026-10-24 的「实例未开启」已不复现）。`192.168.0.38` 是 VPC 内网地址，本机直连超时属正常。**仍未实测**：云端缓存命中——云端 API 尚未部署（11/08 任务），部署后按同一组用例复测即可。
- 缓存是加速手段而非证据库：失效裁定仍以数据库 `invalidation_event` 为准，只换缓存键不能代替失效记录。
- 云端 RDS 已于 2026-10-26 23:47 补齐 `002` 与 `003`（`schema_migration` 三行、结构与本地一致）；云端 `app→RDS` 链路仍未实测，等 11/08 部署。

### M1 后端实现验证记录（2026-10-27，第五阶段：外部依赖联调与阶段交付）

除上表四个阶段外，同日完成两件事：把 11/03 的两项外部依赖接真（OBS 桶、DeepSeek Key），以及把 10/28 与 11/03 两阶段的成果提交并推送。

从 `huice/services/api` 执行（凭证只在本机终端以环境变量形式存在，不写文件）：

```powershell
.\.venv\Scripts\python.exe -m pytest -q          # 427 passed / 44 skipped / 75 subtests
# OBS：HUICE_TEST_OBS_BUCKET / ENDPOINT / AK / SK 四项齐备才运行
.\.venv\Scripts\python.exe -m pytest -q -m obs        # 5 passed（真实桶 huice-policies）
# DeepSeek：HUICE_TEST_DEEPSEEK_API_KEY 存在才运行
.\.venv\Scripts\python.exe -m pytest -q -m deepseek   # 3 passed（真实 Key）
```

覆盖的验收口径（`tests/test_obs_live.py` 5 项 + `tests/test_deepseek_live.py` 3 项）：

- **OBS 写入是真的**：经 `ObsObjectStore` 写入后，用**独立构造的 SDK 客户端**读回字节逐一比对；删除后 `getObject` 返回 404；同内容两次写入得到不同键且都还在。
- **导入链路落桶**：`POST /admin/policies/import` 的正文真正上传到桶，且仓储入参 `storage_keys` 与桶内键一致（契约的 `SourceDocument` 不暴露内部键，因此断言取仓储入参而非响应体）。
- **失败不泄露**：桶不可写时统一 503 `DEPENDENCY_UNAVAILABLE`，异常与详情里不含桶名、AK、SK。
- **模型只改解释**：真实补全返回文本/模型名/用量；整链判断里 `explanationMode=deepseek`，而 `result` 仍是规则求值给出的 `not_satisfied`——模型无法改写结论。
- **错误 Key 走降级**：401 等非 2xx 映射为 503，消息不含 Key 与 base URL。

如实记录的偏差与待办：

- 首版用例把带 AK/SK 的 `Settings` 当夹具返回，一次断言失败使 pytest 把密钥打进终端；该对密钥当日作废重建，用例改为 `Credentials` 数据类（`__repr__` 输出 `<redacted>`）且带密钥的 `Settings` 只在辅助函数内临时构造，`infra/README.md` §4.3 注明不要对这两个 marker 用 `--showlocals`。
- 首版导入用例断言 `sources[0]["storageKey"]`，契约中并无该字段（`KeyError`），失败时在桶里留下 1 个孤儿对象（已在控制台删除）；现改为从仓储入参断言，并给真存储套一层记录型包装器，请求中途失败也能清理。
- 提交：单次提交 `88d8eec`（52 文件），推送 `origin m1/backend-eval-core`，PR #8（base `main`）；两阶段共用同一批文件，拆开会产生跑不过测试的中间提交，原因已写入 devlog。
- `MODEL_MODE` 默认值仍为 `fixture`；切到 `deepseek` 由 `.env` 决定，云端切换随 11/08 部署验证。

### M1 后端实现验证记录（2026-10-27，第六阶段：M2 复核发现的正确性问题）

M2 复核 PR #8 时提出两处正确性问题，均已修复并补回归用例。

**① 失效没有按期间区分（`domain/versioning.py`）**。写入端 `_INSERT_FACT_EVENTS` 本来是按 `fact_key + fact_period_start/end` 命中依赖的，但事件行只存了 `fact_key`，读取端重算有效性时只能按键匹配。于是同一个键的两个期间（年度与季度、前后两个年度）里改动一条，会把只读另一条的条件也标成 stale，`changedFactRefs` 还会列出没被改的期间。修复：迁移 `infra/sql/004_invalidation_event_period.sql` 给 `invalidation_event` 补 `fact_period_start/fact_period_end` 与配对约束；`ChangedEvent` 增 `fact_period`；读取端按 key + 期间 精确匹配，`004` 之前的旧行（期间为空）按整键保守匹配，宁可多标 stale，不可漏标。回归用例：`tests/test_invalidation.py` 3 项（精确期间、一条条件读两个期间、旧事件保守匹配）与 `tests/test_db_evaluation.py::test_fact_change_is_scoped_to_the_exact_period`（真库，同一键两个期间）。

**② AST 200 节点上限只算单层（`domain/rules.py`）**。原实现对每个 `and/or` 各自检查直接子项数 ≤ 200，嵌套后总节点数仍可指数增长。修复：`validate_expression` 先用显式栈遍历整棵表达式累计节点数（超限即停，不做完整遍历），超过 `MAX_NODES` 直接返回 `EXPRESSION_TOO_LARGE`；单层子项上限随之移除（已被整树预算覆盖）。回归用例：`tests/test_rules.py` 3 项（满扇出浅树超限、200/201 边界、300 层 `not` 长链不触发递归错误）。

验证（2026-10-27，本机 PostgreSQL 16 + pgvector 0.8.7 / Redis 7.4）：`pytest -q`（无外部依赖）**433 passed / 45 skipped / 75 subtests**；`pytest -m db` **34 passed**（含新增的同键两期间用例）；`HUICE_TEST_DATABASE_URL` 与 `HUICE_TEST_REDIS_URL` 都指向本地服务时全量 **470 passed / 8 skipped**（余下 8 项为 OBS 5 项与 DeepSeek 3 项门控，需真实凭证）。迁移 `004` 在本地库连跑两遍退出码均 0，第二遍仅 `NOTICE ... already exists, skipping`；核对结果：`invalidation_event` 增 `fact_period_start/fact_period_end` 两列、`invalidation_event_fact_period_check` 约束、`invalidation_event_run_idx (run_id, created_at)` 索引，`schema_migration` 四行齐全。云端 RDS 仍未执行 `004`，随 11/08 部署补齐。**未执行**：OBS/DeepSeek 用例本轮未重跑（改动不涉及这两条链路）。

### M1 后端实现验证记录（2026-11-05，评测输入快照）

为 `EVALUATION_FREEZE` §5.2 补输入侧工具（`services/api/app/evaluation/`，见 2.9）。新增 `tests/test_eval_snapshot.py` 27 项：纯函数 17 项锁住哈希口径（键序、`Decimal` 尾零、日期 ↔ ISO 串等价、`null` ≠ 空串、配置白名单挡密钥）与差异比较（忽略采集时间与签认后新增的数据集文件，值/配置/数据集/缺失行都能报出）；CLI 6 项（缺 `DATABASE_URL` 只报变量名、坏清单被拒、数据集目录不存在先于连库报错、只给 `--dataset-dir` 被拒、列出的文件不存在时点名）；`db` 标记 5 项在真库上验「清单的事实版本 == 引擎落库的 `fact_version`」「改事实值后 `verify` 非零退出且不改动清单」「清单文本里没有任何口令/令牌/连接串」「只哈希 `--dataset-file` 列出的文件、同目录的复核表不进清单」「两侧 `codeCommit` 皆 null 时提示代码身份未证明」。后两项按 M3 在 PR #39 的复核改的：目录扫 `*.csv` 会把材料复核表冻结进去，材料一改就误报；双 `null` 曾静默通过。


**M2 在 #39 复审的三项（同日改）**：① `verify` 原先把 `reviewFinding` 等会改变判断结果的字段漏在比较之外——改为按字段表逐个比较（见 2.9），并加一条逐叶子字段单点变异的用例，加字段忘了进比较会先转红；② 数据集哈希与库快照并列却不证明二者映射一致——新增 `dataset_map.py`（`content-alignment@1`）做逐条对齐，清单记 `datasetMapping`，未解决项一律非零退出，`tests/test_eval_dataset_map.py` 覆盖匹配/值不同/verified/表达式变/双向未覆盖/同名歧义/无文号回退标题/空表达式/缺列/空值对齐/空表（现 20 项）；③ 快照四条查询改为同一 `REPEATABLE READ, READ ONLY` 事务，真库用例验隔离级别与只读、并用中途提交的写入证明不会混入。
**M2 第二轮复审的两项（2026-11-06 改）**：① 不给数据集时 `datasetMapping` 为 `null`，`verify` 对两个 `null` 直接放行，等于「从没核对过 CSV 也算一致」——现在 `inputs`/`verify` 默认要求三份评测输入齐全（缺哪份点名哪份，非零退出），只比数据库必须显式加 `--no-dataset`，两个模式的清单不能互相冒充核对；映射里缺文件、空表（只有表头）、条目一条都没对上，一律记成未解决问题；② CSV 空值与库 `NULL` 同为「未知」，原先 CSV 侧还对 `None` 算哈希，与清单里的 `valueHash: null` 永远对不上——已统一为 `null`，并补「未知 ≠ 0」的用例。新增 CLI 回归：无 `--dataset-file`、只给一份、`--no-dataset` 与数据集文件同时给、只比数据库的清单不带开关核对、带映射的清单硬加 `--no-dataset`、三份 CSV 各自清空成只有表头后 `verify` 非零。

**§5.2 第 4 项「结果导出」（2026-11-05，按 M3 口径）**：`freeze-report` 把下载的 `report.md` 裁成不含有效性段的冻结产物。`tests/test_eval_report_freeze.py` 6 项：剔除干净（下载时间 / 有效性段 / 有效性列 / 失效原因 / 有效期至全部消失，条件级内容与办理清单保留）、同一 run 换下载时刻与失效状态后冻结字节一致、已裁过或手工改过的输入被拒（缺标题、缺有效性段）、摘要表列数异常被拒、用**真实渲染器**输出验证剔除口径（前提断言原始报告确实带有效性段）、CLI 写出文件并回报两侧哈希与下载时间。

**M2 第三轮复审的一项（2026-11-06 改，P1）**：清单只记文件名的哈希，`verify` 再按文件名拼 `--dataset-dir`——三份输入分散在 `data/evaluation`、`data/policies`、`data/fixtures`，从仓库根用三个相对路径签认、换个目录核对就会去找错地方，同名文件还会互相顶替。现在清单记**签认路径**（项目内相对项目根，项目外绝对），`verify` 只按这些路径读、拒收 `--dataset-file`/`--dataset-dir`；同一文件签认两次、两个不同目录的同名文件一起签，都直接拒绝。新增用例：仓库三目录布局下签认后**换工作目录**核对、签认文件被改/被删、命令行重新点名被拒、同一个文件签两次、两个同名文件。验证（本机 Docker）：全量 **782 passed / 11 skipped / 77 subtests**；`tests/test_eval_snapshot.py` 与 `tests/test_eval_dataset_map.py` 合跑 **70 passed**。另外用真实 `data/` 三份文件在本机临时库实跑过 `inputs → verify`：清单记的是 `data/evaluation/labels.csv`、`data/fixtures/enterprises.csv`、`data/policies/criteria_review.csv` 三条相对路径，换到仓库根核对仍读得到，传 `--dataset-file` 被拒。

**部署文件指纹（2026-11-06，按 M2 在 #39 的「单独 PR 跟进」落地）**：`codeCommit` 人工传入或两侧皆空都证明不了部署物身份（M2 在 #36 的判断），而云端没有 `.git`、服务也不暴露 commit，清单里「代码身份」这一项此前没有可复算的证据。新增 `app/evaluation/code_fingerprint.py`：对 `services/api/app/**/*.py`、`contracts/openapi.yaml`、`services/api/requirements.lock`（排除 `__pycache__`、`*.pyc`）逐个取文件字节 sha256，按相对项目根的 POSIX 路径排序后规范化序列化再取 sha256，记进清单的 `codeFingerprint`/`codeFiles`/`codeScope`；新增不连库的 `python -m app.evaluation fingerprint`（`--json` 逐文件明细、`--root` 复算另一棵树）。M2 点的四条边界逐一落实：**文件范围**记进清单且参与比对（只算 3 个文件与算全量必须能区分）、**缺文件/空集合报错**（`services/api/requirements.lock` 缺失、目录扫到 0 个文件都停下）、**规范化**与其他哈希共用 `canonical_json`/`sha256_hex` 且不含大小与时间（`cp`、`git checkout` 内容不变则指纹不变）、**旧清单兼容**（缺这四个字段不报「版本不同」，只提示磁盘身份未证明）。「运行进程 ↔ 磁盘版本」按 M2 的提醒单独处理：`--process-pid` 读 `/proc` 的启动时间并列出写于其后的代码文件，有就停下要求先重启；`processStartedAt` 只记录不比对（重启是正常操作）。`verify` 措辞相应改成「磁盘代码与签认一致；但对应哪个提交、进程里跑的是不是它仍未证明」，不再因指纹一致就宣称运行代码身份已证明。`tests/test_eval_code_fingerprint.py` 20 项（同树两次一致、创建顺序无关、单字节变异、排除字节码、缺文件点名、空集合报错、范围入清单、`stale_code_files` 三例、Linux 才跑的 `/proc` 两例、CLI 三例、比对五例），`test_eval_snapshot.py` 原「两侧 `codeCommit` 皆 null」用例改写为「指纹一致但未证明什么」并新增「旧清单仍可核对且明说未证明」。验证（本机 Docker 库）：全量 **873 passed / 13 skipped / 77 subtests**（新增 20 项、改写 1 项）；`tests/test_eval_snapshot.py` + `test_eval_code_fingerprint.py` + `test_eval_dataset_map.py` 合跑 **90 passed / 1 skipped**。**未执行**：云端实跑（`--process-pid` 依赖 Linux `/proc`，需在 ECS 上验一次）。

**M2 第四轮复审的两项（2026-11-08 改，#51，P1）**：`content-alignment@2` 靠「表达式在库里唯一」认归属，两个前提都不成立——另一份政策恰好有唯一相同表达式时会把条件判错，同一条库条件被两个 CSV 条件代号认领时也不报。现改为 `content-alignment@3`：① `source_register.csv` 升为第四份必需输入，`policy_id → 版本` 是归属的唯一依据，`source_version` 只在**同一代号**的多个版本间消歧；登记行再与库的文号/标题逐项核对，`labels.csv` 的 `policy_id` 与 `policy_version` 也必须一致（只比一列会放过挂错政策的标签）；② 条件映射要求一对一，按 `(versionId, criterionId)` 记认领它的 CSV 代号，多于一个即未解决。新增反例：错误政策恰好有唯一相同表达式、`source_version` 登记在另一政策名下、标签代号与文号不匹配、一条库条件被两个代号认领；另加登记缺失、登记版本不在库、同代号两版本需 `source_version` 消歧各一条。`tests/test_eval_dataset_map.py` 现 29 项；全量（本机，无库门控照常跳过）**982 passed / 99 skipped / 77 subtests**。**未执行**：云端按四份 `--dataset-file` 重跑 `inputs`/`verify`（需在 ECS 上做）。

验证（2026-11-06，本机 Docker `pgvector/pgvector:pg16` + `redis:7.4-alpine`；M2 三轮复审改完后重跑）：`HUICE_TEST_DATABASE_URL` 指向本地库时全量 **782 passed / 11 skipped**（余 11 项为 OBS 5、DeepSeek 3、Redis 3 门控）；`pytest tests/test_eval_snapshot.py -m db` **14 passed**，`tests/test_eval_snapshot.py`（50 项）与 `tests/test_eval_dataset_map.py`（20 项）合跑 **70 passed**；无库全量 **731 passed / 62 skipped**。**未执行**：云端实跑（本工具尚未部署）；冻结正文的条件级结果是 Markdown 项目符号，机器只能半结构化解析——已知缺口，见 2.9。

### M1 后端实现验证记录（2026-11-06，第七阶段：材料要求导入与四态清单）

按 PR #37 的结论（P2 指派 M1 实现、M2 消费页面；P1 的 GRANT-02/05/06 空事实键要能在接口里表达）落地：迁移 `008_material_requirements.sql`、`app/materials/`（CSV 解析校验 + `check`/`import` 命令行）、`domain/preparation.py`（运行期展开）、`orchestrator` 的条目生成与告警条件、`report` 的四态文案；契约升到 `1.0.0-rc.8`（`PreparationItem` 增 `materialId`、`status`）；rc.9 只改 `status` 的两个取值名（`provided`/`missing` → `facts_filled`/`facts_missing`），字段与结构不动。

**实现中改掉的两个口径问题**（不是预先设计的，是跑真实数据时暴露的）：

1. **一个业务代号可以登记多份文号**。`source_register.csv` 里 `CN-TAX-GXJS` 同时有已废止的 `国科发火〔2008〕172号` 与现行 `国科发火〔2016〕32号`，最初的 `read_policy_codes` 见到重复文号就整份报错，把一份本来没用到该代号的清单也挡住了。改成「代号 → 全部文号」原样保留，歧义只在材料真正引用该代号、且各文号在库里指向不同政策时才报错（`test_a_code_with_two_published_documents_is_ambiguous`）。
2. **没有事实键的条件交材料被静默省略**。GRANT-02/05/06 条件只有文字、`proves_fact_keys` 为空，按「条件交只在被引用时列出」的规则永远不出现——申请书不出现会让人以为不用交。改成：有键的条件交仍按引用决定，无键的条件交一律列出并标 `manual_check`（`missing=true` 保守）。这也让 `manual_check` 在真实数据上真正可达。

**真库端到端**（本地 PostgreSQL 16，临时库 `huice_mat_e2e`，2026-11-06）：应用 001—008（`schema_migration` 八行、基础表 16 张）→ 按 `source_register.csv` 的文号发布 5 项政策（条款为合成表达式）→ `python -m app.materials check` 与 `import`：**可导入 13 行**，跳过 TAX-04、GRANT-09（`待核实（依据未入库）`）；对 5 项政策各跑一次判断，清单 4 / 2 / 5 / 3 / 3 条，四种状态都出现过（按 rc.9 的名字：SME-012 政策下 TAX-01 为 `facts_missing`，因 `establishedDate`/`independentLegalEntity` 无值，TAX-02 为 `facts_filled`，TAX-05/TAX-06 各 8 个季度引用均为 `facts_missing`；这次跑数在改名前完成，当时叫 `missing` / `provided`）。临时库只用于这次留证，验证后弃用，不含任何真实企业数据。

验证：`pytest -q`（HUICE_TEST_DATABASE_URL 指向本地库）**1 failed / 815 passed / 12 skipped / 77 subtests**；唯一失败是 `test_contract.py::test_types_are_generated_from_current_contract`——契约变更后 `apps/web/src/api/types.ts` 需由 M2 按 rc.8 重新生成（与前几次契约变更同一流程）。`pytest -m db -q` **59 passed**。

## 9. 各成员的文件编写范围（以 huice/ 为根）

### M1：后端、契约、基础设施

| 文件/目录 | 编写内容 |
| --- | --- |
| `contracts/openapi.yaml`、`contracts/README.md`、`contracts/generate_types.py` | API 参数、schema、权限、错误、示例和类型生成；M2 审查每次变更及前端生成文件 |
| `services/api/app/main.py`、`schemas.py` | 应用组装（CORS、中间件顺序、惰性 `app`）、wire 模型与 camelCase 别名 |
| `services/api/app/{config,errors,security,db}.py` | 环境变量加载与按模式校验（缺失只报变量名）、统一错误信封与 `X-Trace-Id`、**门禁**（restricted 的演示 cookie）、psycopg 连接池与健康探测；E1 起身份/角色/归属分别在 `identity.py` / `access.py`，见 §3 |
| `services/api/app/{auth,identity,access}.py` | 密码哈希与 CSRF 派生、登录限流；凭证真值表（会话 → 运维 Bearer → demo）、角色判定；企业与 run 的对象归属 |
| `services/api/app/routes/{health,policies,admin_policies,enterprises,assessment_runs,auth}.py` | 本文对应的 16 个基线接口 + E1 新增的 AUTH-01—07（`auth.py` 五个账号/会话操作，两个列表在 `enterprises.py` / `assessment_runs.py`）；只组织请求/响应，不在路由里写规则（API-01—16 均已实现，API-14 轨迹在 `assessment_runs.admin_router`） |
| `services/api/app/domain/{rules,citations,questioning,versioning}.py` | AST/求值、引用校验、补问、版本依赖与失效（均已实现；季度平均的期间约束与冲突传播见 2.2） |
| `services/api/app/domain/preparation.py`、`services/api/app/materials/{catalog,__main__}.py` | 材料要求的导入与运行期展开：`catalog` 解析并校验 CSV（只收已仲裁行、必交/条件交字段互斥、事实键白名单），`__main__` 是 `python -m app.materials check/import` 命令行，`preparation.build_items` 把材料按本次判断的引用与期间展开成四态条目（2026-11-06，契约 rc.8） |
| `services/api/app/agents/{contracts,orchestrator,ingestion,extraction,profile,explanation,report,trace}.py` | 交接类型、两条流程、预算与脱敏轨迹（ingestion 文件摄取、profile 画像归一化、extraction 草稿抽取、orchestrator 同步判断、trace 轨迹、report 报告渲染均已实现；`explanation` 的清单 `items[]` 自 2026-11-06 起由 `orchestrator._material_items` 生成、`report` 按四态渲染） |
| `services/api/app/integrations/{storage,cache,llm,obs,accounts}.py` | 持久化、缓存、模型（DeepSeek）与原文存储适配（accounts 是 E1 的账号/会话仓储：注册事务、会话摘要、签发演示 cookie 时登记 `demo_quota` 行（占额在建企业的事务里，见 API-08 下的说明）；storage 已覆盖政策草稿、企业画像、公开政策、run、失效视图与轨迹读取，以及 E1 的归属过滤与两个列表；llm/obs 已实现，`esdk-obs-python` 已入锁，**2026-10-27 已在真实桶与真实 DeepSeek Key 上联调通过**（`test_obs_live.py` 5 项、`test_deepseek_live.py` 3 项）；cache 已实现七字段版本化键、值投影与故障绕过，`redis==8.1.0` 已入锁） |
| `services/api/app/evaluation/`（含 `dataset_map.py`）、`services/api/tests/{test_eval_snapshot,test_eval_dataset_map,test_eval_report_freeze}.py` | 评测输入快照的导出与核对（含 CSV ↔ 库的逐条对齐）、以及 API-13 报告的确定性冻结（`EVALUATION_FREEZE` §5.2 第 1/2/3/4/5 项，见 2.9）；不产出评测指标 |
| `services/api/tests/{test_contract,test_rules,test_invalidation,test_agent_flow,test_citations}.py` | 静态契约与业务测试，独立报告覆盖范围（refs 解析、四态优先级、AST 求值、引用校验、失效视图与端到端判断链路） |
| `services/api/tests/{conftest,support,test_config,test_http_envelope,test_auth,test_e1_auth_flow,test_e1_access,test_e1_draft,test_import_http,test_ingestion_units,test_admin_read_http,test_admin_review_http,test_public_read_http,test_enterprise_http,test_report_trace_http,test_profile_units,test_questioning,test_trace,test_cache,test_cache_redis_live,test_extraction_units,test_openapi_alignment,test_obs_object_store,test_obs_live,test_llm_client,test_deepseek_live}.py` | 无数据库 HTTP 测试（假仓储）、各单元口径与契约对齐测试；适配器用假客户端/假传输层/假缓存后端，不联网。三个例外都靠环境变量门控、默认跳过：`test_cache_redis_live.py`（`HUICE_TEST_REDIS_URL`，真实 Redis）、`test_obs_live.py`（`HUICE_TEST_OBS_*` 四项，真实桶）、`test_deepseek_live.py`（`HUICE_TEST_DEEPSEEK_API_KEY`，真实模型） |
| `services/api/tests/{conftest,support,db_support,test_db_policy_repository,test_db_evaluation,test_db_errors}.py` | `db` 标记的真库用例：独立 schema 建表、用例间清表；覆盖政策导入落库、企业画像 CAS、公开列表与游标、快照闸门、run 落库与失效视图、只追加与不可发布保护 |
| `services/api/tests/{test_materials,test_db_materials}.py` | 材料要求：CSV 解析与校验、代号多文号解析、导入替换与类型一致性（真库），以及运行期展开成四态、季度八引用、关闭政策与报告文案（2026-11-06，24 + 14 项） |
| `services/api/AGENT_PROTOCOL.md`、`pyproject.toml`、实现后生成的 `requirements.lock` | 内部协议、真实依赖与测试命令 |
| `infra/**`、`.env.example`、`.gitignore`、`README.md`、`docs/devlog/M1.md` | 数据库/Compose、网关会话和门禁、部署回滚、环境说明与日志 |

### M2：前端及浏览器联调

| 文件/目录 | 编写内容 |
| --- | --- |
| `apps/web/src/api/types.ts`、`client.ts` | 对齐契约的类型与唯一HTTP调用层；不得自行改枚举、传密钥到构建变量 |
| `apps/web/src/{main,App}.tsx` | 页面入口、导航、公共布局与错误状态 |
| `apps/web/src/pages/EnterprisePage.tsx` | 事实表单、期间、已知/未知、ETag 更新 |
| `apps/web/src/pages/OpportunitiesPage.tsx` | 候选筛选、创建分析、将政策元数据与判断关联 |
| `apps/web/src/pages/PolicyDetailPage.tsx` | 条件结果、引用、计算过程、补问及重算 |
| `apps/web/src/pages/ReportPage.tsx` | 两类清单、历史与失效提示、Markdown 下载 |
| `apps/web/src/pages/AdminReviewPage.tsx` | 后台列表/详情/导入/抽取/修改/发布及409处理 |
| `apps/web/src/pages/TracePage.tsx` | 有序脱敏轨迹与受限访问错误 |
| `apps/web/src/components/{StatusBadge,EvidenceRow,NextQuestion,PreparationList}.tsx` | 本文第6节 Props 的展示和回调 |
| `apps/web/src/styles/{tokens,app}.css`、`src/tests/critical-flow.spec.ts` | 界面样式、关键浏览器场景 |
| `apps/web/{package.json,index.html,.env.example}`、实现后新增的 Vite/TS 配置及 `package-lock.json`、`docs/devlog/M2.md` | 前端依赖/构建与日志；新增配置只放前端目录 |

### M3：政策、文案、样本、人工验收（不要求写业务代码）

| 文件/目录 | 编写内容 |
| --- | --- |
| `data/policies/source_register.csv` | 官方来源、版本、正文/附件、有效期与历史窗口 |
| `data/policies/criteria_review.csv` | 原文定位、AST 人工标注、双人复核、冲突来源与仲裁 |
| `data/fixtures/enterprises.csv` | 合成事实；一行一个 key+期间；空 fact_value 表示未知，不写0代替 |
| `data/evaluation/labels.csv` | 条件级四态真值、标注分歧、仲裁、开发/留出集 |
| `docs/product/{POLICY_SCOPE,ANNOTATION_GUIDE,ACCEPTANCE}.md` | 样本范围、字典含义/期间/提问成本、验收步骤与实际结果 |
| `docs/ui/{UI_BRIEF,COPY_AND_STATES}.md` | 线框、中文文案、四态/失效/窗口/错误/空态 |
| `docs/diagrams/**` | 主要用例图源文件、导出图片与需求映射；M1/M2 审核系统边界 |
| `docs/demo/SCRIPT.md`、`docs/devlog/M3.md` | 真实演示脚本、政策工作和验收日志 |

CSV 用 snake_case 表头，M1 导入时映射 camelCase，不能让 M2 直接读 CSV 充当生产 API。现有表头不任意改：季度起止日期在 period 中采用 `YYYY-MM-DD/YYYY-MM-DD`，API 转成 Period 对象；adjudicated_expression 用带 CSV 转义的 JSON AST；原文定位通过 source_page_or_section/source_span 映射 sourceSpans。API 所需但表头未覆盖的元数据，在导入表单明确补齐，禁止默填。数据未标注或未复核时不能发布。

### 共同维护规则

本文由 M1 维护接口/实现段，M2 审核前端可用性，M3 审核业务与中文含义；文件范围表由三人共同确认。2026-11-09 负责人授权本次按项目组视角统一整理跨模块文档，现行阅读与维护位置见[文档导航](README.md)，跨成员整理记录在[项目协作日志](devlog/PROJECT.md)。历史原稿保留并指向现行计划；后续实现仍按成员职责交接，跨范围问题附请求样例、现象、预期和对应章节。文档整理不代替三方技术/业务签收。

契约变更先更新 OpenAPI、本文和示例，再由 M1/M2 各改自己实现/类型；破坏性变更附迁移说明。M3 不直接改 Python/TS；M2 不裁定政策规则；M1 不凭模型替代人工标注。各人更新自己的 devlog，不替别人写已完成记录。
