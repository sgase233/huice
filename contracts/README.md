# 接口契约

`openapi.yaml` 是 HTTP 字段的单一真源，当前版本 `1.0.0-rc.5`（16 个接口、请求/响应 schema 与合成示例）。人工可读说明见[功能实现逻辑与接口协作约定](../docs/IMPLEMENTATION_AND_INTERFACE.md)，补充了版本、事实期间、清单与后台读取约束。

M1 维护 OpenAPI，M2 审查前端可调用性，M3 审查业务含义。破坏性变更先更新契约与说明，由 M1/M2 确认并附迁移说明。

## 已实现范围（后端，2026-10-27：16/16）

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
| API-14 `getTrace` | `GET /admin/assessment-runs/{runId}/trace` | 已实现；管理 Bearer、脱敏、阶段顺序重排 |
| API-15 `listAdminPolicies` | `GET /admin/policies` | 已实现；`{items,nextCursor}`，无 ETag |
| API-16 `getAdminPolicy` | `GET /admin/policies/{policyId}` | 已实现；200 + ETag，缺省取最新导入版本 |

后端实现与契约的 operationId、tags、security 由 `services/api/tests/test_openapi_alignment.py` 逐项比对。契约定版 `1.0.0` 仍需 M2 复核。

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

## 当前实现产出的错误码

响应统一为 `{"error":{"code","message","details"},"traceId"}`，并带 `X-Trace-Id`。全部 16 个接口都按此信封返回，例如：

| HTTP | code | 触发条件 |
| --- | --- | --- |
| 401 | `UNAUTHENTICATED` | 缺少或错误的 Bearer（响应带 `WWW-Authenticate: Bearer`） |
| 403 | `DEMO_SESSION_REQUIRED` | `DEMO_ACCESS_MODE=restricted` 且 `huice_demo_session` 缺失、签名不符或已过期（格式 `v1.<过期秒>.<HMAC>`，用独立机密 `DEMO_SESSION_SECRET` 签发，见 `infra/deploy/issue-session.py`） |
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
