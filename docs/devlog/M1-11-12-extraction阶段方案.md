# 决策提案：`extraction` 轨迹阶段怎么处理

> 发起：M1，2026-11-04。需要 M2 在两个方案里选一个，选定后按对应时间表执行。
> 背景：11/12 节点 M1 的「修复阻塞缺陷」与「调用/费用记录」都落在这个口径上。

## 1. 问题

契约的轨迹阶段枚举有九个值（`contracts/openapi.yaml:2386`，`TraceStep.stage`），`app/agents/trace.py:16-27` 与 `app/schemas.py:27` 与之一致。但生产代码里**从来没有写入过 `stage="extraction"`**：全仓 `stage="extraction"` 的写入点为 0，只有枚举定义。

实际会写入的步骤是：

| 阶段 | 写入点 |
| --- | --- |
| `ingestion` | `app/integrations/storage.py:366`（导入时） |
| `citation_validation` | `storage.py:616` |
| `human_review` | `storage.py:701` |
| `profile` | `storage.py:777`、`845` |
| `retrieval` / `evaluation` / `questioning` / `explanation` | `app/agents/orchestrator.py` 在线判断时 |

而抽取接口 `POST /admin/policies/{policyId}/extract`（版本在请求体 `version` 里，见 `routes/admin_policies.py:191-201`）是一次**真实的模型调用**（`MODEL_MODE=deepseek` 时），返回体里有 `ExtractionResult.usage`（tokens），轨迹里却没有任何一条对应步骤。

## 2. 影响

- 评审要看的「多智能体审计轨迹」在「导入 → 【空】 → 引用校验 → 人工审核」处断链，正文被模型抽取这一步没有留痕。
- 11/12 的「调用/费用记录」在抽取这条路径上没有轨迹落点，只能靠日志，判断路径却能在轨迹里看到——两边口径不一致。
- 前端 `apps/web/src/pages/TracePage.tsx:5` 已有「条款抽取」的标签，但永远不会显示。

## 3. 方案 A：补写入点（M1 推荐）

在抽取完成时写一条轨迹步骤：

| 字段 | 取值 |
| --- | --- |
| `stage` | `extraction` |
| `status` | 成功 `ok`；有校验发现 `needs_review`；限流 `retryable_error`；其余模型失败 `fatal_error` |
| `inputSummary` | `正文 <N> 字` |
| `outputSummary` | `条件 <K> 条、校验发现 <M> 条`；失败时为脱敏原因 |
| `objectRefs` | `[{"type": "policyVersion", "id": <版本>}]` |
| `modelOrRuleVersion` | 实际模型名或 `fixture` |
| `elapsedMs` | 本次调用耗时 |

- **契约**：结构不变，枚举本来就含 `extraction`，无需改 schema；若按「行为变更也记版本」的惯例，只在 `contracts/README.md` 的变更记录里加一行 rc.7 注记。
- **前端**：**零改动**。`apps/web/src/api/client.ts:71` 的校验白名单已含 `extraction`，`types.ts:425` 的联合类型与 `TracePage.tsx:5` 的标签也都已就绪。
- **代价**：轨迹多一条记录；`outputSummary` 的口径要定死（沿用 `trace.py:30` 的 `MAX_SUMMARY=200`，不写正文、不写提示词、不写密钥）。

### 3.1 实现口径（按 M2 2026-11-04 的五点）

1. **幂等重放不写新步骤。**`read_idempotent` 命中时直接返回既有响应、不发生新模型调用（`admin_policies.py:211-227`）。重放若也写一条 `extraction`，看起来就是又调了一次模型、还会重复计入费用，所以重放**只留一行日志**（`extract_replay idempotency_key=…`，不含正文），不写轨迹。
2. **用量与费用不进 `TraceStep`。**现有轨迹步骤没有 token / 费用字段，11/12 也**不在 API-14 加字段**——那属契约变更，要单独对齐。本轮 tokens 只走结构化日志与文档表；轨迹步骤只记 `modelOrRuleVersion`（模型名）与 `elapsedMs`。记录口径：实际调用记真实用量；`fixture` 与重放记 0 / 不记；失败记 0。金额只在文档表里折算，注明价目来源与抓取日期，拿不到价目就只记 tokens。
3. **顺序与关联。**抽取步骤与 `save_criteria` 的引用校验步骤在**同一事务**内写入，`sequence` 排在 `citation_validation` 之前（现状 `storage.py:614-624` 只写校验那一条）；`objectRefs` 用内部 `policyVersion` id；不同版本、不同政策各自独立，不互相串联。
4. **失败与状态。**`ok` = `fixture` 或真实模型成功；`needs_review` = 有校验发现；`retryable_error` = 429 限流（`UPSTREAM_RATE_LIMITED`）；`fatal_error` = 其余模型失败（503）。模型失败**写一条** `fatal_error` 步骤，摘要只写错误码；调用成功但 `save_criteria` 撞版本冲突（409 `VERSION_CONFLICT`）**不留抽取步骤**——与保存同一事务整体回滚，避免出现「有抽取、没保存」的半截轨迹；进程中断同理由事务回滚兜住。摘要与 `errorCode` 不写正文、提示词与凭据。
5. **验收覆盖。**实现 PR 必须有用例覆盖：首次抽取、重放去重（不新增步骤）、模型失败（429 / 503）、保存冲突、多版本与多政策隔离，以及 API-14 的关联与顺序。


## 4. 方案 B：从契约与代码删掉

- **改动**：`contracts/openapi.yaml:2386` 删 `extraction`；`trace.py:18`、`schemas.py:27` 同步删；契约对齐测试改。
- **前端要跟着改**（属 M2 范围）：`client.ts:71` 的 stage 白名单、`types.ts:425` 的联合类型、`TracePage.tsx:5` 的标签映射，以及 `apps/web/src/tests/admin-trace.spec.ts:55` 里那条用 `stage: 'extraction'` 的用例。
- **好处**：枚举只列真实发生的阶段，契约更「诚实」。
- **代价**：契约是破坏性变更（前端类型与校验都要跟），抽取过程在轨迹里永久不可见，调用记录失去这个落点。

## 5. 结论与时间

推荐 **A**：B 的收益只是枚举干净，代价是前端返工加轨迹断链；A 不动结构、前端零改动，而且 11/12 的调用/费用记录正好可以挂在同一条步骤上。

**2026-11-04 更新**：M2 已同意方案 A 的方向，并提出五点实现口径（幂等重放分开记、用量与费用另行定义且不进 API-14、顺序与关联、失败与状态、验收覆盖）与两处文档更正（接口路径、API-14 的读法）。这些已并入第 3.1 节与第 6 节，方案 B 因此不再作为当前选项保留，仅留作记录。

- 选 A：我在 11/05 与「调用/费用记录」一起提，同一个 PR，扩的是抽取路径与 `explanation` 路径两处。
- 选 B：请先改前端校验与类型，我的契约与后端删值 PR 排在它之后合，避免线上轨迹页先坏。

## 6. 两案都适用的约束

- 摘要不写政策正文、提示词、密钥或连接串（`trace.py` 现有规则）。
- 轨迹步骤入库后不可更新或删除（`trace_step_no_update_delete` 触发器）；但 **API-14 不是运行时刻的不可变快照**——它返回该 run 的在线步骤，再按政策版本关联入库步骤，按政策版本读取、不按运行创建时间截断。引用轨迹时按这个口径描述。
- 本提案只涉及契约枚举与轨迹写入点，不改四态判断、引用校验与缓存行为。
