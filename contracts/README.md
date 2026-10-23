# 接口契约

`openapi.yaml` 是 HTTP 字段的单一真源，当前版本 `1.0.0-rc.2`（16 个接口、请求/响应 schema 与合成示例）。人工可读说明见[功能实现逻辑与接口协作约定](../docs/IMPLEMENTATION_AND_INTERFACE.md)，补充了版本、事实期间、清单与后台读取约束。

M1 维护 OpenAPI，M2 审查前端可调用性，M3 审查业务含义。破坏性变更先更新契约与说明，由 M1/M2 确认并附迁移说明。

## 已实现范围（2026-10-23）

| 接口 | 方法路径 | 状态 |
| --- | --- | --- |
| API-01 `getHealth` | `GET /health` | 已实现；数据库不可用返回 503 |
| API-04 `importPolicy` | `POST /admin/policies/import` | 已实现；201 `PolicyDraft`（`pending_parse`）+ ETag |
| API-15 `listAdminPolicies` | `GET /admin/policies` | 已实现；`{items,nextCursor}`，无 ETag |
| API-16 `getAdminPolicy` | `GET /admin/policies/{policyId}` | 已实现；200 + ETag，缺省取最新导入版本 |

其余 12 个接口仍只有契约。服务实际暴露的接口、operationId、tags 与 security 由 `services/api/tests/test_openapi_alignment.py` 与契约逐项比对。

### 本轮契约变更（待 M2 复核）

1. `info.version`：`0.2.0-draft` → `1.0.0-rc.1` → `1.0.0-rc.2`；描述改为说明已实现范围与“导入后为 `pending_parse`”。
2. API-04 的 201 示例：`status` 由 `pending_review` 改为 `pending_parse`，`criteria` 改为 `[]`。理由：导入只做文件落库，条款抽取属 API-05；示例若显示 `pending_review`，前端会按“已完成抽取”开发并与流程冲突。**只改示例值，schema 未变**，`types.ts` 无差异。
3. `HealthResponse` 示例的 `version` 同步为 `1.0.0-rc.2`。
4. API-04 的 `files` 补充说明：第一个文件是正文（`kind=body`），其余为附件；`files` 仍为可选，不传时 `sources: []`。
5. rc.2（2026-10-23，队长决定改用 DeepSeek）：`ModelUsage.mode` 与 `PolicyAssessment.explanationMode` 的枚举值 `maas` 改为 `deepseek`，其余取值不变。**这是一处破坏性变更**，`apps/web/src/api/types.ts` 已重新生成（仅这两行），请 M2 复核；若前端已按 `maas` 写过字面量，需同步改为 `deepseek`。

## 当前实现产出的错误码

响应统一为 `{"error":{"code","message","details"},"traceId"}`，并带 `X-Trace-Id`。已实现的4个接口会返回：

| HTTP | code | 触发条件 |
| --- | --- | --- |
| 401 | `UNAUTHENTICATED` | 缺少或错误的 Bearer（响应带 `WWW-Authenticate: Bearer`） |
| 403 | `DEMO_SESSION_REQUIRED` | `DEMO_ACCESS_MODE=restricted` 且缺少 `huice_demo_session` cookie |
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
