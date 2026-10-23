# 多智能体实现协议（M1）

当前为实现前的交接说明；`app/agents/*.py` 均只有文件级注释，没有可执行智能体。对外 API 以 `huice/contracts/openapi.yaml` 为字段真源，调用流程见[功能实现逻辑与接口协作约定](../../docs/IMPLEMENTATION_AND_INTERFACE.md)；本协议只约束服务内部调用。

## 1. 两条流程与职责

| 流程 | 调用顺序 | 拦截或降级条件 | 最终产物 |
| --- | --- | --- | --- |
| 离线政策入库 | 管理路由 → 采集智能体 `ingestion` → 条款解析智能体 `extraction` → `domain/citations.py` 引用校验 → 人工复核与发布 | 非官方来源、附件不完整、原文定位缺失、数字/否定词/期间不一致、政策版本冲突 | 已审核的政策版本与规则 AST；失败时保留草稿及原因，不自动发布 |
| 在线企业判断 | 判断路由 → 协调器 `orchestrator` → 画像智能体 `profile` 快照 → 已发布规则检索 → `domain/rules.py` 四态求值 → `domain/questioning.py` 补问 → 解释与准备智能体 `explanation` → 轨迹汇总 | 未发布规则停止；缺关键事实返回“证据不足”；条款冲突暂停肯定结论；关闭或旧版本标失效；模型不可用时解释降级 | 不可变判断快照与读取时有效性视图、原文依据、优先问题、分类型清单和脱敏轨迹 |

入库与判断允许在同一 FastAPI 服务内运行，不要求独立进程、消息队列或让每个角色都调用大模型。采集、抽取、画像、解释四个角色各有明确输入、产物和失败处理；协调器负责调度，不得替代规则引擎作资格判断。模型不可用时，已审核规则仍可求值，但解释必须标明降级状态，不能伪造模型回答。

## 2. 内部交接对象

M1 在 `app/agents/contracts.py` 实现以下类型，并与 `app/schemas.py` 的对外模型映射。字段名可按 Python 风格实现，对外 JSON 仍按 OpenAPI 使用 `camelCase`。

| 对象 | 最少字段 | 约束 |
| --- | --- | --- |
| `AgentContext` | `trace_id, request_id, as_of, period, policy_version_map, enterprise_fact_version, model_version, deadline_ms` | 每步继承同一追踪 ID；版本和时间不可丢失；不传密钥。enterprise_id/policy_ids 等业务参数通过独立 AssessmentInput 传入，不塞进 context |
| `AgentResult` | `status, payload, evidence_refs, findings, usage, elapsed_ms` | `status` 为 `ok/needs_review/retryable_error/fatal_error`；`needs_review` 不等于“满足”；失败不返回伪造的成功载荷 |
| `EvidenceRef` | `policy_id, source_id, source_version, source_url, page, section, quote, text_hash` | page/section 至少一个非空；指向当前快照的已存原文。冲突保留多条引用，不压缩成一个 location 字符串 |
| `TraceRecord` | `sequence, stage, status, input_summary, output_summary, evidence_refs, policy_id, policy_version, model_or_rule_version, elapsed_ms, error_code, human_action` | 共享步骤的 policy_id/version 可空；不含密钥、完整提示词或企业敏感字段；映射到 API-14 TraceStep |

各角色的最小输入与输出：

| 角色 | 输入 | 输出 | 不允许做的事 |
| --- | --- | --- | --- |
| `ingestion` | 官方 URL、元数据、附件 | 原文文本、版本、附件关系、校验摘要与来源引用 | 未审核直接发布，抓取未授权来源 |
| `extraction` | 已存政策版本、原文片段 | `CriterionDraft[]`、原文跨度、模型使用量、校验发现 | 只凭模型文字生成最终资格结论 |
| `profile` | 用户输入的合成企业事实 | 带单位、所属期间、来源、核验状态的 `EnterpriseFact[]` | 猜测缺失财务数值，将未知写成 0 |
| `explanation` | 规则求值结果、questioning 已选问题、有效来源引用、已审材料要求 | 引用支持的解释、税收/资金准备清单、explanationMode/warnings | 改写四态结果或问题排序、编造政策条款或申报金额 |

## 3. 调度、错误与可观察性

1. 管理路由在采集/抽取后调用引用校验和人工审核；发布前必须通过校验。在线协调器只读取已发布规则，不在企业请求中临时抽取并发布政策。
2. `retryable_error` 仅用于限流、暂时网络错误等可恢复故障，并按请求截止时间与调用预算限制重试；`fatal_error` 或 `needs_review` 停止后续肯定结论。抽取按政策版本、Idempotency-Key 和请求体防重；请求 trace_id 不充当幂等键；If-Match 校验草稿 revision，防止覆盖并发人工修改。判断重算创建新 run ID，以 supersedesRunId 关联历史。
3. 规则引擎返回四态及条件级依据后，解释智能体只能包装结果。缺事实为“证据不足”，条款互相抵触为“条款冲突”；版本变化另标 `stale`，不作为第五种判断结果。
4. `app/agents/trace.py` 按 sequence 汇总阶段耗时、模型/规则版本、摘要、错误类别及人工处理。在线 run 链接所用政策版本的入库/审核轨迹，再展示在线步骤；API-14 要求管理授权并脱敏，不能仅因受限演示站已登录而放行。

## 4. 实现与验收

M1 先完成 `contracts.py` 和一个不调用 MaaS 的固定样本调度，再接入真实采集、抽取与解释。`tests/test_agent_flow.py` 至少验证：正常链路可追溯到原文；引用校验失败不能发布；缺失事实不能得到“满足”；解释智能体不能覆盖规则结果；模型暂不可用时仍能返回已审核规则的判断与明确降级说明。M2 在轨迹页展示各步骤状态、依据与耗时；M3 按 `docs/product/ACCEPTANCE.md` 核对中文含义和政策来源。
