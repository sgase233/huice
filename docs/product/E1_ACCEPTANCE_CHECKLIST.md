# E1 账号与归属验收准备清单（AUTH-AT-01—06）

> **状态：M3 执行准备，2026-11-12。不是验收通过记录，也不是任何成员的签收。** 本文把 [ACCEPTANCE.md](ACCEPTANCE.md) 扩展矩阵中的 AUTH-AT-01—06 展开为可执行步骤；用例编号沿用原矩阵，`-A/-B/...` 只是同一用例下的执行步骤，不另立一套验收标准。
>
> **本轮基线（2026-11-12 实际核对）：** `origin/main` = `ea44ae062d750757496215d122e060269eee10e9`（2026-11-12 09:27:27 +0800，合并 PR #61）；正式契约 `contracts/openapi.yaml` = `1.1.0-rc.1`（候选版本，23 个操作）；迁移 `infra/sql/009_users_sessions.sql`；PR #61 已合并（2026-11-12T01:27:27Z，merge `ea44ae0`，三项 CI 检查 success）；PR #62 仍为 open（head `4ed6beec47c3c6afdcf2014e1eead770cbbae0c9`，base `m2/frontend-work-plan-1110`）；issue #59 仍为 open（0 条评论）。
>
> **边界：** 本文只写准备、步骤、预期与证据形式；实际结果一律回填 [ACCEPTANCE.md](ACCEPTANCE.md) 的执行记录表。没有真实账号、同版环境和证据时，保持“未执行/阻塞”，不用 mock、作者自述、CI 通过或另一版本环境冒充联合验收。本文不写任何真实口令、演示 cookie、审核员密钥或连接串。

## 1. 本轮已核对的事实与未决状态

| 项 | 2026-11-12 实际状态 | 对验收的影响 |
| --- | --- | --- |
| 代码基线 | `origin/main` `ea44ae0`；PR #61 已合并；合并提交三项 CI 检查 success | 只能证明该提交通过自动检查，不等于 E1 联合验收 |
| 正式契约 | `1.1.0-rc.1`；AUTH-01—07 七个端点已并入；`x-e1-gate` 与 `x-e1-gate-model` 明确“门禁 → 请求体解析与字段校验 → 身份 → 角色 → 对象归属 → CSRF/Origin” | 验收按此顺序判错误码；候选版本最终口径仍需 M1/M2 确认 |
| 迁移 | `009_users_sessions.sql`：`app_user`、`user_session`、`demo_quota`；`enterprise.owner_user_id` 部分唯一索引；`assessment_run.user_id`；历史数据保持无主 | 验收环境必须实际执行 009；空库与旧库升级都要由 M1 留证 |
| PR #62 | open；head `4ed6bee`；base 为 M2 工作分支，不是 `main`；PR 自述浏览器回归使用内存仓储做合成账号真实 HTTP，未验证 PostgreSQL 事务、正式部署或完整真库审核发布流程 | 前端登录、账号切换、缓存清理、错误态只能待 #62 合并后做同版联合验证 |
| issue #59 | open；跟踪 E1 实施与验证；启动条件中的“隔离环境、两个合成企业账号和一个审核员账号”尚未勾选 | 是 M3 执行 AUTH-AT 的主要外部阻塞 |
| 云端部署 | `https://139.9.33.46/api/v1/health` 返回 `200`、`version=1.0.0-rc.10` | 不是 `1.1.0-rc.1` 同版环境，不能用于 E1 最终判定；2026-11-12 匿名探测见 [ACCEPTANCE.md](ACCEPTANCE.md) |
| 本地环境 | 当前工作机没有 `services/api/.venv`、Docker、本地 PostgreSQL；全局 Python 缺 FastAPI 等依赖 | M3 无法在本机启动后端、真库测试或浏览器联合流程；不把静态阅读写成实测 |
| M3 文件范围 | `data/**`、`docs/product/**`、`docs/ui/**`、`docs/demo/**`、`docs/diagrams/**`、`docs/devlog/M3.md` | 后端、正式契约、基础设施、前端分别由 M1/M2 负责；本文只提证据与建议 |

## 2. 先分清五个失败层次

E1 的失败码不能混记。验收时每一条请求都要标明它失败在哪一层，尤其是“受限演示门禁失效”和“登录会话失效”：

| 层次 | 典型状态/错误码 | 业务含义 | 记录要求 |
| --- | --- | --- | --- |
| 外层演示门禁 | `403 DEMO_SESSION_REQUIRED` | restricted 部署缺少/损坏/过期 `huice_demo_session`；连注册、登录也会先被拦下 | 记“门禁失效”，不记“账号密码错误”或“会话过期” |
| 身份缺失/无效 | `401 UNAUTHENTICATED`、`401 SESSION_INVALID`、`401 TOKEN_INVALID`、`401 DEMO_SESSION_INVALID`（local） | 没有提供凭证，或提供了但无效；不得降级到 demo/Bearer | 记“身份失效”，附具体码与 `details.reason` |
| 身份有效但角色不够 | `403 ROLE_FORBIDDEN` | 例如有效 demo cookie 访问 `/auth/me`、企业账号访问后台写接口、运维 Bearer 访问业务接口 | 记“有效但不足”，不记“未登录” |
| Origin/CSRF | `403 ORIGIN_MISMATCH`、`403 CSRF_REQUIRED` | 写请求来源不同源或缺少/不匹配 `X-CSRF-Token`；Origin 先于 CSRF 判定 | 记请求头实际值（可脱敏），不记成角色错误 |
| 对象归属 | `404 ENTERPRISE_NOT_FOUND`、`404 RUN_NOT_FOUND` | 对象不存在与“存在但不属于你”同码同文案；不能据此探测他人对象是否存在 | 记响应体与文案，不只记状态码 |
| 业务校验/限流 | `422 VALIDATION_ERROR`、`429 ACCOUNT_LOCKED`、`409 USERNAME_TAKEN`/`ENTERPRISE_ALREADY_BOUND`、`409 RUN_NOT_COMPLETED` | 请求体、并发/重复、未成功运行报告等业务结果 | 记字段错误、`Retry-After`、`details.status` 等 |

**门禁与身份是两层：** restricted 下先要 `huice_demo_session`，再谈 `huice_session`；有有效 demo cookie 但没有用户会话，`/auth/me` 是 `403 ROLE_FORBIDDEN`，不是 `401`；用户会话失效时带有效 demo cookie 仍是 `401 SESSION_INVALID`，不会掉回 demo。两类失败必须在记录中分列。

## 3. 合成账号与样本准备

以下均为**占位标识**，不是已创建的账号；实际用户名、口令、cookie、密钥由 M1 通过机密渠道提供，不写进仓库、文档、日志或截图。

| 样本 ID | 角色/身份 | 合成企业/数据 | 本轮用途 | 提供人 |
| --- | --- | --- | --- | --- |
| E1-A | enterprise 账号 | 合成企业 A：`nameSynthetic` 明确标注合成；期间 2026-01-01—2026-12-31；至少 `region=440300`、`industry=C39`；一条成功 run 与可下载报告 | 企业 A 的本人画像、run、报告；越权访问的“他人对象” | M1 创建，M3 使用 |
| E1-B | enterprise 账号 | 合成企业 B：同期间；与 A 不同的合成名称/事实值；一条成功 run 与可下载报告 | 企业 B 的本人画像、run、报告；与 A 互测 | M1 创建，M3 使用 |
| E1-R | reviewer 账号 | 不绑定企业（或按 M1 保底初始化建出的审核员）；可读全部业务数据 | 全局读取、只读边界、当前后台政策发布权限（候选/统计另列后续阶段） | M1 按 `create-admin.py` 或密钥注册创建 |
| E1-DEMO | demo 访客 | 有效 `huice_demo_session`（restricted 模式） | 门禁层、demo 仅无主数据、与账号会话分层 | M1 用 `issue-session.py` 在受控窗口签发 |
| HIST-U | 历史无主样本 | 现有 6 家合成企业及其历史 run/报告，`owner_user_id`/`user_id` 为 `NULL` | 历史无主数据不自动归新账号；审核员可读、企业账号不可见、demo 仅无主 | 现有库数据，不新建/不改归属 |
| E1-A-NR | 非成功 run（可选） | 若 M1 能提供 `failed`/`queued`/`running` 的 run 及无结果记录 | 报告 409、运行列表状态、API-12 未完成语义 | M1 提供；若当前实现不产生，则该项标阻塞并在契约/实现间提差异 |

**样本准备要求：**

1. 只在隔离数据库或隔离 schema 中创建；不得使用真实企业、真实统一社会信用代码、真实纳税/申报材料或生产库。
2. 创建前由 M1 记录基线计数：`app_user`、`enterprise`、`assessment_run`、`assessment`、`user_session`、`demo_quota` 的行数，以及无主企业/无主 run 的 ID 清单。
3. 账号 A/B 各自的企业、画像、run、报告必须能在服务端建立归属；不得手工把历史无主企业改给 A/B。
4. 每个样本记录：账号 ID、企业 ID、run ID、报告下载时间、`factVersion`、`policyVersionMap`、数据库行数快照。截图中不得出现口令、cookie、密钥或真实企业资料。
5. 失败/未完成 run 的构造方式由 M1 说明（例如测试用受控插入或故障注入）；M3 不直接写库，也不把单元测试夹具当成真实 HTTP 结果。

## 4. AUTH-AT-01—06 执行步骤

### AUTH-AT-01 注册事务、唯一归属与提权

| 项 | 内容 |
| --- | --- |
| 前置条件 | 隔离环境已执行迁移 009；restricted 下已取得有效 demo cookie；`PUBLIC_ORIGIN` 与请求 `Origin` 同源；M1 可查隔离库计数；`REVIEWER_SIGNUP_KEY` 已配置（测审核员密钥分支时） |
| 合成账号/样本 | 新用户名 `e1-ent-a`、`e1-ent-b`（占位）；重复用户名；错误/正确审核员密钥；并发同名注册 |
| 操作步骤 | 1. 注册 A（带企业四要素）→ 记录 201 与 `enterpriseId`。2. 注册 B → 记录 201。3. 用同一用户名再注册 → 期望 409 `USERNAME_TAKEN`。4. 不提供密钥且不带 `enterprise` → 期望 422。5. 提供错误密钥 → 期望 403 `ROLE_FORBIDDEN`。6. 提供正确密钥、不带企业 → 期望 201 reviewer、`enterpriseId=null`。7. 两个并发请求注册同一新用户名 → 期望一个 201、一个 409，库里只有一行。8. 在请求体塞 `role: reviewer`（无密钥）→ 期望角色仍为 enterprise 或被 schema 拒绝，不能提权。9. 核对注册事务后的账号、企业与归属，确认没有半个账号或第二家企业；注册本身不建立登录会话，响应只有 `user`，不下发 `huice_session`。10. A/B 的登录、会话创建与 `/auth/me` 恢复放到 AUTH-AT-04 执行，不并入注册用例。 |
| 预期结果 | 成功路径账号＋企业同事务提交；注册本身不创建登录会话（登录会话在 AUTH-AT-02/04 验证）；失败请求不得新增账号、企业或归属（已存在的账号不因重复注册被改写）；重复用户名 409；普通注册不能自选 reviewer；响应不含密码哈希、注册密钥、会话凭据；错误响应带 `X-Trace-Id` |
| 证据形式 | 每个请求的方法/路径/状态码/错误码/traceId；隔离库前后计数与用户名查询结果（M1 提供）；并发测试的两次响应；截图只截合成数据 |
| 主执行/复核 | M1（主）/ M2、M3（复核） |
| 依赖/阻塞 | 隔离环境、迁移 009、有效 demo 门禁、M1 数据库查询；当前未提供，未执行 |

### AUTH-AT-02 保底审核员与注册密钥

| 项 | 内容 |
| --- | --- |
| 前置条件 | 全新或受控隔离环境没有 reviewer；持有服务器 root 的 M1 可运行 `infra/deploy/create-admin.py`；`REVIEWER_SIGNUP_KEY` 可配置/轮换；日志与构建产物可检索 |
| 合成账号/样本 | `e1-reviewer`（占位）；正确/错误/轮换后的旧密钥；首次登录口令（机密渠道提供，验收后立即改密） |
| 操作步骤 | 1. 用环境变量运行 `create-admin.py`（不把口令放命令行参数）→ 记录退出码、stdout 是否只含用户名。2. 查库确认 `role=reviewer`、无企业、口令为哈希。3. 用该账号登录 → 200；`/auth/me` 返回 reviewer、`enterpriseId=null`。4. 用正确密钥注册新 reviewer → 201；错误密钥 → 403；未配置密钥 → 同样 403 且不回显配置状态；轮换后的旧密钥 → 403。5. 连续错误尝试 → 第 6 次起 429 `ACCOUNT_LOCKED`，检查 `Retry-After`。6. 在响应体、日志、构建产物、前端包中检索口令/密钥/cookie，确认无泄露。 |
| 预期结果 | 保底账号可登录且只有 reviewer 权限；密钥错误与未配置同码同文案；旧密钥轮换后失效；机密不进入响应/日志/构建产物；限流按 IP 生效 |
| 证据形式 | 脚本退出码、库内账号行（脱敏）、登录/注册响应、日志检索结果（只记命中/未命中，不贴值）、限流响应 |
| 主执行/复核 | M1（主）/ M2、M3（复核） |
| 依赖/阻塞 | 服务器 root、机密渠道、隔离库；当前未提供，未执行 |

### AUTH-AT-03 两个企业账号互测与审核员只读边界

| 项 | 内容 |
| --- | --- |
| 前置条件 | E1-A、E1-B 各自已有企业、画像、成功 run、报告；E1-R 可登录；HIST-U 无主样本存在；M1 可查归属列 |
| 合成账号/样本 | E1-A、E1-B、E1-R、HIST-U；每账号的 enterpriseId/runId/report URL；一个不存在的 UUID 作为“同码同文案”对照 |
| 操作步骤 | 1. A 登录，`GET /enterprises` 只返回 A；`GET /enterprises/{A}` 200；`GET /enterprises/{B}` 404；`PATCH /enterprises/{B}/facts/{key}` 404；`POST /assessment-runs` 指向 B 的企业 404；`GET /assessment-runs/{B-run}`、`/report.md`、`/trace` 均 404。2. A 的 `GET /assessment-runs` 只返回 A 的 run；用 B 的游标续页 → 400/422；用不存在的 enterpriseId 筛选 → 404。3. B 对称执行，确认 B 也看不到 A。4. A 下载自己的报告 → 200；下载 B 的报告 → 404；若 E1-A-NR 存在，下载非成功 run 的报告 → 409 `RUN_NOT_COMPLETED`，`details.status` 为真实状态。5. E1-R 登录：`GET /enterprises` 含全部（含 HIST-U）；读取 A/B/HIST-U 的企业、run、报告、轨迹 → 200；`PATCH` A 或 HIST-U 的事实 → 404；对他人企业发起 run → 404。6. 核对 A/B/HIST-U 的 `owner_user_id`/`user_id` 未被读取动作改变。 |
| 预期结果 | 企业账号只能读/写本人企业及本人 run；越权与不存在同码同文案；审核员可全局读，但不能因读权修改他人事实；报告只对成功 run 提供；无主数据不被自动认领；分页游标不越权 |
| 证据形式 | 两组账号的请求矩阵（方法/路径/状态码/错误码/traceId）；响应体中的错误码与文案；数据库归属前后对比；报告下载文件哈希或截图（合成内容） |
| 主执行/复核 | M1（API）/ M2（#62 页面）/ M3（业务复核） |
| 依赖/阻塞 | 两账号、一审核员、隔离库、#62 同版前端；当前未提供，未执行 |

### AUTH-AT-04 门禁、会话生命周期与切换

| 项 | 内容 |
| --- | --- |
| 前置条件 | restricted 环境；有效 demo cookie；E1-A/E1-B；可控制会话过期/撤销（DB 或短 TTL 配置）；#62 前端用于浏览器步骤 |
| 合成账号/样本 | E1-DEMO、E1-A、E1-B；过期/撤销/格式错误的 `huice_session`；错误 CSRF/Origin 请求 |
| 操作步骤 | **门禁层：** 1. 不带 demo cookie：`GET /enterprises` → 403 `DEMO_SESSION_REQUIRED`；`POST /auth/login` 用 `{}`、错类型、空体、非 JSON → 均 403（门禁先于请求体校验）；`GET /health` 200；`POST /auth/logout` 204。2. 损坏/过期 demo cookie → 同样 403。**身份层：** 3. 有效 demo cookie、无用户会话：`GET /auth/me` → 403 `ROLE_FORBIDDEN`；`GET /enterprises` → 200 且仅无主数据。4. 登录 A（带有效 demo）→ 200，检查 `Set-Cookie: huice_session` 的 HttpOnly/SameSite/Path/Secure（local 例外）；`/auth/me` 返回同一 `enterpriseId` 与 csrfToken。5. 刷新页面 → 会话恢复；`/auth/me` 不得缓存。6. 登出（带 CSRF+Origin）→ 204；重放旧 cookie → 401 `SESSION_INVALID`；再次登出 → 204。7. **重新登录 A** 建立新会话；再在另一个独立客户端/浏览器上下文登录 A，建立第二个独立会话（两个不同 `session_id`；同一浏览器登录会撤销其出示的有效旧会话，不能在同一 cookie jar 里制造两个会话）。8. 在第一个会话上修改密码 → 204；分别重放改密前的两个旧 cookie → 均 401 `SESSION_INVALID`；旧密码登录 → 401；新密码登录 → 200。9. 使会话过期（DB 改 `expires_at` 或空闲超时）→ 401 `SESSION_INVALID`；同时带有效 demo cookie 仍为 401，不掉回 demo。10. 缺 CSRF/Origin 的写请求 → 403 `CSRF_REQUIRED`/`ORIGIN_MISMATCH`；连续错误登录 → 429。 |
| 预期结果 | 门禁失败、身份失败、角色不足、CSRF/Origin 失败分别可辨；登出只撤销当前会话；改密撤销该账号全部会话（含当前会话与另一个独立会话）；无效会话不降级；Cookie 属性符合契约；限流生效 |
| 证据形式 | 每条请求的 Cookie/Origin/CSRF 头摘要（不贴明文 cookie）、状态码/错误码/traceId、Set-Cookie 属性、数据库 `user_session.revoked_at/expires_at` 行、浏览器网络面板截图（脱敏） |
| 主执行/复核 | M1（API）/ M2（浏览器）/ M3（业务语义复核） |
| 依赖/阻塞 | restricted 同版环境、有效 demo cookie、会话过期控制、#62；当前仅对 rc.10 做了匿名门禁探测，未执行账号步骤 |

### AUTH-AT-05 角色矩阵、后台与轨迹

| 项 | 内容 |
| --- | --- |
| 前置条件 | E1-A/E1-B/E1-R；一个成功 run 的轨迹；HIST-U 无主 run 轨迹；运维 Bearer（若仍保留）；restricted 演示 cookie |
| 合成账号/样本 | A 的 run、B 的 run、HIST-U run；**当前已存在**的后台政策列表/导入/抽取/保存/发布端点；`/assessment-runs/{runId}/trace`。候选（DISC-*）与统计（VIS-04/05）另列后续阶段，不作为本轮样本要求 |
| 操作步骤 | 1. A 登录：当前后台政策接口（`GET /admin/policies`、导入/抽取/保存/发布）→ 403 `ROLE_FORBIDDEN`；`GET` 自己的 trace → 200（**仅在 M1/M2 确认本人轨迹边界后执行**；否则标“边界待确认”）；读 B/HIST-U 的 trace → 404。2. E1-R：当前后台读 → 200；当前后台写/发布（在隔离测试政策上）→ 允许；A/B/HIST-U 的 trace → 200；`PATCH` 他人事实 → 404。3. 运维 Bearer：后台读 → 200；业务接口 → 403；后台写 → 403；轨迹 → 200（按契约当前口径）。4. E1-DEMO：后台 → 403；无主企业/run/trace → 200；有主企业/run/trace → 404。5. 检查 trace 响应只含阶段、状态、摘要、版本、错误与 `{type,id}` 对象引用；不得含提示词、密钥、真实原文或他人企业事实值；`objectRefs` 不得当政策引文。6. 候选与统计接口（DISC-*、VIS-04/05）当前未实现/未签收，本轮**不调用、不判权限**；待 E2/E3b 实现并签收后另测。 |
| 预期结果 | 当前后台政策接口的角色矩阵与契约一致；企业账号不能进后台；审核员可读全部但写入仍受归属限制；Bearer 只读且不读业务；demo 只读无主；本人轨迹边界未确认前不扩大开放；轨迹脱敏。候选/统计接口未实现时不作为权限缺陷，待实现并签收后另测 |
| 证据形式 | 角色×端点矩阵表（状态码/错误码/traceId）；trace 响应字段清单与脱敏检查；截图 |
| 主执行/复核 | M1（API）/ M2（页面）/ M3（业务语义） |
| 依赖/阻塞 | 本人轨迹脱敏投影边界仍未在 #59/PR #61 复审中定案；M2 PR #62 明确“本人轨迹入口保持关闭”；该项不得自行扩大。候选/统计接口依赖 E2/E3b，当前未实现，不纳入本轮。当前未执行 |

### AUTH-AT-06 旧通道、历史无主与账号切换

| 项 | 内容 |
| --- | --- |
| 前置条件 | 旧格式 `v1` demo cookie（只读）；运维 Bearer；E1-A/E1-B；HIST-U；#62 已合并的同版前端；隔离库 |
| 合成账号/样本 | 旧 v1 demo cookie、有效 v2 demo cookie、Bearer、A/B、HIST-U 企业/run |
| 操作步骤 | 1. 旧 v1 demo cookie：读无主数据 200；写操作 → 403 `DEMO_SESSION_LEGACY`；不得读有主企业。2. 运维 Bearer：后台读 200；业务读/写 403；不得用 Bearer 绕过新账号隔离。3. 注册 A 前后对比 HIST-U：`owner_user_id` 仍为 `NULL`，A 的企业列表不含 HIST-U；不得把第一条历史企业认领给 A。4. #62 前端：A 登录后切换 B/退出，企业、run、报告、审核缓存与未保存敏感状态清理；旧账号晚返回的响应不得覆盖新账号界面；主题偏好可保留。5. 门禁失效与会话失效在页面分别提示：`DEMO_SESSION_REQUIRED` 回演示入口；`ROLE_FORBIDDEN` 显示登录入口；`SESSION_INVALID/UNAUTHENTICATED` 跳登录；三者不得互相冒充。 |
| 预期结果 | 旧通道不绕过新隔离；历史无主数据不被认领；账号切换无缓存/异步串数据；页面错误分层与契约一致 |
| 证据形式 | 旧 cookie/Bearer 请求矩阵；注册前后无主数据计数；浏览器网络/缓存截图与 #62 测试结果（#62 结果只能作输入，不替代 M3 联合验收） |
| 主执行/复核 | M1（API）/ M2（前端）/ M3（业务复核） |
| 依赖/阻塞 | #62 尚未合并；同版前端与隔离环境未就绪；当前未执行 |

## 5. 当前执行状态（截至 2026-11-12）

| 用例 | 静态核对 | 实际执行 | 结论/阻塞 |
| --- | --- | --- | --- |
| AUTH-AT-01 | 契约、迁移 009、`tests/test_e1_auth_flow.py` 有注册/重复/密钥/用户名边界用例 | 未执行 | 缺隔离环境与账号；测试存在不等于 M3 联合验收 |
| AUTH-AT-02 | `infra/deploy/create-admin.py`、部署手册 §3.4、契约密钥错误口径已核对 | 未执行 | 缺服务器 root/机密渠道/隔离环境 |
| AUTH-AT-03 | `access.py` 的读写归属、`tests/test_e1_access.py` 的越权用例已核对 | 未执行 | 缺两账号、一审核员、隔离库；页面需 #62 |
| AUTH-AT-04 | 契约门禁模型、`identity.py` 真值表、`GatedRoute`、限流实现已核对 | **仅匿名门禁探测**（rc.10，非 1.1.0-rc.1 同版） | 不能判通过/失败；见 [ACCEPTANCE.md](ACCEPTANCE.md) 2026-11-12 记录 |
| AUTH-AT-05 | 当前后台政策接口的角色矩阵、`require_reviewer*`、轨迹 `require_run_access` 已核对；候选/统计接口尚未实现 | 未执行 | 本人轨迹边界待确认；候选/统计待 E2/E3b；缺账号/环境 |
| AUTH-AT-06 | 旧 v1 demo 只读、Bearer 只读、历史无主迁移设计已核对 | 未执行 | #62 未合并；缺同版前端/环境 |

**统计：** 0 个用例完成联合验收；1 个用例只有门禁层的部分匿名探测，且环境版本不一致；其余未执行。任何“CI 通过”“作者本地测试通过”“#62 内存仓储联调”都不能填成 M3 的通过记录。

## 6. 证据格式与保管

1. **ACCEPTANCE 执行记录表**每行至少填：实际日期、用例 ID、代码/契约/迁移版本、环境与样本/数据快照、预期与实际、runId/请求/截图等证据、缺陷及复测、执行/复核。缺任何一项都按“未完成”处理。
2. 请求证据用方法、路径、状态码、错误码、`X-Trace-Id`、关键响应字段；cookie/密钥/口令只记“已提供/已撤销/未泄露”，不贴明文。
3. 截图和录屏只含合成数据；不提交真实企业资料、数据库转储、`.env`、cookie 或密钥。仓库外证据目录需在记录中写明路径；其他成员无法访问时，不能当作已复核。
4. 门禁失败与会话失败分列；同一请求同时带 demo cookie 与用户会话时，写清实际走的是哪一层。
5. 失败/未完成/未覆盖如实写；复测要写新日期、版本和结果，不能覆盖原失败记录。
6. 不在验收表里填虚构账号、截图、运行 ID、签认人或“已通过”。

## 7. 需要 M1/M2/负责人明确的事项

### 7.1 M1

1. **隔离环境与账号交付：** 哪个环境、哪个库/schema、何时可用；两个企业账号、一个审核员如何创建；口令/密钥/cookie 如何通过机密渠道交付；验收后如何撤销和清理。不要把口令写进仓库、文档、命令行参数或日志。
2. **同版部署：** 当前 `https://139.9.33.46` 的 `/health` 是 `1.0.0-rc.10`，而本轮契约是 `1.1.0-rc.1`；请明确何时部署同版、迁移 009 是否已执行、部署 commit/指纹和回滚方式。M3 不在旧版本上冒充新契约验收。
3. **`queued` 生命周期：** 契约 AUTH-07 写“`queued` 只出现在 E1 起的新写入路径（受理即落行）”，但 `save_run()` 当前只在成功时插入 `status='succeeded'`，代码中没有 `queued` 写入路径。请确认：E1 是否实际产生 `queued`；若不产生，是改契约描述还是补实现。对应地，METRICS_SPEC 的运行三态/未完成口径要按实际实现更新。
4. **API-12 未完成语义：** API-13 已明确未成功 run 返回 `409 RUN_NOT_COMPLETED`；API-12 尚未在契约中规定。当前 `get_run()` 在 run 没有 assessment 行时返回 `404 RUN_NOT_FOUND`（“该判断运行没有结果”）。请定 API-12 对 `failed`/`queued`/`running`/无结果记录的稳定错误码，并补契约与测试。
5. **本人轨迹边界：** API-14 当前允许企业会话读取本人 run 的轨迹，响应结构只含阶段/状态/摘要/`{type,id}` 引用；但 PR #61 复审与 issue #59 仍把“本人轨迹脱敏投影边界”列为未完成，#62 明确“本人轨迹入口保持关闭”。请 M1/M2/M3 共同确认：API 是否继续允许企业会话读本人 trace、前端何时开放入口、脱敏字段清单是什么。未确认前 M3 不扩大访问范围。
6. **历史无主数据清单：** 提供 HIST-U 的企业 ID、run ID、报告 ID、`owner_user_id`/`user_id=NULL` 的核对结果，以及“注册不认领”的验证方法。
7. **契约/文档同步：** `docs/README.md`（仍写 rc.9 / 16 端点）、`docs/DEVELOPMENT_PLAN.md`（基线 `807e1bb`）、`docs/EXTENSION_INTERFACES.md`（仍写新增端点未实现）与 `contracts/openapi.yaml` 1.1.0-rc.1 不一致；这些文件由 M1/项目负责人维护，请按实际状态更新，M3 不代改。

### 7.2 M2

1. **#62 合并与版本：** #62 仍 open，base 不是 `main`；请明确 rebase/合并顺序、同版 `types.ts`、前端构建与部署窗口。main 已取入 #62 的 `types.ts`（`34f4c1f`），但 #62 的页面/客户端尚未合并，不能据此认为前端 E1 已完成。
2. **错误态分层：** 页面必须分别处理 `DEMO_SESSION_REQUIRED`、`ROLE_FORBIDDEN`、`SESSION_INVALID`/`UNAUTHENTICATED`、`ORIGIN_MISMATCH`/`CSRF_REQUIRED`；不要把门禁失效显示成“会话过期”，也不要把 demo 访客态清成未登录。
3. **切换/缓存：** 退出或切换账号时清理企业、run、报告、审核缓存与未保存敏感状态；旧请求晚返回不得覆盖新账号界面；请提供浏览器网络/缓存证据。
4. **本人轨迹入口：** #62 保持关闭是正确的；开放前需三方确认边界，不能仅因 API-14 契约允许就放入口。
5. **联合验收：** 提供同版构建、浏览器用例、截图/录屏和失败的复测；M2 的单元/模拟回归结果只作输入，不替代 M3 的真实账号联合验收。

### 7.3 项目负责人

1. 确认 E1 验收环境、账号交付责任人和验收窗口；当前 issue #59 的启动条件尚未满足。
2. 确认 `1.1.0-rc.1` 是否作为 E1 验收候选版本，以及 #62 合并/部署顺序。
3. 对“本人轨迹是否在 E1 开放入口”作范围决定；未决定前保持关闭，不以验收准备清单代替决定。

## 8. 本轮明确不做

- 不把 AUTH-AT-01—06 标为通过；不填虚构账号、run ID、截图、traceId 或签认人。
- 不用 mock、内存仓储、作者自述、CI 结果或 rc.10 环境冒充 1.1.0-rc.1 同版联合验收。
- 不修改 `services/api/**`、`contracts/**`、`infra/**`、`apps/web/**`；发现的问题只提供证据和建议。
- 不代替 M1/M2/负责人签收，不把 D3/D4/D5 草案写成已签收。
