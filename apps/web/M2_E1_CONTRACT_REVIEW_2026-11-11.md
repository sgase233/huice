# M2 E1 消费者逐端点初审

审查日期：2026-11-11；[PR #55](https://github.com/sgase233/huice/pull/55) head `4d145b9b670292a00648cb521ef0cd4cef64b70c`，草案 `contracts/openapi.e1-draft.yaml` / `1.1.0-rc.1-draft.1`，以现行 `openapi.yaml` rc.9 为基线。下表是**静态初审，非签收**；E0 提案、扩展设计和草案不一致处需 M1/M2/M3 共同确认。已有 [M2 评论](https://github.com/sgase233/huice/pull/55#issuecomment-6783599370) 要求修订后再逐项复核；本文件未发送、不冒充新评审反馈。

## AUTH-01—07

| 操作 | 草案中前端需消费的关键字段/行为 | 初审结论及阻塞问题 | 要求的验证 |
| --- | --- | --- | --- |
| AUTH-01 `POST /auth/register` | `username` 3—64、`password` 12—128；企业用户带 `enterprise`，审核员可带 `reviewerSignupKey` 且可不建企业；201 返回 `user`、不自动登录 | **需修改/澄清**：schema 只要求账号密码，企业或审核员密钥的条件必填仅在文字；缺两者、空/无效密钥、`enterprise` 与密钥组合的服务端错误和事务示例需明确。restricted 门禁与 `security: []` 的关系未闭合 | 企业缺画像、无密钥、错密钥、重复账号、限流；失败不得留下半个账号/企业；注册后明确转登录 |
| AUTH-02 `POST /auth/login` | `username/password`；200 返回 `user/expiresAt/csrfToken` 并设置 HttpOnly 会话 cookie；错误账号与密码统一提示 | **需修改/澄清**：端点描述应明确 Origin；失效旧 cookie 与登录的优先级、restricted 外层门禁及允许恢复路径需完整示例 | 正常、错误凭据、限流、缺/错 Origin、失效会话 cookie、demo cookie 共存；不记密码/令牌 |
| AUTH-03 `POST /auth/logout` | 成功 204；带会话写请求携带 `X-CSRF-Token` | **需修改**：描述“无有效会话也 204”与 `UserSession` 必需、401、全局无效凭据 401 冲突；无/失效/有效 cookie 的清除、Origin、CSRF 真值表缺失 | 无 cookie、过期/撤销、有效＋正确/缺失 CSRF、重复退出；分别核对服务端撤销与浏览器清理 |
| AUTH-04 `GET /auth/me` | 200 恢复 `user`（`enterpriseId` 可 null）、`expiresAt/csrfToken`；不得缓存 | **待修改后复审**：会话必需且失效不降级；restricted 门禁失败的状态和缓存头在部署模式下需明确，前端必须能转登录或演示入口 | 刷新恢复、过期/撤销、门禁缺失、网络失败；不展示旧用户数据 |
| AUTH-05 `POST /auth/password` | `currentPassword/newPassword`；204 撤销该用户全部会话并清 cookie | **待修改后复审**：Origin/CSRF、失败信封及 restricted 门禁组合；成功后的旧会话不可恢复 | 错旧密码保留会话；成功后旧会话全部失效、转登录；重放及 401/403 |
| AUTH-06 `GET /enterprises` | `{items,nextCursor}`；summary 含 `id/nameSynthetic/period/ownership`，无 facts；角色过滤和游标绑定在服务端 | **需修改/澄清**：`UserSession OR DemoSession` 与 restricted 门禁 AND 关系不一致；审核员 `other/unowned` 只读与本人写入须以对象鉴权证明 | 企业仅本人、审核员全局、demo 无主、Bearer 禁止；跨身份游标不泄露；空页/分页/未知 ownership 不冒充本人 |
| AUTH-07 `GET /assessment-runs` | 可选 `enterpriseId/cursor/limit`；summary 含 `status`、`reportAvailable`、时间与 ID；只有完整成功结果可下载报告 | **需修改/澄清**：凭证门禁同 AUTH-06；`queued/running/succeeded/failed` 与 D5 的 interrupted 恢复及 E1/E3 边界未统一；未知枚举不能按成功处理 | 角色过滤、越权游标、每种状态与 reportAvailable、跨午夜历史、409 `RUN_NOT_COMPLETED`；不得对失败/未完成显示报告入口 |

`User` 的 `role` 仅 `enterprise/reviewer`，`enterpriseId` 必填但可 null；`SessionResponse` 三字段均必填且 CSRF 至少 16 字符。`RegisterRequest` 条件业务必填仅由描述表达；不能靠生成类型的可选字段替代后端校验。初始时长、密码长度和正式版本仍需共同签收，不将草案值写成已确定部署规则。

## 现有 16 个操作：消费者影响初审

下表按草案 `x-e1-impact` 汇总变化，目标均待逐端点 schema、实例与权限矩阵复核；`client.ts` 的响应检查、ETag 和已有错误恢复不能仅凭路径不变认定兼容。

| 现行操作 | 草案方向及前端迁移/未决 |
| --- | --- |
| `GET /health` | 草案写不变；需确认门禁与无效 cookie 是否影响健康检查。 |
| `GET /policies`；`GET /policies/{policyId}` | 草案称门禁不变，登录后访问；需澄清 restricted 与会话关系，保留分页、筛选、历史 version 和错误恢复。 |
| `POST /admin/policies/import` | 浏览器写操作改 reviewer 会话，Bearer 403；须验证 multipart、CSRF、Origin、导入不确定状态和门禁。 |
| `POST /admin/policies/{id}/extract` | reviewer 会话写；需保留 If-Match 与幂等键和重放，确认 CSRF/Origin 及失效会话。 |
| `PUT /admin/policies/{id}/criteria`；`POST /admin/policies/{id}/publish` | reviewer 会话写；确认 ETag/409、未保存输入、人工确认发布，不能用旧 Bearer 页面绕过。 |
| `GET /admin/policies`；`GET /admin/policies/{id}` | 草案允许 reviewer 或只读运维 Bearer；企业会话拒绝；前端不再提供共享令牌输入，核对分页和详情 ETag。 |
| `POST /enterprises` | 企业账号已绑定则 409；未绑定 reviewer 可建本人企业，demo 受配额和无主约束；现有“复制为新样本”不能直接照搬到账号模式。 |
| `GET /enterprises/{id}` | 本人、审核员全局、demo 无主；越权 404；重新恢复身份时不能继续使用旧企业缓存。 |
| `PATCH /enterprises/{id}/facts/{key}` | 企业/审核员仅本人、demo 无主；保留 If-Match 与冲突恢复，添加 CSRF，服务端验证对象，不靠只读控件。 |
| `POST /assessment-runs` | 本人企业/审核员本人/demo 无主；确认 supersedesRunId、配额、ETag 相关语义与跨身份请求。 |
| `GET /assessment-runs/{id}`；`GET /assessment-runs/{id}/report.md` | 归属过滤；未完成 run 409、禁止下载；历史格式、四态、版本映射与 Markdown 必须逐字段复核。 |
| `GET /assessment-runs/{id}/trace` | 草案允许本人脱敏、reviewer/运维 Bearer 全量及 demo 无主；与扩展设计“本人轨迹须发起人校验”的范围和 demo 投影需共同明确。旧 run 无发起人不可作为本人轨迹开放；objectRefs 不是原文引文。 |

**跨所有受影响操作的阻塞项**：local/restricted 外层门禁、账号/运维/demo 多凭据先后及失效、Origin/CSRF、角色和对象过滤的组合需有机器可读约束或清晰补充真值表；每种错误的状态码、`error.code/message/details/traceId`、限流及 204/空体应给可执行示例。后端对象授权须隔离环境直接请求证实，前端隐藏导航不是权限证据。

## 本次检查的边界

先前已在隔离输出解析草案及引用、用仓库生成器函数生成并编译合并 schema 的临时类型，检查 8 个路径内联媒体示例；详见 [M2 日志](../../docs/devlog/M2.md)。没有覆盖所有引用响应示例、全部字段差异、并发、真实浏览器 Cookie、服务实现或 16 条旧操作的实际响应；本矩阵保留待决项，不可作通过记录。下一步待 M1 更新 head 后重新读取，逐项记录接受/需修改和实际证据，再决定签收与正式接入。


## draft.2 增量复审：`537e5a0ac360a203f4ebed4277f4da0728fffcf0`

复审日期 2026-11-11；从 GitHub 指定完整 SHA 只读取 `contracts/openapi.e1-draft.yaml`，对照本地基线 `aa5a63d` 的正式 rc.9。**本次为静态评审，不是签收；补充问题已于 2026-11-11 [发送至 PR #55](https://github.com/sgase233/huice/pull/55#issuecomment-6786980848)。**上面 draft.1 初审保留作为历史，不自动作为本版本结论。

### 需 M1 解释或修订

1. **AUTH-07 审核员筛选范围冲突。** 操作说明称审核员可看全部 run，`enterpriseId` 参数却写“非本人企业一律 404”。审核员查看其他企业时，筛选到底应成功还是 404，会改变列表与详情导航。请按 enterprise/reviewer/demo 分别规定可筛选对象（包括无主企业）、不存在及越权状态，并配同一对象的正反例。依据草案 `/assessment-runs` 说明和该参数的 `description`，约第 690、715 行。
2. **登录/注册遇到旧会话的规则未闭合。** 全局说所有已提供的无效凭证返回 401，唯一例外为登出；AUTH-01/02 却是 `security: []`，注册未列 401，登录 401 仅描述用户名密码错误。浏览器请求自动携带旧 cookie，不能由 JS 删除 HttpOnly cookie。请明确注册和登录是否忽略已有会话、先调用豁免门禁的登出，还是返回何种错误；列出 local/restricted 下有效、失效会话与 demo 门禁并存的例子。不能仅依据 `security: []` 推断服务端行为。
3. **AUTH-03 文本与参数存在条件差异。** 无有效会话时 Origin/CSRF 均不校验；有有效会话时两者必须校验。草案对登出采用可选参数符合条件性，但需明确缺失、错误 token 和 Origin 同时错误时的优先级，以及 403 是否保留 cookie 与现有服务端会话，避免客户端把失败当退出。当前表已明确有效会话缺 CSRF 时不撤销，但未覆盖全部组合；这是澄清请求，不预设服务端实现。
4. **AUTH-04 门禁响应未显式列出。** `GET /auth/me` 的 `x-e1-gate.restricted` 写明可返回 403 `DEMO_SESSION_REQUIRED`，但操作响应只有 200、401、5xx 与 `default`。建议显式列出 403 及示例，便于生成客户端和前端区分门禁过期与账号过期；`default` 不足以表达具体恢复路径。
5. **注册角色条件和错误映射仍缺可执行示例。** `reviewerSignupKey` 出现即审核员，未提供时企业画像必填；描述同时称审核员密钥“错误与缺失同样处理”。请明确无密钥且无画像、空/短密钥、密钥与画像共存、无效密钥的状态码/错误码，确保缺失密钥的企业注册与无画像失败可区分且事务回滚；不得让前端推断用户角色。
6. **保留既有反馈。** `RunStatus` 封闭 enum 与“未知值前向兼容”不一致；curl 凭据展开会进入进程参数；两项已见 [已有 M2 评论](https://github.com/sgase233/huice/pull/55#issuecomment-6785489661)。`info.version` 是 draft.2，但 info 描述仍写 draft.1（约第 36 行），建议同步版本说明，避免评审记录指向错误版本。

### AUTH-01—07 和旧操作分项

| 操作 | 本版可确认的草案方向 | 仍需验证/处理 |
| --- | --- | --- |
| 注册、登录 | restricted 先过 demo 门禁；二者要求 Origin；注册成功不自动登录 | 旧会话、角色条件及注册错误见上；实际 cookie/Origin 需浏览器联调 |
| 登出、恢复、改密 | 登出无有效会话可 204 且豁免门禁；恢复只接受会话；改密撤销全部会话 | 登出多错误组合、恢复 403 显式响应、会话撤销及缓存头需复核 |
| 企业列表 | 企业本人、审核员全部、demo 无主；游标绑定身份 | 确认多凭据优先级、对象范围及游标错误；无效身份不能降级 |
| 运行列表 | 审核员全局，demo 无主，报告由 `reportAvailable` 约束 | 筛选冲突、封闭枚举及未知值规则待修订；不能提前接入 |
| health；政策列表/详情 | health 豁免门禁；政策读取在 restricted 需门禁，身份为会话或 demo | 公开读取口径、Bearer 403 与无效凭证优先级需最终示例 |
| 审核导入、抽取、保存、发布 | reviewer 会话写；Bearer 不再写；保留原 ETag/幂等语义，叠加 CSRF/Origin | 逐个联测 401/403、版本冲突、不确定响应及输入保护 |
| 审核列表、详情 | reviewer 会话或只读运维 Bearer；企业/demo 禁止 | 多凭据并存、过期会话优先级和 Bearer 对象范围需服务端证据 |
| 企业创建、详情、事实 PATCH | 创建限未绑定本人或无主 demo；读取 reviewer 全局；写本人或 demo 无主 | 创建限额、所有权、越权 404、CSRF 和原 If-Match 恢复待联测 |
| 分析创建、详情、报告 | 创建限制本人/无主；读取按本人、reviewer 全局或无主；未完成不提供报告 | 旧响应字段、历史 run 和状态扩展、角色与对象隔离需同版服务联测 |
| 轨迹 | 企业本人与 demo 使用受限投影，reviewer/运维 Bearer 全量 | 发起人和历史无发起人规则、投影字段及对象授权仍需明确并联测 |

**本轮实际检查**：使用 YAML 解析当前草案和基线，程序对比基线 16 个方法/路径与 `x-e1-impact`：16 行、16 唯一、无缺漏或额外；每行均含 gate、identity、roles、ownership、csrf、newFailures 六个键。另逐读七个新增操作、以上矩阵及相关 schemas。键存在不证明值正确或完整；本轮未跑 OpenAPI 全量校验、所有示例校验、类型生成或真实服务测试。旧操作仍需逐个与最终正式 schema、错误响应和服务器行为对照，以上不足以签收。


## draft.4 合并版补充复核：轨迹授权边界

复核日期 2026-11-11；工作分支 HEAD `58c07bde9994cc8de125f348a5e1ebe6b0654cdd`，草案 `1.1.0-rc.1-draft.4`，正式契约 rc.9。此前 draft.1/2 结论为历史，不能直接作为本版签收。

- **需共同确认：本人轨迹的发起人约束。** `docs/EXTENSION_INTERFACES.md` §3.1、§3.2 要求企业用户仅访问本人企业中自己发起的 run；历史无发起人 run 不开放企业轨迹。draft.4 `x-e1-impact` 的 API-14 行仅写本人 run 和脱敏投影，未明确发起人、无发起人历史 run 的判定及拒绝响应。不能仅凭“本人”推定包含全部条件。请 M1/M2 核对是否沿用原约束，并在实现规格和验收样本中明确；字段级脱敏范围也仍待定义。确认之前不开放企业本人轨迹入口。
- **需澄清：demo 轨迹投影。** API-14 的 identity 和 ownership 包含 demo 无主 run，但 roles 只列企业、审核员和 Bearer；草案未定义 demo 返回哪些字段。需确认 demo 是否允许读取、使用何种投影以及旧 demo cookie 的可读范围；不据此自行扩大入口。
- 原反馈中的注册/登录旧会话、注册组合表、登出顺序、恢复 403、审核员筛选和封闭枚举已在 draft.4 文本中给出方向；具体一致性和可执行示例仍须逐项复核，不能把本次局部检查等同 23 个操作全部通过。

本次运行 `python contracts/check_e1_draft.py` 通过（16 旧操作、7 新操作、114 引用、3 请求及 8 响应示例）；`python contracts/generate_types.py --check` 通过，验证的是现行正式契约类型，不是草案生成类型。未执行真实服务、权限测试或三方签收；本记录未发送。


## draft.4 七个新增操作的消费者复核

本次以本地 `58c07bd` 的草案和 `src/api/client.ts` 静态检查；表中“方向明确”只表示文档可读，不代表通过签收或服务端已实现。

| 操作 | 消费者方向及待解决项 |
| --- | --- |
| AUTH-01 注册 | 企业/审核员密钥和企业组合、回滚、旧会话保持、Origin 已列；条件必填在文字而非 schema，界面必须按角色校验，服务端仍须独立验证。限流、密钥及真实事务待联调。 |
| AUTH-02 登录 | 200 的 user、expiresAt、csrfToken 与 Cookie 明确；旧会话替换、无效旧值忽略、登录失败 401 与门禁 403 需真实浏览器验证。账号切换的旧请求隔离仍须前端实现。 |
| AUTH-03 登出 | 无有效会话 204；有效会话校验 Origin 再校验 CSRF，403 不撤销或清 Cookie。现有 request 对成功响应要求 JSON，未来调用 204 时必须增加空响应处理。 |
| AUTH-04 恢复 | 200 恢复 SessionResponse；401 与受限模式门禁 403 的恢复路径不同。现有 UI 没有会话恢复，需清除旧账号数据并防止迟到响应覆盖。 |
| AUTH-05 改密 | 请求字段及成功 204、全部会话撤销已列；**旧密码错误的 HTTP 状态、错误码、示例及失败时会话是否保持未明确**。响应虽列 401/403/422，但复用通用组件无法判定是哪一种；需 M1 明确，避免错误地将其当会话过期或改密成功。204 也需空体处理。 |
| AUTH-06 企业列表 | summary 四项、ownership 及游标明确；越权游标仅写 400/422，尚未规定确定状态码、错误码和重置/重试策略。列表无 facts，详情应单独获取；角色过滤由服务端证明。 |
| AUTH-07 运行列表 | 四态、reportAvailable 和审核员筛选范围已写；前端必须同时依据状态及 reportAvailable，不自动重跑。跨身份或筛选变化后的游标错误也需明确，并验证旧成功 run 与空列表。 |

**跨操作迁移问题**：当前客户端使用 `credentials: include`，但 `request` 对所有成功结果要求 JSON，且写操作没有 CSRF 头；不能直接用于 AUTH-03/05。现有本机开发前端与 API 分别位于 5173/8000，属于跨源；现行 `services/api/app/main.py` 的 CORS 允许头不含 `X-CSRF-Token`，因此跨源会话写请求的预检需随 E1 后端配置调整。草案要求 Origin 等于 `PUBLIC_ORIGIN`；应确认它取浏览器前端源及部署反向代理策略，同源生产和跨源开发分别实测；不据现行后端推定草案实现已具备。

旧 16 操作的影响矩阵已复看：健康检查、政策两读、四个审核写、两个审核读、企业创建/读取/事实修改、运行创建/读取/报告/轨迹分别有身份及迁移方向；其中轨迹缺口见上节。当前客户端对 `/admin/` 和 `/trace` 自动带 Bearer，E1 审核写需会话与 CSRF，因此必须同窗替换旧入口；现有 ETag、幂等键、历史版本和错误恢复仍须保留。此为矩阵和客户端的静态核对，**并非逐字段/所有错误示例或真实服务的完整验证**。

远程 Issue #59 本次只读核对仍开放、无评论；未发现签收或后端就绪证据。未发送评审；上述新问题需要 M1/M2 共同明确。


## draft.4 补充示例校验（2026-11-11）

基线为 `58c07bd` 工作区中的 `contracts/openapi.e1-draft.yaml` 和正式 `openapi.yaml`。检查 `contracts/check_e1_draft.py` 后确认：它遍历操作中直接内联的 media examples，不展开 `$ref` 到 components responses，且构造 `Draft202012Validator` 时没有传 `FormatChecker`；因此上一次检查器通过不证明引用的错误示例或 date-time 格式已校验。

本次以 Python 的 PyYAML、jsonschema `Draft202012Validator`、`FormatChecker` 和指向本地两份文件的 RefResolver，分别遍历：

- 七个操作 requestBody 和直接内联 responses 的 `content.*.examples.*.value`：**11 个，0 个错误**。
- 草案 `components.responses` 中直接定义的 `content.*.examples.*.value`：**13 个，0 个错误**。

两组都按所属 media 的 schema 校验并解析本地引用；开启格式检查。组件示例按定义校验一次，而非声称每个引用该响应的端点各有独立测试。该检查不验证示例的业务语义、所有未附示例的响应、完整 OpenAPI 文档结构或实际服务行为。原检查器由 M1 维护，本轮未修改；完整 OpenAPI 验证器在当前 Python 环境不可用。

远程只读检查：Issue #59 仍开放且无评论；当前开放 PR 列表仅有 #57（冻结文档）。这不证明其他分支没有尚未提交的工作。未发送评审、提交或推送。


## draft.4 运行创建与生命周期衔接（2026-11-11）

本次对照 `58c07bd` 的正式 rc.9、E0 提案 D5.3、draft.4 AUTH-07 和 API-11/12 影响行，以及当前 `RealApp.tsx` / `client.ts`。这是静态消费者分析，非实测或正式签收。

- rc.9 API-11 明确为同步调用，成功 `200 AssessmentRun`；当前 `api.createRun` 和 `analyze`/`continueRun` 均等待完整结果后记录 runId、显示结果。
- E0 D5.3 写分析开始前独立事务插入 `running`；draft.4 AUTH-07 和 `RunStatus` 则写 E1 新运行从 `queued` 开始、受理即落行。两处对于**首个持久化状态**不同，应由 M1/M2 确定适用决策及迁移范围；状态存在本身不证明 API-11 已改为异步。
- draft.4 API-11 影响行未列创建响应格式或超时后如何定位运行。请明确：API-11 是否仍同步返回完整 `200 AssessmentRun`；若引入提前返回，具体状态码、响应 schema、runId 和状态查询方式是什么；无论同步或异步，连接超时/断开时已受理运行能否安全定位，何时可重试，以及补问后的 `supersedesRunId` 重复提交如何处理。不要凭空假设创建接口有幂等键或允许自动重跑。
- API-12 对 failed/未完成返回 409，而列表返回生命周期：请以同一合成运行给出 queued/running/succeeded/failed 的列表与详情示例、状态变更和错误处理；验证 409 不使前端错误展示报告或将失败归零。刷新/换账号应在服务端授权下重新获取，不靠旧页面内存。

可选处理方向：保留同步 API-11 并明确超时定位与用户手动恢复；或签收异步受理契约后实现状态查询及恢复。前者保留现有客户端成功路径但仍需解决响应丢失，后者改变响应和页面流程；本次不替成员决定。`interrupted` 单独错误码是 E3b 后续议题，不把它作为所有 E1 的共同阻塞。

Issue #59 本次只读检查仍开放、无评论；尚未向他人发送本补充评审。未运行服务或测试，不能据此判断实际事务、重试和权限行为。


## OpenAPI 文档结构校验（2026-11-11）

使用 `apps/web/.venv` 的 Python 3.12，在仓库外 `../huice-phase6-evidence/openapi-validator` 隔离安装 `openapi-spec-validator==0.9.0`（未修改项目依赖），通过临时 `PYTHONPATH` 分别运行 `python -m openapi_spec_validator contracts/openapi.e1-draft.yaml` 与 `contracts/openapi.yaml`：两者均输出 `OK`、退出码 0。首次离线安装因隔离缓存无该包失败；随后从包仓库安装成功。

该检查验证 OpenAPI 文档结构和引用，不能证明草案中自定义 `x-e1-*` 语义、所有权限/错误组合、服务实现或 M1/M2 签收。与此前启用格式检查的示例校验互补；前面写“当前环境无完整验证器”的记录是安装前的历史状态。


## draft.4 隔离类型生成与编译（2026-11-11）

将 `58c07bd` 的正式 rc.9 69 个 schema 与 draft.4 13 个 schema 在内存中合并（名称冲突 0），使用现有 `contracts/generate_types.py` 的 `render_types` 生成到仓库外 `../huice-phase6-evidence/20261111-e1-draft4-types/types.ts`。以本地 TypeScript 执行 `tsc --noEmit --strict --skipLibCheck --target ES2020` 编译生成文件和同目录 `check.ts`，通过：示例覆盖可空企业 ID、注册字段、运行摘要；`@ts-expect-error` 验证 `interrupted` 不属于封闭 `RunStatus`、`SessionResponse` 缺 CSRF 不可赋值。随后 `python contracts/generate_types.py --check` 通过，正式 `apps/web/src/api/types.ts` 未覆盖。

这只证明当前生成器对所测组合能产生可编译类型；字符串长度、日期格式、注册的条件必填、凭证门禁和对象授权不能由这些 TypeScript 类型保证。此合并仅是隔离技术检查，不是正式契约合并、消费者签收或实现。
