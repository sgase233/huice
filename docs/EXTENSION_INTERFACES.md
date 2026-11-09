# #46—#48 扩展接口与数据协作设计

**全员共享设计；阅读入口见[文档导航](README.md)。**M1 提供技术方案并维护契约，M2 复核调用/状态/兼容性，M3 复核业务含义/数据来源；项目负责人统筹范围和排期。本文“建议”字段仍需相应评审，不因项目组统一整理而自动变为已签收。

| 工作包 | 阅读章节与接口编号 | 关联决策 | 验收对应 |
| --- | --- | --- | --- |
| AUTH / #46 | 第 2—3 节；AUTH-01—07，现有 API-08—14 权限变更 | D1、D2 | AUTH-AT-01—06 |
| DISCOVERY / #47 | 第 2、4 节；DISC-01—08 | D1—D3 | DISC-AT-01—07 |
| VISUAL / #48 | 第 5 节；VIS-01—06，现有政策/结果/轨迹复用 | D1、D2、D4、D5 | VIS-AT-01—08 |
| RELEASE | 第 6 节迁移、部署及签收 | D2、D6 | RELEASE-AT-01—02 |

决策记录统一回写[开发计划 E0 表](DEVELOPMENT_PLAN.md#e0-决策记录)，执行结果统一回写[验收清单](product/ACCEPTANCE.md)。AUTH/DISC/VIS 是设计追踪编号，不是已实现 HTTP 端点编号。

> 2026-11-09；基线 `807e1bb` / OpenAPI `1.0.0-rc.9`。**本文为待 M1/M2 契约评审、M3 语义复核的设计草案，新增端点均未实现。**已确认的业务决定与未决事项见 [开发计划](DEVELOPMENT_PLAN.md)。现行 16 个接口继续以 [openapi.yaml](../contracts/openapi.yaml) 为准，本次不将规划端点写进可生成客户端的现行契约，也不提前升级版本号。

## 1. 现有能力核对

| 能力 | 本地实际 | 对扩展的影响 |
| --- | --- | --- |
| 身份 | `security.py` 只有 `AdminBearer` 与 `DemoSession`；`enterprise` 无 owner | 新角色必须在服务端和对象查询上实现，隐藏菜单不足以隔离数据 |
| 条件表达式 | `PolicyDetail.criteria[].expression` 与 `Criterion.sourceSpans` 已存在；API-11/12 的 `CriterionResult` 只有条款级结果，无逐 AST 节点结果 | #48 逻辑结构不必先新增 expression 字段；按 run 的政策版本读取 API-03，再按 criterionId 关联。逐节点着色需要另补数据，不能在前端重写求值器 |
| 轨迹 | API-14 实际路径为 `GET /assessment-runs/{runId}/trace`，受管理权限保护（路径没有 `/admin`）；`TraceStep.objectRefs` 为对象引用 | 先维持权限；#46 后增加本人校验及企业可见投影；不能把 objectRefs 当成政策引文 |
| 政策推荐 | 手动 import 接收文件/元数据，extract 作用于已入库草稿；没有联网调度与候选区 | 候选提交前的抽取需调用共享抽取核心，不能提前 import 来假装候选未入政策库 |
| 人工审核统计 | `publish_policy` 写 `human_review`/`approve` 轨迹并设置 `reviewed_at`；旧事件无账号身份，未有每日待办快照 | 审核完成应有可追溯事件，不能仅拿 reviewed_at/发布时间当计数；缺失日终数据从上线起采集 |
| 系统运行统计 | `save_run` 在成功后插入 `status='succeeded'`，`created_at` 与 `finished_at` 写入同值；异常路径没有完整运行记录 | 旧数据只能证明保存过成功结果，不能证明失败率为 0 或真实耗时为 0；须先补运行生命周期采集 |

## 2. 通用契约与兼容边界

- 路径统一省略 `/api/v1`；JSON 使用 camelCase、UUID、带时区 ISO 时间；DB 存 UTC，日统计使用 `Asia/Shanghai`；金额继续用十进制字符串。
- 列表 `{items,nextCursor}`，游标与当前用户/权限/筛选绑定，最多 50 条；分页越权不能依靠前端过滤。新增错误继续使用现有 `ApiError` 与 `X-Trace-Id`。
- 草稿/配置/候选编辑使用 `ETag` + `If-Match`；缺条件头 `428`，过期 `409`。重复提交用 `Idempotency-Key`；作用域包含操作者、操作和目标，原子占用，同键异请求 `409`，同键重放返回同一结果，不再次创建政策或计费。
- 幂等提交先鉴权并检查已有键及请求指纹；已成功的同请求重放返回原结果，不因候选已 submitted 或旧 ETag 而拒绝。首次提交才检查候选状态/ETag 并原子占用键；进行中的重复请求返回明确可重试状态，不能再发起一次导入。
- 身份缺失/失效 `401`；角色不允许 `403`；企业用户读取非本人对象统一 `404`（避免泄露是否存在）；业务非法 `422`；速率受限 `429`；依赖不可用 `503`。候选过期/已处理统一 `409`，细分错误码在 E0 写入 OpenAPI。
- 无数据、未知、未采集、错误必须可区分。`null` 不得按 0；结构化用量继续保留 `tokensUnknown` 语义；图表不解析日志摘要冒充结构化指标。
- 新权限/字段不一定向后兼容：M1/M2 逐端点评估 security、必填字段、严格 schema 与旧脚本；在同一契约 PR 同步 OpenAPI、后端 schema、错误/示例、生成类型和回归用例。版本号按评审结论决定，本文不预定 `1.1.0`。

## 3. #46：账号、会话与对象权限

### 3.1 权限矩阵（目标行为，待实现）

| 操作 | 未登录 | 企业用户 | 审核员/保底账号 |
| --- | --- | --- | --- |
| 注册/登录 | 允许，受限流及外层演示门禁策略约束 | 不用于提升现有账号权限 | 密钥只在服务端验证，不能提交 role 自选权限 |
| 企业、事实、运行、报告读取 | 拒绝 | 仅本人绑定企业及运行 | 全局业务读取，依据 #46 明确决定 |
| 事实修改/发起分析 | 拒绝 | 仅本人企业 | 首版同样只对本人企业；“查看所有”不等于修改他人事实 |
| 本人轨迹 | 拒绝 | #46 完成后仅自己发起且属于本人企业的 run，返回脱敏投影 | 可读取全部业务轨迹 |
| 导入/抽取/编辑/发布、候选、发现配置 | 拒绝 | 403 | 允许；记录真实操作账号 |
| 审核工作量/全系统运行趋势 | 拒绝 | 403 | 全局读取，后端核角色 |
| 登出/改密 | 无有效会话的登出可幂等清 cookie | 仅自己的会话/密码 | 同左，不增加替他人改密接口 |

`reviewer` 与 `enterprise` 为建议角色值。保底管理员是预置 reviewer，不新增第三角色。密码哈希、注册密钥、会话凭据、模型密钥不属于任何角色可读取的“所有信息”。授权检查覆盖 API-08—14、历史运行关联、supersedesRunId、报告下载、后续列表与统计；先校验归属，再读缓存/返回数据。

### 3.2 建议端点

| 编号（草案） | 方法/路径 | 请求 | 成功响应/副作用 |
| --- | --- | --- | --- |
| AUTH-01 | `POST /auth/register` | `{username,password,enterprise:EnterpriseInput,reviewerSignupKey?}` | `201 {user}`；事务内创建账号及唯一企业，默认不自动登录；字段非法或密钥错误不留下半个账号/企业；重复用户名 409 |
| AUTH-02 | `POST /auth/login` | `{username,password}` | `200 {user,expiresAt,csrfToken}`＋会话 Cookie；账号不存在/密码错误使用同一提示 |
| AUTH-03 | `POST /auth/logout` | 无业务体；有效会话请求需 CSRF 校验 | `204`；撤销当前服务端会话、清 Cookie；重复退出仍 204 |
| AUTH-04 | `GET /auth/me` | 会话 Cookie | `200 {user,expiresAt,csrfToken}`；刷新恢复登录及 enterpriseId；响应禁止缓存 |
| AUTH-05 | `POST /auth/password` | `{currentPassword,newPassword}` | `204`；验证旧密码、更新哈希、立即撤销该账号全部会话，清 Cookie，重新登录 |
| AUTH-06 | `GET /enterprises?cursor=` | 角色过滤在服务端 | `200 {items:[EnterpriseSummary],nextCursor}`；企业用户至多本人一条，审核员可查看全部，包括标明为评测用途的无归属历史企业 |
| AUTH-07 | `GET /assessment-runs?enterpriseId=&cursor=` | 角色/归属过滤在服务端 | `200 {items:[RunSummary],nextCursor}`；支持历史分析、报告导航，不能只依赖浏览器保存 runId |

建议 `User={id,username,role,enterpriseId}`；不返回哈希、注册密钥。EnterpriseSummary 至少含 `id,nameSynthetic,period`；RunSummary 至少含 `id,enterpriseId,createdAt,status`，失败/未完成项不提供不存在的报告下载。

注册界面分账号信息与企业初始信息两步。为复用现有 `EnterpriseInput`，需收集合成企业名、事实期间、地区和行业（现有必需事实），其余未知事实不补 0；不是只传企业名却依赖后端猜期间/地区。如需“仅名称注册”，须由 M1/M2 另审不完整企业模型和后续补全流程，不能绕过原校验。

### 3.3 存储、旧入口与安全

- 建议新增用户表（唯一规范化 username、Argon2 密码哈希、role、状态）和可撤销 DB 会话表（只存令牌摘要、userId、expiresAt、revokedAt）；企业 owner 唯一，服务端阻止一账号创建第二家企业。审核员密钥与 `ADMIN_TOKEN` 分开、可轮换、不写前端配置/日志；普通注册不接受 role。保底账号由部署初始化受控建立，初始密码通过机密渠道提供。
- 会话 Cookie 建议 `HttpOnly; Secure; SameSite=Lax; Path=/`（本机 HTTP 开发例外由 M1 明确）；所有写请求校验同源 Origin，登录后校验 CSRF token；跨源开发允许源及凭据头由 M1/M2 明确。登录/注册/密钥试错限流；固定过期时间及密码/用户名规则在 E0 冻结。
- 原 API-08 `POST /enterprises` 不再允许新账号绕过注册创建第二家企业；注册复用同一内部校验/创建服务。保留脚本入口的权限由 D1 决定。API-09/10/11/12/13 都新增服务端归属检查，不能只改管理接口。
- 现有合成企业与历史 run 保持评测用途，owner 可为空但对普通账号不可见；不能把第一条历史企业认领给第一个注册用户。新运行记录发起 userId；旧 run 无发起人时企业轨迹入口拒绝开放。保底账号的企业同样需合成资料、明确创建；不占用原评测企业。
- 建议保留 `ADMIN_TOKEN` 给受限运维脚本、保留演示门禁作外层访问控制；**此为 M1 待定方案**。旧 DemoSession 本身不能成为用户身份，旧 Bearer 不能作为公众可用的账号绕过入口。须列明网关/脚本可达范围及每条旧接口授权方式，测试无新账号会话时不可读取新账号数据。
- 登出/换账号后 M2 清企业、run、报告、审核缓存和未保存敏感状态；请求中的 enterpriseId 不可信，异步旧账号响应不得覆盖新账号界面。保留主题偏好可独立处理。

## 4. #47：政策发现、候选与人工提交

### 4.1 状态与持久化边界

候选可持久保存在独立暂存区（来源文本/附件、元数据、结构化条件及 findings），**不得提前写 `policy/policy_version/criterion` 或出现在公开政策列表**。运行/审核审计与已提交政策不随候选轮换删除。候选提交前必须有结构化抽取结果；抽取失败保留明确失败状态供人工处理，不伪装完整候选。

建议状态：发现运行 `running/succeeded/partial_failed/failed`；候选 `pending_review/extraction_failed/submitted/discarded/expired`。列表无候选须结合运行状态显示“未到期/运行中/没有新政策/抓取失败/部分失败”，不能一律显示“暂无更新”。编辑 candidate 只改变候选 revision，不改变正式政策。

每候选必须含 `id,runId,revision,sourceUrl,fetchedAt,contentHash,metadata,criteriaDrafts,validationFindings,status`；额外保留 `sources`、源字段的出处、`duplicateOf` / `existingPolicyId` / `existingVersion`（可空）、缺失字段及抽取用量。metadata 使用 `PolicyImportMetadata` 相同字段名，但候选阶段未知值可空；**不能直接复用现有全部必填 schema**。`SourceSpan` 与候选 sources 使用稳定 UUID，转为草稿时按同一导入校验重新建立/验证引用，不相信客户端的 passed 标记。

保存期限按已确认要求：**下一次实际自动抓取新政策开始前，上一批未提交候选过期**；固定 timer 空轮询不算新抓取。建议原子轮换：获得任务锁后锁定旧批次，标记过期并登记新 run，然后开始联网；与提交竞争时只允许提交或过期其中一个成功。若下一次抓取失败，旧候选仍显示已过期，页面展示失败原因，不自动恢复可提交状态。该具体事务边界由 D3 复核；不能擅自改成永久保留或“只在下一次成功后清理”。已提交草稿及来源证据长期按现有政策历史规则保存；旧候选正文/附件可回收，最小运行审计与去重指纹保留期限由 M1/M3 明确。

### 4.2 建议端点

| 编号（草案） | 方法/路径 | 请求/响应及约束 |
| --- | --- | --- |
| DISC-01 | `GET /admin/discovery/settings` | `200 DiscoverySettings`＋ETag；含 enabled、sourceUrls[]、intervalMinutes、timeZone、nextRunAt、服务端可配置范围 limits |
| DISC-02 | `PUT /admin/discovery/settings` | If-Match；只允许修改 enabled/sourceUrls/intervalMinutes/timeZone，nextRunAt/limits 服务端计算；200＋新 ETag |
| DISC-03 | `GET /admin/discovery/runs?cursor=` | 运行列表：id、计划/实际开始/完成时间、status、来源级状态、发现/新增/重复/失败数量、错误类别及预算结果；无凭据或堆栈 |
| DISC-04 | `GET /admin/policy-candidates?status=&cursor=` | CandidateSummary 列表，包含来源/抓取时间/哈希、批次、处理/过期状态与重复提示 |
| DISC-05 | `GET /admin/policy-candidates/{candidateId}` | `200 CandidateDetail`＋ETag，含正文定位、待核元数据、结构化条件、findings 与用量 |
| DISC-06 | `PATCH /admin/policy-candidates/{candidateId}` | If-Match；只改 metadata/criteriaDrafts/目标 policyId 与 version；来源 URL/哈希/抓取时间等取证字段只读；服务端重校验并返回新 ETag |
| DISC-07 | `POST /admin/policy-candidates/{candidateId}/submit` | If-Match＋Idempotency-Key；服务端读取已保存候选；201 PolicyDraft＋ETag；响应同时可通过 candidate 查到 submittedPolicyId/submittedVersion |
| DISC-08 | `POST /admin/policy-candidates/{candidateId}/discard` | If-Match；`{reason?}`；200 更新候选；无政策写入；重复操作和已过期操作按 409 提示当前状态 |

所有端点服务端校验 reviewer。首版调度由 CLI 触发，不新增浏览器直接触发抓取的接口；原 API-04 手动导入不依赖 timer 或候选系统。

### 4.3 执行链与失败处理

1. 固定间隔 `systemd timer` 启动 CLI；读取后台配置，只有 enabled 且到 nextRunAt 才运行。以跨进程锁/租约防止重叠；配置修改不改变运行中的配置快照。建议停机后最多补一次到期任务，不补跑所有错过周期；具体最短周期/补跑在 D3 冻结并回显 limits，不能承诺“永不被封”。
2. 首批配置为现有 `data/policies/source_register.csv` 对应网站的明确网址列表，如 `www.gov.cn`、`www.sz.gov.cn`、`www.most.gov.cn`、`www.chinatax.gov.cn`；域名存在不等于具体栏目已验证可抓。M3 提供栏目/入口样本，M1 验证解析支持、robots/访问规则、限速与超时。页面/附件/每次重定向均重新校验 HTTPS、官方白名单和 DNS 解析结果，禁止回环/内网/链路本地等地址及 DNS 重绑定绕过。
3. URL 规范化＋正文内容哈希判重。相同内容新 URL 关联既有候选/政策；同 URL 内容变化标记新候选并要求审核员确认版本，不覆盖旧政策。同批并发去重有唯一约束；跨批保留最小指纹及处理结果，不能因清理正文而每轮重复推送已提交或丢弃的相同内容。
4. 限制每站频率、文件大小/类型、重定向、超时/并发、每轮条数及模型预算；DeepSeek 抽取调用前保守估算并预留输出预算，超预算停止后续调用并显式记录，不静默截断正文。用量未知时标记未知、停止继续消耗预算，不把 0 当实际零费用。外部政策内容只作为数据，不执行正文指令或改变抓取范围。
5. 复用 extraction/citations 的内部能力，在暂存 sources 上抽取 AST 与引用；禁止自动生成 `manual_review`。未确认元数据留空、显示 findings。审核员补全现有导入必填字段、核对结构化条件后才能提交。
6. 提交通过共享 ingestion/仓储校验创建草稿（相当于 API-04，初态 pending_parse），再通过共享保存/引用校验逻辑保存人工确认条件（相当于 API-06，进入 pending_review）。不得从浏览器串行调用两个接口冒充原子提交；关联 candidate、创建草稿、保存条件/来源应事务一致，失败可重试且不留下孤儿草稿，外部对象写入也须有失败清理/恢复策略。
7. 结构/必填/版本冲突阻止提交；引用 findings 可随草稿保留，仍由现有发布闸门拦住。候选审核不是最终发布，API-07 继续要求人工确认与重新校验。只有之后发布才触发现有失效逻辑。

## 5. #48：图形、数据口径与接口

### 5.1 七项交付与数据来源

| 图/页面 | 首版数据和算法边界 | 缺口与交付批次 |
| --- | --- | --- |
| 1 资格逻辑树 / 政策详情 | API-03 的 criteria.expression/sourceSpans；按 API-11/12 的 policyVersionMap 指定 version，criterionId 关联结果/事实引用 | E3a 可画真实 AND/OR/NOT 结构＋条款状态；AST 子节点无独立结果则标“未提供节点结果”，不可照抄父状态。若要求每节点四态，M1 扩展节点级快照后再着色 |
| 2 完整度 / 企业画像 | 已填写适用字段 / 适用字段总数，按已签收分组，未知/未填写不计完成、不适用不入分母 | E3b；新增适用字段清单/计算响应。`0`、`false` 是已填写，verified=false 不等于未填；“不适用”不能由缺值推断 |
| 3 结果分布 / 机会列表 | 前端聚合本次 run.assessments；四态计数总和等于本次已分析政策数；stale 是正交子集标记 | E3a，无新增聚合 API；候选/未分析政策不入分母，筛选显示统计范围，不把 stale 当第五态 |
| 4 时间线 / 机会、清单、报告 | 已核实 applicationWindow、effectiveFrom/To、handlingMode；绑定所展示政策版本，分开有效期与申报期 | E3a 先核对日期可信度；当前 date 值存在不能证明已核实，登记表有推算日期。未知/未核实显示说明不推算；税收不伪造申报窗口 |
| 5 分析流程 / 轨迹 | API-14 实际 steps，离线政策处理与在线企业分析分组；保持 sequence、状态、版本、错误 | E3a 先管理员；E1 后本人可见。无步骤即未记录，不画成已完成；企业投影不暴露审核员/其他企业或后台细节 |
| 审核工作量 / 审核端 | Asia/Shanghai 按日：新草稿创建数、可追溯人工审核完成事件数、日结束时待办快照 | E3b，补事件身份/唯一键及每日快照，缺历史留缺测 |
| 系统运行趋势 / 审核端 | Asia/Shanghai 按日：assessment_run 次数、成功、失败、未完成、完整起止时间耗时 | E3b，先补生命周期采集；不解析 model_call 日志，不将历史同值时间当 0ms |

第 6 项政策审核/版本变化图及差异对照本轮不实施。图形与文字/表格共同表达，空/未知/失效/错误分别呈现；截图里的样例必须标合成，不用于真实监控。

### 5.2 必要的建议接口/字段

| 编号（草案） | 接口或扩展 | 约束 |
| --- | --- | --- |
| VIS-01 | `GET /enterprises/{enterpriseId}/completeness?periodStart=&periodEnd=` | 本人或审核员；200 包含 enterpriseId、factVersion、period、definitionVersion、groups、filledCount、applicableCount、ratio。每组提供字段清单/FactRef、填写状态、适用性及依据，支持定位；分母为 0 时 ratio=null、显示“不适用” |
| VIS-02 | 复用 API-03 expression；按需扩展 API-11/12 的条件结果节点信息 | 建议 nodePath、result、reason、factRefs、evidenceRefs；节点 ID 只在给定 policy/version/criterion 内稳定。旧 run 无快照返回未提供，不用当前事实补算旧节点状态；节点设计须 M1 确认后进 OpenAPI |
| VIS-03 | 扩展 API-14 权限及返回的轨迹分组标记 | 路径不变，审核员完整视图、本人脱敏视图；服务端统一策略，旧 AdminBearer 是否继续允许由 D1 决定。建议每步 process=policy_offline/assessment_online；不放开 /admin 其他路由 |
| VIS-04 | `GET /admin/metrics/review-workload?from=&to=` | 审核员；按日返回 newDrafts、reviewsCompleted、pendingAtEndOfDay 及指标级 availability；当前日 pendingAtEndOfDay=null，不伪装已结算日 |
| VIS-05 | `GET /admin/metrics/assessment-runs?from=&to=` | 审核员；按日返回 total、succeeded、failed、unfinished、durationSampleCount、avgDurationMs 及指标级 availability；缺测为 null |
| VIS-06 | API-03/PolicySummary 的日期来源状态（如无其他可靠承载则新增） | 对 effectiveFrom/effectiveTo/applicationWindow 分别给 verified/unverified/unknown 与可追溯来源；不将“有值”默认为 verified。M1/M3 核对字段承载与既有日期数据后冻结 |

完整度定义由 M3 给业务清单、M1 按现有 18 个 FactKey/FactRef 编码，M2 核对定位；不能直接拿 `facts.length/18`。年度值、日期字段和季度起末值如何成为计数单位要写进 definitionVersion：建议以适用 `(key,period)` 为单位，8 个季度起末输入与一个年度字段不能混为同一事实。无法判定适用性的字段单列 unknownApplicability，整图不显示误导性总百分比；三方确认后再实现。

### 5.3 统计约定（查询形状为建议，业务口径依据 #48）

两类趋势响应建议统一带 `timeZone:"Asia/Shanghai",from,to,generatedAt,collectionStartedAt,coverage,items[]`；from/to 是当地日期且首尾包含，首版建议最多 31 天，超限 422；具体限制在 E0 冻结。每个指标分别标 `available/not_collected/partial`，避免“草稿计数可读”被误解为“日终待办也可读”。查询失败走非 2xx 错误，不返回全零；未采集/缺测为 null，完成采集后实际为零才返回 0。

- 审核工作量：newDrafts 按新建 **policy version** 计一次；reviewsCompleted 按成功且可追溯的人工审核完成事件计，建议唯一 `(policyVersionId,reviewRevision)`，含操作者/时间，幂等重放不重复。当前只有发布时的 human_review/approve，可将该动作作为现有流程中的审核完成事件；这是“审核事件”语义，不是简单 count published，M1/M3 在 D5 签收。候选提交/丢弃不计正式政策审核完成，提交生成的新版本才计新草稿。
- 待处理量：当天结束（下一天 00:00 边界）所有 pending_parse/pending_review 正式版本的存量快照；不是每日新增之和，不含未提交候选。新一天运行的快照任务须使用可还原边界状态的事件或准点快照；缺事件/错过采集不能用次日当前值补昨日日终。
- 运行量：按实际开始时间归日（不按用户传入的 asOf）；同一个 runId 只计一次。运行横跨午夜仍归开始日；已完成数可在后续查询更新，响应给 generatedAt。total = succeeded + failed + unfinished；queued/running 均单列未完成，不算失败。缓存命中仍产生新 run，按真实运行计；解释降级但规则结果正常返回是 succeeded，可另注明降级，不把它伪装成运行失败。
- 耗时：仅纳入有可信 startedAt/finishedAt、非负差值且已结束的运行；avgDurationMs 的分母为 durationSampleCount，没有样本返回 null。不得相加各 trace elapsedMs 冒充端到端耗时。旧同值占位记录不进入样本，保留原记录并显示覆盖边界。
- 生命周期：鉴权和请求校验后建立运行，执行结束推进 succeeded/failed 与 finishedAt，失败保留错误类别；服务中断遗留 running 需恢复核查，不能直接认定成功。写入失败/采集不可用必须报告缺口，不能声称包含所有 HTTP 错误；未建立 run 的参数错误、未授权请求不属于本指标。现有不可变结果快照与触发器需兼容，不能覆盖旧报告。
- 新生命周期引入 failed/未完成记录后，API-12/13 不得把无结果记录序列化成完整 AssessmentRun/报告；建议返回 `409 RUN_NOT_COMPLETED` 并在运行列表显示真实状态，具体错误进入契约评审。`succeeded` 才能读完整结果，失败记录不能因缺少 assessments 而产生 500。

## 6. 数据迁移、部署与签收

本地迁移目前到 `008_material_requirements.sql`；下一编号应从实际最新序号之后追加，不在多人 PR 中同时占用 009，也不修改 001—008。候选最小持久化、审核事件/日终快照和运行起止时间确实需要新增存储；先复用现有表/服务，不为图形专门建立通用事件平台或任务队列。

迁移验收：空库及已有 001—008 的隔离库均可升级；旧企业/历史报告与来源不丢失；唯一账号归属约束在并发注册中有效；已发布版本不可变；统计只从可信覆盖起点展示。记录迁移 checksum、行数、执行/恢复结果。回滚须先关闭新增入口及 timer、撤销/隔离新账号访问，恢复与数据模型兼容的应用；**不得直接退回不检查 owner 的旧公开接口继续服务新账号数据**。不删除候选已转成的政策，也不改历史运行来凑统计。

部署配置由 M1 增补 `.env.example`（仅占位）、systemd timer/CLI、保底账号初始化、密钥轮换、旧门禁/脚本及恢复步骤；M2 同步登录入口、类型/客户端、状态清理与图表；M3 同步字段定义、来源和验收样本。每个工作包交付同版 OpenAPI＋类型＋后端 schema＋示例＋测试和部署约束；本草案未签收处以 [开发计划 D1—D6](DEVELOPMENT_PLAN.md#4-开工前待定项) 跟进。
