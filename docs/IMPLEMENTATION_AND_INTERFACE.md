# 惠策协同：功能实现逻辑与接口协作约定

> 日期：2026-10-23；版本：v0.2 协作草案。成员沿用 M1（后端）、M2（前端）、M3（政策与验收），尚未指定实名。
>
> 本文依据[需求分析](./brief/惠策协同_需求分析与功能范围.md)、[三人分工与开发计划](./brief/惠策协同_三人分工与开发计划_截至11月15日.md)、[策划案](./brief/华为ICT大赛赛题一_政策红利挖掘与申报多智能体_初步策划案.md)、[评审建议](./brief/评审建议_惠策协同方案_可行性与优化.md)及 `huice/` 现有文件整理。当前应用仍是注释骨架；本文和配套 OpenAPI/TypeScript 类型定义是实现依据，不表示业务已经实现或已完成联调。本文补充旧计划中未定义的细节；接口由 M1/M2 联合审查后冻结。

## 1. 范围、依据和成功标准

交付范围为深圳合成企业的政策机会分析：2 项国家税收政策、3—5 项深圳资金通知、6—8 家企业和至少 30 个判断组合。FR-01～FR-09 为 P0；文件 OCR、金额试算、自动巡检、第二城市、提醒与批量报告仍为 P1。不代企业申报，不把历史通知显示成当前开放项目。

实现顺序：政策来源与标注 → 契约/事实字典 → 引用校验与规则求值 → 企业判断 → 补问/清单/版本 → 页面联调 → 评测部署。先完成确定性链路，再接 MaaS 解释和向量检索；MaaS 不决定资格。

主要用例图见 [PlantUML 源文件](./diagrams/main-use-cases.puml)、[PNG](./diagrams/main-use-cases.png)、[SVG](./diagrams/main-use-cases.svg)；角色、用例关系及本地插件预览/导出方法见[图示说明](./diagrams/README.md)。

| 可验证标准 | 负责人/检查位置 |
| --- | --- |
| 16 个接口的字段、枚举、状态码、参数位置、权限和示例一致 | M1/M2；`contracts/openapi.yaml`、`src/api/types.ts`、`test_contract.py` |
| 数字方向抽错不能发布；未发布规则不能判断；缺事实不判满足 | M1/M3；`test_rules.py`、`test_agent_flow.py` |
| 补答只影响相关条件；跳过不改事实；0 与 null 区分 | M1/M2；规则测试和浏览器用例 |
| 政策或事实变化能指出依赖和失效原因，保留历史原文及结果 | M1；`test_invalidation.py` |
| 税收/资金清单不同；页面与报告展示相同依据和降级提示 | M2/M3；`critical-flow.spec.ts`、`ACCEPTANCE.md` |

来源优先级：需求分析决定功能范围；本文细化调用行为；OpenAPI 是 HTTP 字段的单一真源；TypeScript 类型与后端 schema 必须跟随 OpenAPI；`AGENT_PROTOCOL.md` 只规定内部调度。旧计划的 14 个 API 保留原编号，本文新增 API-15/16 供后台读取草稿，不能用公开接口泄漏未审内容。原计划没有写清的取舍均在本文明确，不宣称已获全员签收。

## 2. 各功能怎样实现

### 2.1 FR-01：政策导入与版本

1. `AdminReviewPage → client.importPolicy → admin_policies.py → ingestion → storage/obs`。前端提交 `multipart/form-data`，`metadata` 是 JSON 字符串；附件用同名 `files` 重复添加，不能手写 multipart boundary。
2. 新政策不传 `policyId`，后端分配 UUID；新版本传原 `policyId` 和新 `version`。`(policyId, version)` 唯一。已发布版本不能原地改元数据/规则，必须重新导入新版本，保留旧版；仅有草稿不让旧判断失效。
3. `metadata` 包含标题、类型、机关、地区、文号（可 null）、适用主体、发布日期、有效日期、申报窗口、官方入口和来源 URL。后端记录 `fetchedAt`、文件哈希和正文/附件关系。URL 必须通过服务端官方域名白名单检查；跳转后也检查目标，禁止访问本机、内网和非 HTTPS 地址。网络抓取失败返回错误，不凭空补原文。
4. 导入后为 `pending_parse`；抽取完成为 `pending_review`；人工发布后为 `published` 或按时间推导为 `closed`。内部保存“是否已发布”和有效日期，`closed` 是已审核但过期/窗口关闭的公开视图，不代表未审核。
5. M3 可以人工填规则，仍须经过引用校验与发布。官方 URL 不是校验通过的充分条件；原文和定位必须存在。

### 2.2 FR-02：条款、AST 与引用审核

`extract(version, modelVersion) → extraction → validate_citations → 草稿与 findings`。模型返回值先做结构检查；引用校验核对源版本、片段哈希、数值、单位换算、比较方向、否定、行业与期间。含糊内容返回 `needs_review`，不能只靠关键词相同放行。

`PUT criteria` 整体替换指定版本的草稿条件，保存结构合法但引用失败的草稿并返回 `validationFindings`，让审核员看到问题；结构不合法返回 422。发布时重新校验，存在 `failed/pending` 条件或未确认人工审核均返回 422。不能信任前端传入的“校验通过”状态。待核实冲突可以作为**已审核的冲突记录**发布，带两处来源和 `reviewFinding=conflict`，规则引擎固定返回 `conflict`；这与“引用不支持表达式”不同，后者不能发布。

每个 `CriterionDraft` 为 `{id,title,expression,sourceSpans,reviewFinding}`；后端增加 `validationStatus`。`sourceSpans` 至少一个，冲突至少两个；含 `sourceId,sourceUrl,sourceVersion,page,section,quote,textHash`，`page` 与 `section` 至少一个非 null。`sourceId` 定位已保存正文或附件；哈希由后端依据保存文本计算并验证。

AST 仅接受以下形状，不能执行 Python/JS 字符串：

| `op` | 参数 | 求值方式 |
| --- | --- | --- |
| `and` / `or` | `children: Expression[]`，至少两个 | AND 任一不满足则不满足；全满足才满足。OR 任一满足则满足；全不满足才不满足；其余证据不足 |
| `not` | `child: Expression` | 满足/不满足互换，未知仍未知 |
| `compare` | `factKey,period,comparator,value,unit` | `comparator=lt/lte/eq/gte/gt/ne`；数值用 Decimal，禁止 JS float 预先算结果 |
| `date` | `factKey,period,comparator,value`（日期） | 按日期比较，禁止本地化日期字符串 |
| `industry_exclusion` | `factKey=industry,period,excluded[]` | 行业值在名单中为不满足；缺行业为证据不足 |
| `quarterly_average` | `metric=employeeCount/assets,period,comparator,value,unit` | 各季度 `(期初+期末)/2`，再对完整四季度求平均；保留八个输入与中间值 |

AST 中 `period` 是 `assessment`（本次判断所属年度）或显式 `{startDate,endDate}`；年度季度平均表达式必须指向完整自然年度。地区等字符串比较只允许 eq/ne。区间用 AND 的上下界表达，不另加操作符。比例以 unit=`ratio` 的十进制字符串表达；新增事实字段要先改字典和契约。所有冲突节点先单独检测并向上传播为 `conflict`，不得被 OR 短路掩盖。

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

所有事实 `value=null` 均表示未知；省略事实等价于尚未提供。空字符串、字符串 `"null"`、NaN 均拒绝。数值的通用词法允许最多 6 位小数，按上述字段进一步校验；`"0"` 是有效已知值。`period={startDate,endDate}` 使用日期，包含两端；开始不能晚于结束。同一 key 不同年度是不同事实，不互相覆盖。

编辑接口 API-10 的路径仅传 key，**目标期间在 body.period 中确定**，完整提交 value/unit/period/source；null 用于清除值，不物理删除历史记录。每次成功变更生成 `factVersion`（完整画像规范化快照的 SHA-256；数组按 key/期间排序，Decimal 规范化），相同内容不产生新版本。不从前端接受哈希。ETag 采用带双引号的 factVersion，前端必须保留原样。

### 2.4 FR-04：候选与确定性判断

1. `OpportunitiesPage` 调 API-02 获取已审核候选。地区包含国家级与深圳；允许列出行业相关但不满足的政策用于解释，检索相关性不代表资格。类型/状态/日期用于检索，不能以相似度绕过硬门槛。
2. API-11 校验企业、1—7 个不重复政策 ID、`asOf` 和 `period`；固定企业快照及每项政策的最新已发布版本。`asOf` 是带时区时间，判断政策日期时换算为北京时间；不凭 asOf 自动选任意历史版本。本版重放依据通过旧 run 读取，重新计算使用当前已发布规则。
3. `orchestrator → rules.evaluate_policy(policySnapshot, enterpriseSnapshot, period, asOf)`。只读取引用校验通过且已人工审核的数据。未审政策整体返回 422，不在一次运行内悄悄跳过。
4. 条件级先检测已审核的冲突，再执行硬门槛和 AST。政策级默认 AND：存在冲突为 conflict；否则任一条件不满足为 not_satisfied；否则存在必要事实未知为 insufficient_evidence；全部满足才 satisfied。OR 分支中无关的缺失事实不应阻止已经成立的有效分支。
5. 关闭政策可用于历史演示，不得伪装成开放机会：以 asOf 求四态结果，同时以读取时刻判断 `validity`；已关闭/已过期明确标 stale，显示原因并隐藏申报动作。晚于有效期的当期评估还应有时间门槛不满足。
6. 创建不可变 run，保存规则版本、政策快照、事实快照、依赖、确定性解释与来源，再允许模型润色。缓存命中也分配新 runId，并注明复用来源；不覆盖旧 run。

### 2.5 FR-05：补问与局部重算

`questioning.choose_next_question(assessments, skippedFactRefs)` 对 unresolved 的“key+period”去重，按“受影响的待定政策数降序、获取成本升序、敏感程度升序、key/日期字典序”确定稳定顺序。成本/敏感度由 M3 在标注约定中给整数档位，不由模型猜测。函数返回当前全局第一问（rank=1）或 null；仅把同一问题放入受该 FactRef 影响的 policy.nextQuestion，其他政策为 null。前端按 FactRef 去重后一次显示一问，不自行重新评分；完整排序理由写脱敏 trace 供验收比较。

`NextQuestion` 回调只给输入值或 skip：回答后调用 `PATCH fact → 收到新 Enterprise → POST assessment-runs`；重算 body 传 `supersedesRunId`。后端验证旧 run 属于同一企业、同一组政策和期间，按依赖图仅重算受改动影响的条件，其他条件可复用；仍生成新 ID。政策版本变更则重算对应政策。补问计数按实际成功更新/明确跳过记录，不能按渲染次数计数。

跳过不调用 PATCH，前端本次页面会话保存已跳过的 FactRef；下次重算可传 `skippedFactRefs` 供排序排除，事实仍未知，结果不得改成不满足。不增加回答/跳过专用端点。

### 2.6 FR-06/08：准备清单、解释和报告

每项结果带 `preparation={type,handlingMode,officialEntryUrl,deadline,items[]}`；item 含 `title,description,requiredFactRefs,missing`。税收 handlingMode=`tax_self_assessment`，资金=`grant_application`；真实具体办理说明来自已审核材料要求，模型只改写文字。类型不同必须分栏；关闭窗口不能有立即申报动作。

结果的 `explanationMode=maas/saved/fixture`，`warnings[]` 说明解释降级/样例模式。模型失败时规则结果照常返回，使用已存确定性说明；不能把整次判断伪装成模型成功。抽取端模型不可用仍返回 503 并保留草稿。

`ReportPage` 先读 API-12 的 JSON 展示，再通过 API-13 下载 UTF-8 Markdown；不能把下载内容当 JSON 解析。报告含原 run 的生成时间、企业/政策/规则版本、条件与证据快照、两类清单、风险文案；每次下载前追加“截至 downloadedAt 的有效性提示”，正文历史依据不变。旧文件脱离系统后不会自动更新，页首提示再次申报前返回系统核验。

### 2.7 FR-07：失效、并发和缓存

`storage` 在同一数据库事务内提交事实/政策发布变更与失效事件。草稿修改不影响现有结论；发布新版本使依赖该 policyId 旧版本的 assessment 失效。事实改动按 key+period 查依赖，仅影响相关条件/政策；没有读到该事实的政策保持 current，不按整企业粗暴失效。run 保存完整 factVersion，条件另存依赖快照，以便识别这种差别。

历史判断正文与旧事实/政策快照不可变；`validity,staleReasons` 是按变更事件和当前时间生成的读取视图，不回写四态结果。`staleReasons` 包括 `policy_changed/fact_changed/window_closed/policy_expired/valid_until_passed`，附 changedCriterionIds/FactRefs 和中文原因。GET run、报告与缓存命中都重新计算该视图；只换 Redis 键不能代替数据库失效记录。

`validUntil` 是现有已知条件下的最早未来失效时刻，可取适用期结束次日零点、窗口关闭等边界的最小值；没有可靠边界时 null。政策/事实变化仍可提前使其失效，不把政策结束日期无条件复制为结论有效期。已过边界的当前读取标 stale。

缓存键包含 `enterpriseId,policyId,policyVersion,factVersion,ruleVersion,period,asOf`；不漏判断时点、期间和规则版本。不把一个时间的结果套给另一个时间。Redis 不可用可绕过后重算，数据库不可用返回 503。

所有写请求以数据库中的当前版本执行 compare-and-swap；仅先查询再无条件更新会丢失并发修改。ETag 使用强标签，不接受 `*` 或弱 ETag；缺 If-Match 返回 428，版本不一致沿用旧计划返回 409，禁止前端自动覆盖重试。

### 2.8 FR-09：智能体与轨迹

入库链：管理路由 → ingestion → extraction → citations → 人工审核。在线链：assessment 路由 → orchestrator → profile 快照 → 已审规则检索 → rules → questioning → explanation → trace/storage。四个角色可共享同一 Python 进程与 MaaS 客户端。

`AgentContext` 只有上下文（trace/request ID、asOf、period、版本图、预算）；业务参数由独立 `AssessmentInput` 传入，不能把 enterpriseId/policyIds 塞进未定义的 context 字段。内部 snake_case 转外部 camelCase 只在 schema 边界做。

`TraceStep` 包含 `sequence,stage,status,inputSummary,outputSummary,evidenceRefs,policyId,policyVersion,modelOrRuleVersion,elapsedMs,errorCode,humanAction`；多政策公共步骤的 policyId/version 可 null。状态仅 `ok/needs_review/retryable_error/fatal_error`。线上 run 应链接所用政策版本的入库、审核轨迹，再列在线轨迹；不能只保留模型最后一句话。summary 不得含密钥、完整提示词或企业资料。

## 3. HTTP 共同约束

- 前缀 `/api/v1`；JSON camelCase；实体/请求/追踪 ID 为 UUID。政策 version 为人工版本字符串，事实哈希和规则版本不强制 UUID。日期 `YYYY-MM-DD`，时间带时区 ISO 8601；数据库 UTC。
- 成功响应直接返回对象，不另包 `{data:...}`。列表 `{items,nextCursor}`，默认 limit=20、1—50；cursor 不透明，过滤条件改变须清空 cursor。空列表 200，不是 404。
- 所有公开政策读取只含已审核版本。`version` 指定不存在/未发布版本时 404，不能回退到其他版本。后台 API-16 可读取未发布版本。
- 非 2xx 统一 `{"error":{"code":"...","message":"中文可执行说明","details":{}},"traceId":"UUID"}`；FastAPI 默认 detail 数组必须转换。响应头 `X-Trace-Id` 与 body.traceId 一致；成功也返回追踪头。422 的 details 可放 fieldErrors/findings；409 放 currentVersion；不得含堆栈、密钥或连接串。
- 状态码：400 请求语法/查询参数错误；401 未认证；403 已认证但权限不足；404 不存在；409 版本/幂等冲突；413 文件过大；415 文件类型不支持；422 业务或 body 校验失败；428 缺并发头；429 限流；500 未预期内部错误（脱敏）；503 必需依赖不可用。
- 本地模式仅监听 loopback；管理 API 和轨迹均须 `Authorization: Bearer <ADMIN_TOKEN>`。M1 在受控机器手动输入，M2 的 client 仅保存在内存，退出/刷新清空，不写 localStorage、URL 或 VITE 变量。没有令牌不展示后台数据。
- 云端 restricted 模式网关先验证 `huice_demo_session` 会话 cookie（HttpOnly/Secure/SameSite=Lax），管理 API/轨迹再验证 Bearer；会话签发由 M1 在部署入口落实，不能仅隐藏按钮。OpenAPI 描述 restricted 模式；local 模式免演示 cookie，但不免管理 Bearer。健康检查无认证且不含连接详情。
- 跨源开发 fetch 使用 `credentials:'include'`，CORS 只允许确切前端源并允许 credentials。允许头 `Content-Type,Authorization,If-Match,Idempotency-Key`；暴露头 `ETag,X-Trace-Id,Content-Disposition`。M1 处理 OPTIONS；M2 不能以 no-cors 规避。

## 4. 端点与参数传递（路径均省略 /api/v1）

### 4.1 公开政策与企业

| ID / 方法路径 | 参数位置与必填约束 | 成功响应 |
| --- | --- | --- |
| API-01 `GET /health` | 无 body | 200 `{status:"ok",version}`；未就绪 503 |
| API-02 `GET /policies` | query：status=published/closed、type=tax/grant、region、asOf、cursor、limit 均可选；不传 status 时含已审开放和关闭样本 | 200 `PolicyList`；支持历史展示 |
| API-03 `GET /policies/{policyId}` | path UUID；query version 可选 | 200 `PolicyDetail`，sources/criteria/preparationTemplate |
| API-08 `POST /enterprises` | JSON `EnterpriseInput`，见 2.3 | 201 `Enterprise` + ETag |
| API-09 `GET /enterprises/{enterpriseId}` | path UUID | 200 `Enterprise` + ETag |
| API-10 `PATCH /enterprises/{enterpriseId}/facts/{factKey}` | path ID/key；If-Match 头必填；JSON `FactUpdate` 不含 key，period 必填 | 200 完整 `Enterprise` + 新 ETag |

`Enterprise={id,nameSynthetic,period,facts:EnterpriseFact[],factVersion}`。API-02 只给政策元数据，不含企业判断；M2 按 policyId 将 API-11/12 的 assessments 合并到列表。未分析时显示“尚未分析”，不得猜测四态。

### 4.2 后台政策（全部管理权限）

| ID / 方法路径 | 参数位置与必填约束 | 成功响应 |
| --- | --- | --- |
| API-04 `POST /admin/policies/import` | multipart metadata 字符串 + files；每文件 ≤10 MiB、最多5个；仅 PDF/UTF-8 text（含原始 HTML，展示须转义），检查真实内容 | 201 `PolicyDraft` + ETag；相同政策版本 409 |
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
| API-14 `GET /assessment-runs/{runId}/trace` | path UUID；管理 Bearer | 200 `{steps:TraceStep[]}`，sequence 升序 |

API-11 不复用 runId；用户重复点击由前端禁用提交按钮，网络失败后不得自动创建重复运行。同步超时由 M1 记录，MVP 不引入未定义异步队列。所有 API 都须处理统一错误；报告接口失败时 Content-Type 回到 application/json。

`AssessmentRun={id,enterpriseId,period,asOf,createdAt,policyVersionMap,factVersion,ruleVersion,supersedesRunId,assessments}`。`supersedesRunId` 响应始终有值或 null。

`PolicyAssessment={policyId,result,validity,staleReasons,criteriaResults,missingFacts,nextQuestion,validUntil,preparation,explanation,explanationMode,warnings}`。`CriterionResult={criterionId,result,reason,factRefs,evidenceRefs,calculation}`，calculation 可 null，季度计算必须给输入与中间值。`FactRef={key,period}`；missingFacts 是 FactRef 数组。`NextQuestion={factRef,prompt,reason,rank}`；不存在为 null。

四态枚举仅 `satisfied/not_satisfied/insufficient_evidence/conflict`；有效性仅 `current/stale`；closed 是政策状态。stale 展示优先，但原四态与原因仍可见。schema 中规定的可空字段应显式传 null；不能把空数组、缺字段、null 任意互换。

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

组件 Props：`StatusBadge({result,validity,policyStatus})`；`EvidenceRow({criterionResult,sources,facts})`；`NextQuestion({question,onAnswer(value),onSkip(),pending})`；`PreparationList({preparation,policyStatus,validity})`。组件不访问数据库/MaaS、不推导政策阈值；页面负责调 client 和管理请求状态。Report/Trace 页面以 runId 读取，不以当前 enterpriseId 重新拼装旧报告。

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

M1 实现后补：API TestClient 的成功/失败/错误头、multipart 超限/伪文件、未授权后台读取、If-Match 缺失/过期、抽取幂等、发布冲突、按 period 更新、Decimal 边界、季度缺一输入、OR/NOT/冲突传播、局部失效、模型降级、数据库失败和缓存隔离。

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

未执行：业务 API、规则求值、数据库/Redis/MaaS/OBS、浏览器与 TypeScript 编译（项目尚无构建依赖）。这些不计作通过，静态测试结果不能作为业务验收率。

## 9. 各成员的文件编写范围（以 huice/ 为根）

### M1：后端、契约、基础设施

| 文件/目录 | 编写内容 |
| --- | --- |
| `contracts/openapi.yaml`、`contracts/README.md`、`contracts/generate_types.py` | API 参数、schema、权限、错误、示例和类型生成；M2 审查每次变更及前端生成文件 |
| `services/api/app/main.py`、`schemas.py` | 应用组装、配置加载、CORS/错误转换、数据模型与别名 |
| `services/api/app/routes/{health,policies,admin_policies,enterprises,assessment_runs}.py` | 本文对应16个接口；只组织请求/响应，不在路由里写规则 |
| `services/api/app/domain/{rules,citations,questioning,versioning}.py` | AST/求值、引用校验、补问、版本依赖与失效 |
| `services/api/app/agents/{contracts,orchestrator,ingestion,extraction,profile,explanation,trace}.py` | 交接类型、两条流程、预算与脱敏轨迹 |
| `services/api/app/integrations/{storage,cache,maas,obs}.py` | 持久化、缓存、模型与原文存储适配 |
| `services/api/tests/{test_contract,test_rules,test_invalidation,test_agent_flow}.py` | 静态契约与业务测试，独立报告覆盖范围 |
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

本文由 M1 维护接口/实现段，M2 审核前端可用性，M3 审核业务与中文含义；文件范围表由三人共同确认。根目录原有四份说明文档继续只读引用，后续修订先讨论再指定提交者。每人只编辑负责范围；跨范围问题提供请求样例、现象、预期和对应章节交给所有者。本次用户明确授权的核对修改已跨目录同步契约，后续成员按此边界工作。

契约变更先更新 OpenAPI、本文和示例，再由 M1/M2 各改自己实现/类型；破坏性变更附迁移说明。M3 不直接改 Python/TS；M2 不裁定政策规则；M1 不凭模型替代人工标注。各人更新自己的 devlog，不替别人写已完成记录。
