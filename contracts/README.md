# 接口契约

`openapi.yaml` 是 HTTP 字段的单一真源，当前版本 `1.1.0-rc.1`（23 个操作 = API-01—API-16 + AUTH-01—AUTH-07，请求/响应 schema 与合成示例）。人工可读说明见[功能实现逻辑与接口协作约定](../docs/IMPLEMENTATION_AND_INTERFACE.md)，补充了版本、事实期间、清单与后台读取约束。

M1 维护 OpenAPI，M2 审查前端可调用性，M3 审查业务含义。破坏性变更先更新契约与说明，由 M1/M2 确认并附迁移说明。

**`security` 与 `x-e1-gate` 是两个维度**（rc.10 起）：标准 `security` 只表达「要哪些凭证」（列表内 OR、对象内 AND）；部署模式相关的外层门禁逐操作写在 `x-e1-gate`（`local: none`；`restricted: DemoSession` 或 `exempt`），根级 `x-e1-gate-model` 给出判定顺序、豁免清单与两种模式的说明。restricted 下只有 `GET /health` 与 `POST /auth/logout` 豁免，连 `security: []` 的注册/登录也要先过门禁（403 `DEMO_SESSION_REQUIRED`）。门禁在**解析请求体之前**执行（服务端由路由类 `GatedRoute` 保证）：缺 cookie 时请求体缺字段、类型不对、为空或不是 JSON 也一律 403，不会先回 422——前端据此区分「门禁失效（回演示入口）」与「表单错误（422）」「登录失效（401）」。运行时 `/openapi.json` 发布同一份口径，`tests/test_openapi_alignment.py` 逐操作比对两份文档，`tests/test_e1_gate_modes.py` 按文档驱动 restricted 模式的实际请求。

## #46—#48 扩展设计入口（2026-11-09）

[扩展接口设计](../docs/EXTENSION_INTERFACES.md) 定义账号/会话/对象归属、发现配置/运行/候选审核、完整度/轨迹/趋势的建议请求与响应，以及迁移、幂等、权限和错误边界。其中账号/会话/对象权限（E1）的 7 个端点已实现并并入 `openapi.yaml`（`1.1.0-rc.1`）；其余部分仍待评审、未实现，契约不提前声明不存在的客户端能力。开发顺序和未决项见[开发计划](../docs/DEVELOPMENT_PLAN.md)。

M1/M2 按实际兼容性选择下一契约版本，不预定 `1.1.0`；每个实现 PR 将已签收部分同步进 OpenAPI、后端 schema、M2 生成类型/客户端及成功失败示例。鉴权改造必须附旧门禁/脚本和新账号数据隔离迁移方案。迁移当前到 `008`，实施前核查最新序号再追加；不修改已部署 SQL。

## E1 契约草案（2026-11-10）

[`openapi.e1-draft.yaml`](openapi.e1-draft.yaml) 是 #46 账号/会话/对象权限的**设计记录**（`1.1.0-rc.1-draft.7`）。自 `openapi.yaml` 的 `1.1.0-rc.1` 起，7 个操作与账号/会话 schema 已并入正式契约，端点、schema 与 `security` 以 `openapi.yaml` 为准；本文件保留评审时用到的门禁/身份真值表、错误码清单与可复核的请求样例。它不进 `generate_types.py` 的管线、不影响 `APP_VERSION` 与评测指纹。

- 内容：AUTH-01—07 七个操作、16 个现有操作的 security 与 404/409 语义变更（`x-e1-impact`）、错误码表（`x-error-codes`）、旧消费者清单（`x-legacy-consumers`）、未决项（`x-open-questions`）。
- 写法：复用的现有 schema 一律 `$ref: './openapi.yaml#/components/...'`，草案不复制它们；两份文件在同一个目录，工具可直接解析。
- 自检（可复跑，只读两份契约）：`python contracts/check_e1_draft.py`。检查 `$ref` 可解析、7 个操作都有 `x-e1-gate`、`x-e1-impact` 16 行与基线端点一一对应（比较基线时排除已并入的 7 个 E1 操作）、错误码唯一且被引用、样例按 schema 通过。`services/api/tests/test_e1_draft.py` 再把 16 行的门禁与 FastAPI 路由的 `GatedRoute`／依赖对一遍，并逐操作比对正式契约、草案与服务端三处的 operationId、`security` 与 `x-e1-gate`。
- 流程：M2 逐端点评审 → 版本号与切换安排共同签收 → 并入 `openapi.yaml`（已完成，`1.1.0-rc.1`）→ M2 重新生成 `types.ts`。

## 已实现范围（后端：23/23；前 16 项 2026-10-27，AUTH 7 项 2026-11-11 并入契约）

| 接口 | 方法路径 | 状态 |
| --- | --- | --- |
| API-01 `getHealth` | `GET /health` | 已实现；数据库不可用返回 503 |
| API-02 `listPolicies` | `GET /policies` | 已实现；只列已发布版本，`closed` 读取时派生 |
| API-03 `getPolicy` | `GET /policies/{policyId}` | 已实现；未发布/校验未过返回 422 |
| API-04 `importPolicy` | `POST /admin/policies/import` | 已实现；201 `PolicyDraft`（`pending_parse`）+ ETag |
| API-05 `extractPolicy` | `POST /admin/policies/{policyId}/extract` | 已实现；按版本 + `If-Match` + `Idempotency-Key` 防重 |
| API-06 `saveCriteria` | `PUT /admin/policies/{policyId}/criteria` | 已实现；返回校验发现，失败条件置 `failed`；policy 级发现以 `criterionId: null` 返回（`code` 是自由字符串，如 `POLICY_UNSATISFIABLE`），无需改契约 |
| API-07 `publishPolicy` | `POST /admin/policies/{policyId}/publish` | 已实现；发布时**重算**引用校验（含政策级发现），未过 → 422 `CRITERIA_NOT_VALIDATED` |
| API-08 `createEnterprise` | `POST /enterprises` | 已实现；返回 `factVersion` 作为 ETag |
| API-09 `getEnterprise` | `GET /enterprises/{enterpriseId}` | 已实现 |
| API-10 `updateFact` | `PATCH /enterprises/{enterpriseId}/facts/{factKey}` | 已实现；`factVersion` CAS，冲突 409/428 |
| API-11 `createRun` | `POST /assessment-runs` | 已实现；四态求值 + 补问 + 清单 + 解释 + 轨迹，缓存可绕过 |
| API-12 `getRun` | `GET /assessment-runs/{runId}` | 已实现；返回 run 快照 + 读取时有效性视图 |
| API-13 `downloadReport` | `GET /assessment-runs/{runId}/report.md` | 已实现；Markdown 附件，附「截至下载时刻」的有效性 |
| API-14 `getTrace` | `GET /assessment-runs/{runId}/trace` | 已实现；管理 Bearer、脱敏、阶段顺序重排；路径无 /admin 不代表公开可读 |
| API-15 `listAdminPolicies` | `GET /admin/policies` | 已实现；`{items,nextCursor}`，无 ETag |
| API-16 `getAdminPolicy` | `GET /admin/policies/{policyId}` | 已实现；200 + ETag，缺省取最新导入版本 |
| AUTH-01 `registerUser` | `POST /auth/register` | 已实现；201，事务内建账号与（企业用户）唯一企业 |
| AUTH-02 `loginUser` | `POST /auth/login` | 已实现；下发 `huice_session`，返回 `csrfToken` |
| AUTH-03 `logoutUser` | `POST /auth/logout` | 已实现；204，豁免演示门禁，无 401 分支 |
| AUTH-04 `getCurrentUser` | `GET /auth/me` | 已实现；会话恢复，`Cache-Control: no-store` |
| AUTH-05 `changePassword` | `POST /auth/password` | 已实现；204，改密后撤销该账号全部会话 |
| AUTH-06 `listEnterprises` | `GET /enterprises` | 已实现；按身份与归属过滤，游标绑定可见范围 |
| AUTH-07 `listRuns` | `GET /assessment-runs` | 已实现；按身份与归属过滤，`enterpriseId` 只筛不放宽 |

后端实现与契约的 operationId、tags、security 由 `services/api/tests/test_openapi_alignment.py` 逐项比对（23 个操作全覆盖）。契约定版仍需 M2 复核。

### 本轮契约变更（待 M2 复核）

1. `info.version`：`0.2.0-draft` → `1.0.0-rc.1` → `1.0.0-rc.2`；描述改为说明已实现范围与“导入后为 `pending_parse`”。
2. API-04 的 201 示例：`status` 由 `pending_review` 改为 `pending_parse`，`criteria` 改为 `[]`。理由：导入只做文件落库，条款抽取属 API-05；示例若显示 `pending_review`，前端会按“已完成抽取”开发并与流程冲突。**只改示例值，schema 未变**，`types.ts` 无差异。
3. `HealthResponse` 示例的 `version` 同步为 `1.0.0-rc.2`。
4. API-04 的 `files` 补充说明：第一个文件是正文（`kind=body`），其余为附件；`files` 仍为可选，不传时 `sources: []`。
5. rc.2（2026-10-23，队长决定改用 DeepSeek）：`ModelUsage.mode` 与 `PolicyAssessment.explanationMode` 的枚举值 `maas` 改为 `deepseek`，其余取值不变。**这是一处破坏性变更**，`apps/web/src/api/types.ts` 已重新生成（仅这两行），请 M2 复核；若前端已按 `maas` 写过字面量，需同步改为 `deepseek`。
6. rc.3（2026-10-28，按 M3 标注侧需求）：`FactKey` 由 10 键扩到 16 键——新增 `revenue`、`equityFinancing`、`rdExpenseIncrease`、`rdExpenseRevenueRatio`、`rdAddDeductionClaimed`、`creditRecord`，地方通知的研发投入补助/专精特新条款要用的六类事实；`FactUnit` 补 `ratio`（非负十进制小数，3% 存 `0.03`）。**只放宽枚举，不改任何既有字段与端点**，`apps/web/src/api/types.ts` 已重新生成（`FactKey` 与 `FactUnit` 两处联合类型），请 M2 复核。增量与占比按「预计算事实」处理：由标注方给出算好的数值，引擎只做 `compare`，不新增算子。配套迁移 `infra/sql/005_fact_dictionary_extension.sql` 重建 `enterprise_fact` 的三个约束。
7. rc.3 内追加（2026-10-29，仅描述）：API-02 的 `region` 参数补语义说明——按区划码筛同时包含全国性政策（全国性政策记 `region=CN`），传 `CN` 只返回全国性政策。**只改描述文字，不改 schema**，`apps/web/src/api/types.ts` 不变（`generate_types.py --check` 通过）。同步落在 `services/api/app/integrations/storage.py` 的 `NATIONAL_REGION_CODE` 与同一条过滤口径。
8. rc.4（2026-10-29，按 #9 的三键讨论结论）：`FactKey` 由 16 键扩到 18 键——新增 `specificMarketSince`（`date`，企业**开始从事该细分市场之日**）与 `independentLegalEntity`（`boolean`，`true` = 具有独立法人资格），专精特新的细分市场年限与法人资格条款要用。**只放宽枚举，`FactUnit` 与任何既有字段、端点都不动**；**`apps/web/src/api/types.ts` 未由 M1 改动**（前端目录归 M2），需 M2 执行 `python contracts/generate_types.py` 重新生成（差异只有 `FactKey` 一行）；在那之前 `generate_types.py --check` 会报 `Frontend types differ`，`npm run build` 也会因为新键没有映射而失败。两个设计取舍：①年限做成**日期**而不是数值——年数是相对量，同一份企业快照换个年份重算就得出另一结论、不可复现，`date` 算子按绝对日期判断；②法人资格键名从提案的 `legalPersonStatus` 改为 `independentLegalEntity`，原名看不出 true 指「合法」还是「具备法人资格」。另有行为变更：`date` 算子由「只认 `establishedDate`」改为认 `rules.DATE_KEYS`（白名单）。配套迁移 `infra/sql/006_fact_dictionary_extension_rc4.sql`。被否决的提案：`restrictedIndustry`（布尔）——标注规范已规定「不属于限制禁止行业」用 `industry_exclusion` 表达，加布尔键会与自身口径冲突，未解的问题是码表粒度而不是表达力。
9. rc.5（2026-10-31，应答 M2 在 PR #9 的 P1）：`Expression` 新增 `manual_review`（`{"op":"manual_review","reason":"…"}`，reason 1–200 字），求值固定返回 `insufficient_evidence` 并回显 reason，**不会被当成满足**；引文仍须逐字可查；发布闸门照常放行（`validationStatus` 说的是结构与引文没问题，与「判定为真」不是一回事）。用途是把「这条按政策口径本就不由程序判」写进数据模型——此前标注方只能在「硬写一个大概率错的表达式」和「退回待核实、整版政策发不出去」之间二选一。抽取智能体不得生成该算子。同时修正 rc.4 的 `DateExpression.factKey`：schema 当时仍写死 `establishedDate`，与描述和服务端 `rules.DATE_KEYS` 不一致，照 schema 写 `specificMarketSince` 的日期条件会被拒；rc.5 改为 `enum` 两个日期键，并在 `test_openapi_alignment.py` 加了算子集合与日期键集合的对齐断言。**`apps/web/src/api/types.ts` 仍归 M2 生成**，需按 rc.5 重新生成一次（含新增 schema 与版本号）。
10. rc.6（2026-11-03，应答 M2 issue #27）：`TraceStep.evidenceRefs` 改名 `objectRefs`，元素换成新 schema `TraceObjectRef={type,id}`，与 `CriterionResult.evidenceRefs` 的政策原文引文分开。服务端同字段由 `list[dict]` 收紧为 `list[TraceObjectRef]`；`trace_step.evidence_refs` 列名不变，无新迁移。**`apps/web/src/api/types.ts` 需按 rc.6 重新生成**（由 M2 执行）。
11. rc.7（2026-11-05，应答 M2 对 PR #34 的 P1）：`ModelUsage` 新增**可选** `tokensUnknown`（布尔，缺省 false）。`true` 表示上游没有返回**完整可用**的用量（2xx 但缺 `usage`、只给一半、格式异常），此时 `inputTokens`/`outputTokens` **不得计入费用或合计**，也不能与真实零用量混同；`fixture`、规则抽取这类**没有发起调用**的路径仍是真实的 0，不带该标记。数值本身不保证是 0：rc.7 自己产生的未知一律是 `0/0`，而重放 rc.6 时期写下的历史记录时，rc.6 对两个计数是**各自**兜底的，判不出缺的是哪一个，数值原样保留（可能是 `120/0` 这样的一半，见下）。服务端另有迁移 `infra/sql/007_idempotency_usage_semantics.sql`（`idempotency_record` 加 `usage_semantics` 列），纯服务端内部、不影响线路。
    **不是纯加法：这是带行为语义的契约迁移，服务端在已知用量时省略该字段。** `ModelUsage` 是 `additionalProperties: false`，若常路径也回 `tokensUnknown: false`，按 rc.6 严格校验的旧消费者会把**正常响应**判为非法。省略后：已知用量的响应与 rc.6 逐字段一致（旧消费者照常通过）；未知用量时多出 `tokensUnknown: true`，旧消费者会**失败**——这是有意的，比让它把占位 0 当真实用量好。忽略未知属性的旧消费者在未知场景仍只能看到 0，这类调用方本来就无法区分，需按本文件与 `infra/README.md` §4.3 的口径自行标注。
    迁移约定：**不是「后端先上」**。现有前端不是严格消费者——`apps/web/src/api/client.ts` 允许额外字段、审核页直接显示 `usage.inputTokens/outputTokens`，收到 `tokensUnknown: true` + 占位 0 会显示「输入 0 / 输出 0 tokens」。后端先上等于把「未知」**安静地**变成「零」，比解析失败更糟。因此顺序是：

    1. **前端先行**：M2 按 rc.7 重新生成 `types.ts`，在展示与统计处先判 `tokensUnknown`：`true` 显示「未知」并排除出合计。**「响应里没有该字段」不等于「已知」**——它只说明服务器没给这个状态。后端仍是 rc.6 时，rc.6 在上游没回 `usage` 时对两个计数**各自**用 0 兜底（`integrations/llm.py` 的 `usage.get(...) or 0`），这个 0 与真实零用量分不开，部分缺失（`120/0`）也看不出来。所以在「后端还是 rc.6」的窗口里，前端应把该区块标成「旧版未提供用量状态」（或暂停这部分合计），不能当成已确认的 0。
    2. **后端随后**上 rc.7，此时「未知」才第一次出现在线路上，而接收方已经能表达它。
    3. **严格校验旧响应形状的第三方消费者单独安排**：已知路径逐字段一致、不受影响；未知路径会解析失败（fail-closed），要么先升级，要么接受这类调用在这条路径上不可用。

    回滚分两种，结论不同：

    - **只回退 rc.7 的「未知」表达、保留 #34 的其余部分**：`model_call ... usage=unknown` 日志与轨迹摘要「另有 K 次调用用量未知」仍在——它们来自 #34，不是 rc.6 自带的。
    - **整体回退到 rc.6（main）**：`model_call` 日志、`extraction` 轨迹步骤、用量三态**一起消失**，回滚后未知调用没有任何记录可查；此前写下的行留在只追加表里，但不会再有新增。别按「回滚后还能查日志」验收。

    已经落库的 rc.7 响应：`idempotency_record.response` 存的是抽取响应**整体**（路由里 `result.model_dump(by_alias=True, mode="json")`），未知用量时里面就有 `usage.tokensUnknown: true`；该表带 `reject_mutation()` 触发器，删不掉也改不了。回退到 rc.6 的 `ModelUsage`（`extra="forbid"`）后，用**同一个 Idempotency-Key、草稿 revision 未变**地重放**未知用量**的那些记录，会在 `ExtractionResult.model_validate` 处失败（500）；用量已知的记录响应形状与 rc.6 逐字段一致，照常可重放（新增的 `usage_semantics` 列旧代码不读，不影响）。影响面限于「同键 + 同版本」的幂等重放，换新键不受影响；不能接受就在回滚时保留能读该字段的解析（只回退「未知」的输出路径），或明确接受这些键不可重放。因此**「该字段不落库」不成立**：它没有做成业务表字段（迁移 `007` 的 `usage_semantics` 是**记录来源**，与用量本身无关），但它落在 `idempotency_record.response` 与轨迹摘要文本里。

    前端在回滚期间：**不能**把「没有该字段」当已知（理由同上，rc.6 的 0 兜底与真实 0 分不开）；显示「旧版未提供用量状态」并暂停这部分合计，或干脆不展示 tokens。前端这套逻辑保证的是**格式兼容**，不是**语义正确**——rc.6 窗口里的用量状态，前端无法区分。

    **历史记录的两个重放方向**（幂等重放原样返回存储的响应，所以「当前服务版本」证明不了这条响应的用量语义）：

    - **记录来源写在库里（迁移 `007`）**：`idempotency_record` 的 `usage_semantics` 列标明这条记录出自哪一代服务端——`'explicit'` = rc.7 起写入，未知一定带标记、缺省即已知；`'legacy'` = 本迁移之前写入，也可能是回退到 rc.6 期间写入的（旧代码不写这一列，`DEFAULT` 就是 `'legacy'`，语义正好）。响应本体不动，回退到 rc.6 后已知用量的记录仍能原样重放。
    - **rc.6 写下的记录 + rc.7 服务端重放**：rc.6 对 `prompt_tokens`、`completion_tokens` 是**各自** `or 0` 兜底的——`120/0` 只说明 prompt 给了、completion 没给，`0/45` 反之，`0/0` 两个都缺，连 `120/45` 也不能凭「非零」认定完整。所以判据不是数值而是来源：记录是 `legacy` 且 `mode=deepseek`，一律按未确认处理，补 `tokensUnknown: true`，**数值原样保留**（可能是 `120/0` 这样的一半）供人核对；`mode=fixture`/`saved` 没有发起调用，0 是真实的，不补标。只改本次响应视图，**不写回记录**（该表只追加）。因此 rc.7 服务端不会把来源不可确认的历史用量当已知回给线路。
    - **rc.7 写下的记录 + rc.6 服务端重放**：未知用量的记录 `ModelUsage` 是 `extra="forbid"`，解析失败 → 500（见上）；已知用量的记录形状与 rc.6 一致，照常重放。服务端侧无法补救，只能按上面的回滚约定处理。
    - **消费者规则（不依赖 `/health`，也不按数值猜）**：`tokensUnknown: true` → 未知，两个数值都不得计入费用或合计；`mode=fixture`/`saved` → 没有发起调用，是真实的 0；其余「无标记」只在**这条响应出自 rc.7 服务端**时才算已知（rc.7 会给来源不可确认的历史记录补标）。rc.6 窗口内的响应没有标记也不可信——rc.6 的 0 兜底与真实 0 分不开。「`mode=deepseek` 且双 0 → 未知」这条老判断只覆盖「两个都缺」，漏得掉部分缺失（`120/0`、`0/45`），只能算兜底的一层，不能当主判据。
    - **不要用「换个 Idempotency-Key 重来」当默认处置**：换键会重新发起抽取与模型调用，可能重复计费，且新结果会覆盖同一草稿的条款。先查 `idempotency_record`（是否已有记录）、轨迹与 `model_call` 日志确认这次调用是否已经发生、结果是否已保存，再决定是重放、修复还是重新调用。

    `apps/web/src/api/types.ts` 需由 M2 重新生成（差异为 `ModelUsage` 多一行 `tokensUnknown?: boolean;`），在那之前 `python contracts/generate_types.py --check` 会报 `Frontend types differ`。

12. rc.8（2026-11-06，应答 M2 对 PR #37 的 P1/P2）：`PreparationItem` 新增**必填** `materialId` 与 `status`，`missing` 保留。此前 `missing` 是唯一的判据，页面只能把它映射成「资料缺失」——而没有事实键的材料（项目申请书、信用报告、社保凭证）本来就无法自动判断是否已提供，政策到期或申报窗口已关闭时材料也不再提交，两种情况都会被写成「缺材料」，是在断言一件没查过的事。
    - `status` 四种取值：`provided`（这份材料要证明的事实都已有取值，**不等于已核验**）、`missing`（有事实键、至少一个还没取值）、`manual_check`（没有事实键，程序判断不了，需人工核对）、`policy_closed`（政策已到期或窗口已关）。
    - `missing` **语义不变**、仍是保守口径：`manual_check` 也算 `true`。**展示请以 `status` 为准**，`missing` 只保留给暂未升级的消费者。前端在升级前仍会把这四类都显示成「资料缺失 / 资料已提供」，这是已知的过度断言，不是这次后端能替它修好的。
    - `title` 已带「【必交】/【条件交】」前缀（`required`/`condition_desc` 的承载方式按 MATERIAL_REQUIREMENTS §4 的方案 ②，不加字段），条件交材料在 `description` 末尾附「（何时需要：…）」。
    - `requiredFactRefs` 是**运行期展开**的结果：季度类键（资产负债、从业人数）展开成四个季度的期初与期末共八个引用，随判断期间变化的键按本次期间给出具体起止日期；契约不接受 `"assessment"` 字符串。
    - 哪些材料会出现在清单里：必交无条件列出；条件交只在本次判断真的引用到它证明的事实键时列出。**没有事实键的条件交（GRANT-02/05/06：申请书、社保凭证、信用报告）一律列出并标 `manual_check`**——条件只有文字、程序判断不了，省略会让人以为这项不用交。
    - 材料表的 `applies_to_policy_ids` 是业务代号，导入时借 `source_register.csv` 的文号落到库里的政策版本；一个代号可以登记新旧多份文号（`CN-TAX-GXJS` 就有 2008 与 2016 两版），只有材料真正引用该代号、且这些文号在库里指向不同政策时才算歧义并整体失败。
    - 数据来源是 `python -m app.materials import` 导入的 `data/policies/material_requirements.csv`（只导入 `review_status=已仲裁` 的行），配套迁移 `infra/sql/008_material_requirements.sql`。**服务启动前云 RDS 必须已执行 001—008**；未导入时 `items` 为空、`warnings` 里保留「申报材料清单待 M3 审核材料导入后补齐」，报告正文同样回落到那句占位文案。
    - 历史 run 不受影响：此前 `preparation.items` 恒为 `[]`，已落库的快照里没有条目，新增必填字段不会让旧快照解析失败。
    - `apps/web/src/api/types.ts` 需由 M2 按 rc.8 重新生成（差异为 `PreparationItem` 多两行、`MaterialStatus` 联合类型一处），在那之前 `python contracts/generate_types.py --check` 与 `npm run build` 都会红。

13. rc.9（2026-11-06，应答 M2 对 PR #40 的 P1）：`PreparationItem.status` 的两个取值改名，`provided` → `facts_filled`、`missing` → `facts_missing`；`manual_check`、`policy_closed`、`missing` 布尔字段与其它字段一律不动。
    - 为什么改：`provided` 会被读成「材料已提交」，但服务端只读企业事实、从不读材料，它实际只说「这份材料要证明的事实都有取值」。名字比能证明的多说了一层，页面照着写就是过度断言；`missing` 同理，它说的是**事实**缺项。
    - 四态现在的读法：`facts_filled`（要证明的事实都有取值，**不代表材料已交或已核验**）、`facts_missing`（有事实键、至少一个还没取值）、`manual_check`（没有事实键，程序判断不了）、`policy_closed`（政策已到期或窗口已关）。
    - `missing` 布尔仍是保守口径（`manual_check` 也算 `true`），语义不变；报告文案同步改为「所需事实都有取值，但不代表材料已交」「所需事实有缺项」，不再出现「缺材料」。
    - 破坏性：枚举只有这两个值变了，按旧值匹配的消费者会落空。`apps/web/src/api/types.ts` 需由 M2 按 rc.9 重新生成并同步页面文案（把「资料缺失／资料已提供」改成「所需事实有缺项／事实已齐（不代表已交）」口径），在那之前 `python contracts/generate_types.py --check` 会红。

14. rc.10（2026-11-11，应答 M2 对 PR #61 的 P1「发布的 OpenAPI 与真实授权不符」）：16 个操作的 `security` 改成 E1 之后的**真实**身份集合，新增 `securitySchemes.UserSession`（cookie `huice_session`）。
    - 后台写（导入、抽取、保存条件、发布）只剩 `UserSession`：E1 起运维 Bearer 只读，原来写的 `DemoSession + AdminBearer` 既许诺了 Bearer 能写，也把两个方案写进**同一个映射**（那是「与」不是「或」），与实现两处都不符。
    - 后台读（`GET /admin/policies`、`GET /admin/policies/{policyId}`）是 `UserSession` 或 `AdminBearer`；轨迹（`GET /assessment-runs/{runId}/trace`）三类身份都可读；其余业务接口是 `UserSession` 或 `DemoSession`——Bearer 在 E1 后读不到业务数据（403）。
    - 兼容性：`security` 不进生成的类型（`generate_types.py` 只读 `components.schemas`），`apps/web/src/api/types.ts` 不变，`--check` 仍一致。按旧文档「Bearer 能写」实现的调用方在 E1 已实装后本来就会收到 403，这次只是把文档改成事实。
    - **E1 的 7 个新端点当时仍未并入本契约**（只在 `openapi.e1-draft.yaml`）：并入会新增 schema，`types.ts` 必须由 M2 重新生成，属消费者切换窗口。该窗口已在 `1.1.0-rc.1` 打开。

15. `1.1.0-rc.1`（2026-11-11，应答 M2 对 PR #61 的复审）：把 E1 的 7 个操作并入正式契约。
    - 新增路径/操作：`POST /auth/register`、`POST /auth/login`、`POST /auth/logout`、`GET /auth/me`、`POST /auth/password`，以及给既有 `/enterprises`、`/assessment-runs` 增加 `GET`（AUTH-06/AUTH-07）。
    - 新增 13 个 schema：`UserRole`、`User`、`RegisterRequest`、`RegisterResponse`、`LoginRequest`、`PasswordChangeRequest`、`SessionResponse`、`Ownership`、`EnterpriseSummary`、`EnterpriseSummaryList`、`RunStatus`、`RunSummary`、`RunSummaryList`；新增参数 `CsrfToken`、`OriginHeader`、`OriginHeaderOptional`。
    - `RegisterRequest.username` 与 `User.username` 是 **3—32 位**（`minLength: 3`、`maxLength: 32`、`pattern: ^[a-z0-9_.-]{3,32}$`），服务端先去首尾空白再转小写，之后才套这条规则。规则只有一处真值：`app/auth.py` 的 `USERNAME_PATTERN`，库表（迁移 `009` 的 `app_user_username_shape` CHECK）与契约的 `pattern` 都是它；`app/schemas.py` 直接读这个常量生成 JSON Schema 并在解析阶段判。此前契约写 64 位、handler 另判 3—32，按契约构造的 33—64 位用户名能过 schema 却被 422 拒绝，现已同源（M2 在 PR #61 复审指出）。`LoginRequest.username` 相反：只做规范化后查账号，**不**套注册规则，长度或字符不合规的名字统一回 401 `INVALID_CREDENTIALS`，不借登录接口暴露名字格式或是否存在（契约字段说明已写）。**校验对象写明是请求体的原始值**（M2 在 PR #61 复审 P2）：契约要求调用方先 trim、转小写再发，`pattern`/`maxLength` 直接作用于发送值，用 schema 做请求校验的客户端与服务端因此一致；服务端解析时再做一次幂等规范化只是容错，不是契约承诺，带首尾空白或大写的原始输入能成功属实现细节，客户端不得依赖。按契约构造的合法值原样通过、原样入库。
    - 版本从 `1.0.0` 升到 `1.1.0`：新增了路径、操作与 components，`apps/web/src/api/types.ts` 必须重新生成，所以不是补丁级。`info.version` 与 `APP_VERSION` 已同一次改动一起升。**兼容性分两层，不能笼统说「向后兼容」**：①相对本 PR 内的 `rc.10` 是**纯增量**——既有 16 个操作的 schema、`security` 与门禁一字未改；②相对 `main` 上的 `rc.9` 与按 `rc.9` 写的旧调用方是**破坏性鉴权变更**——`rc.10` 已把后台写权限从运维 Bearer 改为审核员会话（Bearer 只读，拿 Bearer 写后台会 403），从 `rc.9` 升级必须同时切换调用方，不能只换契约文件。`1.1.0-rc.1` 是候选版本号，最终版本口径由 M1/M2 确认。
    - **已知中间状态**：`python contracts/generate_types.py --check` 与 `services/api/tests/test_contract.py::test_types_are_generated_from_current_contract` 在 M2 重新生成 `types.ts` 之前会红。`apps/web/**` 归 M2，M1 不直接改；生成只需要 `python contracts/generate_types.py`（差异是新增的 13 个 schema，既有类型不变）。
    - 门禁判定顺序补一层：门禁在**解析请求体之前**执行，restricted 下缺 cookie 一律 403 `DEMO_SESSION_REQUIRED`（缺字段/错类型/空体/非 JSON 都一样），local 模式同样的请求才是 422。`x-e1-gate-model` 的 `evaluation-order` 与各操作 `x-e1-gate.note` 已同步。

## 当前实现产出的错误码

响应统一为 `{"error":{"code","message","details"},"traceId"}`，并带 `X-Trace-Id`。全部 23 个操作都按此信封返回，例如：

| HTTP | code | 触发条件 |
| --- | --- | --- |
| 401 | `UNAUTHENTICATED` | 未提供任何凭证（E1 起「提供了但无效」另报 `TOKEN_INVALID` / `SESSION_INVALID`，不再混用本码，也不降级到下一种凭证；新增错误码见 `openapi.e1-draft.yaml` 的 `x-error-codes`） |
| 403 | `DEMO_SESSION_REQUIRED` | `DEMO_ACCESS_MODE=restricted` 且 `huice_demo_session` 缺失、签名不符或已过期（格式 `v2.<过期秒>.<sid>.<HMAC>`，E1 前签发的 `v1.<过期秒>.<HMAC>` 仍验签通过但只读；用独立机密 `DEMO_SESSION_SECRET` 签发，见 `infra/deploy/issue-session.py`） |
| 400 | `INVALID_QUERY` | query/path 参数不合法（含 limit 越界、非法 UUID、游标无效） |
| 400 | `INVALID_METADATA` | `metadata` 不是合法 JSON 对象 |
| 400 | `INVALID_MULTIPART` | multipart 结构损坏 |
| 404 | `POLICY_NOT_FOUND` | policyId 或 version 不存在 |
| 409 | `VERSION_CONFLICT` | 同一 `policyId` 下 `version` 已存在，事务回滚无部分写入；不带 `policyId` 的导入视为新建政策，不触发此错误 |
| 413 | `FILE_TOO_LARGE` | 单文件 >10 MiB 或请求体 >51 MiB |
| 415 | `UNSUPPORTED_MEDIA_TYPE` | 非 multipart/form-data 请求 |
| 415 | `UNSUPPORTED_FILE_TYPE` | 内容不是 PDF/UTF-8 文本（按真实内容判断） |
| 422 | `VALIDATION_ERROR` | metadata 字段/类型/枚举/日期或窗口顺序不合法（`details.fieldErrors`） |
| 422 | `OFFICIAL_URL_REJECTED` | URL 非 https、带 userinfo、非 443 端口、指向本机/内网，或不在 `gov.cn` 白名单 |
| 422 | `POLICY_NOT_FOUND` | 指定 `policyId` 不存在（无法为其新增版本） |
| 422 | `TOO_MANY_FILES` / `UNEXPECTED_FIELD` | 文件超过5个 / 出现 metadata 之外的表单字段 |
| 422 | `EMPTY_FILE` / `PDF_HAS_NO_TEXT` / `INVALID_PDF` / `ENCRYPTED_PDF` / `PDF_TOO_LONG` | 文件内容问题 |
| 500 | `INTERNAL_ERROR` | 未预期异常，脱敏输出并带 traceId |
| 503 | `DEPENDENCY_UNAVAILABLE` | 数据库或对象存储不可用 |

页面状态里没有 `closed` 的持久化值：已发布版本按有效期与申报窗口在读取时派生为 `closed`。

## 静态检查

从 `huice/` 运行：

```powershell
python contracts/generate_types.py          # 更新前端 wire types
python contracts/generate_types.py --check  # 只检查，不写文件
python -m unittest discover -s services/api/tests -p test_contract.py -v
```

`generate_types.py` 由 M1 维护，生成的 `apps/web/src/api/types.ts` 由 M2 审查，禁止手工改生成内容。生成器只处理本契约使用的 ref、union、object、array、enum 和 primitive；新增 schema 语法时同步扩展生成器及测试。TypeScript 不执行正则、期间或业务校验，后端必须单独实现。

静态工具版本锁定在 `services/api/requirements.lock`（PyYAML 6.0.3、jsonschema 4.26.0、`rfc3339-validator` 0.1.4）。`rfc3339-validator` 用于让 `format: date-time` 真正生效——缺少它时“日期时间缺时区”用例会误判为通过。静态检查与 `/openapi.json` 比对不等于后端契约验收，业务语义与数据库行为仍需集成测试。
