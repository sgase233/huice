# E2 政策发现 PR #64 M3 业务审查（政策事实、口径与验收）

> **状态：M3 静态评审输入，2026-11-12。不是验收通过记录，也不是 M1/M2 的签收。**
> 本文核对 PR 代码、接口草案、首批来源样本与 DISC-AT 验收边界；不修改 M1/M2 代码，不代替三方联合验收。
>
> **评审对象：** PR #64 `接口：实现 E2 政策发现、候选暂存与后台配置`，最新 head `00c02f19f6d5ca2c361bdc016598be3f16e373de`（2026-11-12T15:42Z，北京时间 23:42）。5 个提交：`0005c88` → `9791978` → `45d4c5c` → `70e00d5` → **`00c02f1`**；相对 base `5d1adeb` 共 40 个文件、+8,728/−159。PR 仍为 open，`mergeable_state=clean`。
>
> **审查依据：** [WORKING_GUIDE](../../WORKING_GUIDE.md)、[开发计划](../DEVELOPMENT_PLAN.md) E2/D3、[扩展接口设计](../EXTENSION_INTERFACES.md) §4、[验收矩阵](ACCEPTANCE.md) DISC-AT-01—07、[首批来源](DISCOVERY_SOURCES.md)、[`discovery_sources_phase1.csv`](../../data/policies/discovery_sources_phase1.csv)、[政策范围](POLICY_SCOPE.md)、PR 描述及 M1/M2 全部评审回复。
>
> **本地更新说明：** 本机 `git fetch` 曾因 `github.com:443` 代理不可用失败；后以 `-c http.proxy= -c https.proxy=` 成功更新 `origin/main` 至 `44a637d`、`origin/m1/e2-policy-discovery` 至 `00c02f1`。本文不采用旧 head `45d4c5c` 或 `70e00d5` 的结论。

## 1. 结论

**M3 不签收 E2，也不把 DISC-AT-01—07 任何一项写成“通过”。**

`00c02f1` 已经处理了 M3 在 `70e00d5` 上提出的三项主要问题，并且 M1 明确采纳了附件范围建议 B（本轮不建附件来源链路，改为如实说明并排除 S03/S04/S05）：

1. **跨站同文判重已修复。** 新增 `body_fingerprint`（长句集合指纹）；我按新算法复现 S01/S10 得到同一指纹 `6bf01b236c58…f0d38`，M1 的结论可复现。换 URL 同内容现在能判重。
2. **框架页误判已修复（样本层面）。** `looks_like_article` 以 60 字为门槛；D 根页 0 字、F 根页“点击跳转”4 字、F 的 `currentissue` 0 字，都不再产伪候选，而记来源级 `unsupported_list`。60 字只是启发式，长导航页仍可能通过，需真实栏目实测。
3. **附件口径已按 B 收敛。** 文档、契约和提示都写明：附件不下载、不解析，附件内容不能作为候选草稿的引用来源；S03/S04/S05 这类“条件主要在附件”的来源暂不纳入 E2 自动发现验收；条件只在附件时走离线转换 + 人工导入（仅 PDF/UTF-8 文本）。**这不是附件链路已实现，而是把范围缩小并如实说明。**

**仍有 2 个 P1 业务问题，建议修复后再合并：**

- **P1-A 同 URL 内容变化可能被长句指纹误判为重复。** `runner.py:349` 在判断 `version_changed`（`:360`）之前先用 `body_fingerprint` 查重并直接 `continue`；而指纹只取 ≥40 字的句子（`fetch.py:578`）。我用当前函数复现：只改短句中的截止日 `2026→2027`、金额 `100→200 万`、人数 `300→500`、适用范围 `深圳市→广东省`，指纹都不变。**同 URL 的申报截止日/金额变化会被当成重复跳过，直接违反 DISC-AT-04“同 URL 改内容提示版本变化”，且是最不该漏的一类变化。**
- **P1-B HTTP 附件被过滤后，附件提示也会消失。** `fetch.py:628` 的 `find_attachments` 静默丢弃 `http://` 链接；`attachmentCount`（`schemas.py:736`）与 `ATTACHMENT_NOT_EXTRACTED` 都只依赖过滤后的列表。页面只有 HTTP 附件时，列表显示 0、没有提示，审核员会以为“页面没有附件”。应单独记录“存在但未纳入的附件链接”并给出可见提示，不能把“过滤后为空”解释为“没有附件”。

**附件是否本轮必做：** 全平台、全格式的附件自动获取/抽取/引用不必作为 E2 后端合并的绝对前置；但 S03/S04/S05 若要算 E2“已支持”，附件链路就是必做。M1 已按 B 明确排除这三类来源，M3 接受该缩小范围的方案；验收时必须把排除写进清单，不能用“提示已显示”冒充附件已核对。

**发布口径：** `ATTACHMENT_NOT_EXTRACTED` 当前**不阻断发布**，这是 M1 明确记录的取舍。M3 接受“没有逐附件处置记录时，直接阻断会让带附件政策永远发不出去”的理由，但必须同时记录：**当前系统没有逐附件确认，提示不等于已核对；附件型来源在能力补齐前不纳入自动发现验收。** 后续若要把附件纳入，需先补逐附件 disposition（审核员、时间、URL/哈希/大小/类型、处置结论、附件引用），再改为未处置即阻断。

**未执行证据：** 真实抓取器、真实模型、云端部署、010 迁移、三方联合验收均无证据，不能写“通过”。

## 2. 评审回复核对

| 轮次 | M2 复核 head | 主要问题 | M1 回应 |
|---|---|---|---|
| 2026-11-12 10:11Z | `0005c88` | 并发幂等 P1、正文失败被报成无更新 P1、预算未预留输出 P1、附件证据 P2 | `9791978` 修复前三项，附件只加提示 |
| 11:25Z | `9791978` | 附件提示编辑后丢失 P1、幂等作用域 P2、正文类型 P2 | `45d4c5c` 改为派生提示、补内容类型 |
| 13:45Z | `45d4c5c` | 草稿 API-06/API-05 重算后附件提示仍丢失 P1 | `70e00d5` 用 `PRESERVED_SOURCE_FINDING_CODES` 保留来源级提示，并补回归 |
| 14:35Z | `70e00d5` | 无新增明确代码阻塞；附件是否阻断发布、完整附件范围交 M3；幂等作用域文档分叉待签收 | M1 明确“倾向不阻断，交 M3 定” |
| 15:43Z（M1 回 M3） | `70e00d5` → `00c02f1` | M3 三项主要问题逐条回应：附件按 B 缩小范围、跨站指纹、框架页闸门 | `00c02f1` 实现并补 8 条测试 |
| 15:49Z | `00c02f1` | M2 新提两项 P1：长句指纹漏短句变更、HTTP 附件过滤导致提示消失 | 本轮 M3 复核确认两项成立 |

M2 在 `45d4c5c` 指出的“草稿再次保存/重抽后提示丢失”已在 `70e00d5` 修复，并有 `test_the_attachment_note_survives_saving_criteria_and_re_extraction` 覆盖；本轮不再作为 open P1。

## 3. 具体问题（按严重程度）

### P1-A 同 URL 内容变化可能被长句指纹误判为重复（DISC-AT-04）

**位置：**
- `body_fp = body_fingerprint(article.text)` 在 `version_changed` 之前查重并 `continue`：[`runner.py:349`](https://github.com/sgase233/huice/blob/00c02f1/services/api/app/discovery/runner.py#L349)
- 指纹只取 ≥40 字的句子：[`fetch.py:578`](https://github.com/sgase233/huice/blob/00c02f1/services/api/app/discovery/fetch.py#L578)
- `version_changed` 在其后才计算：[`runner.py:360`](https://github.com/sgase233/huice/blob/00c02f1/services/api/app/discovery/runner.py#L360)

**触发场景：** 同一 URL 的政策正文更新，只改短句中的申报截止日、资助金额、人数上限、适用范围或申报批次；长条款文字不变。

**复现（2026-11-12，按 `00c02f1` 的 `body_fingerprint` 函数）：**
- 只改“申报截止时间为 2026 年 12 月 31 日 → 2027 年 12 月 31 日”：指纹相同。
- 只改“单个项目资助金额不超过 100 万元 → 200 万元”：指纹相同。
- 只改“企业从业人数不超过 300 人 → 500 人”：指纹相同。
- 只改“本指南适用于深圳市企业 → 广东省企业”：指纹相同。
- 原因：这些短句均不足 40 字，不进入长句集合；指纹只由两条不变的长条款组成。

**影响：** 同 URL 的真实政策修订被计为重复、直接跳过，不会产生 `versionChanged` 候选，也不会提示旧结论失效。这是 DISC-AT-04 明确要求的“同 URL 改内容”场景，且截止日/金额/人数/适用范围正是业务最敏感字段。M2 也独立复现了截止年份问题。

**建议：**
- **同 URL 规则优先于指纹判重**：若 `page_row is not None` 且 `content_hash` 不同，必须按新候选/`versionChanged` 处理，不能用 `body_fingerprint` 自动跳过。
- 跨站同文可以继续用 `body_fingerprint`，但建议把命中作为“疑似重复”提示，或把短句也纳入规范化比较；至少不能把“长句相同”当成“完整正文等价”。
- 补测试：短句日期、金额、人数、适用范围、条款顺序变化；同 URL 内容变化；跨站同文 S01/S10。M1 新增的 `test_a_changed_page_is_a_new_candidate_marked_version_changed` 只覆盖了长条款变化，未覆盖短句。

### P1-B HTTP 附件被过滤后，附件提示也会消失

**位置：**
- `find_attachments` 静默丢弃非 https 链接：[`fetch.py:628`](https://github.com/sgase233/huice/blob/00c02f1/services/api/app/discovery/fetch.py#L628)
- `attachmentCount` 只数过滤后的列表：[`routes/discovery.py:518`](https://github.com/sgase233/huice/blob/00c02f1/services/api/app/routes/discovery.py#L518)
- `ATTACHMENT_NOT_EXTRACTED` 只由 `attachment_urls` 派生：[`routes/discovery.py:529`](https://github.com/sgase233/huice/blob/00c02f1/services/api/app/routes/discovery.py#L529)

**触发场景：** 页面附件链接只有 `http://…gov.cn/…pdf`，或 HTTP/HTTPS 混合且 HTTP 是唯一含关键条件的附件。

**影响：** `attachmentCount=0`、`validationFindings` 没有 `ATTACHMENT_NOT_EXTRACTED`；审核员把“0 个附件”当成“没有附件”，可能发布只覆盖正文的不完整政策。这与“无数据/未知/错误必须可区分”的验收口径冲突。

**建议：** 保留 `attachmentUrls` 只收 https 的契约，但另记一个可见的“发现但未纳入的附件链接数/原因”（如 `unsupportedAttachmentCount` 或 finding `ATTACHMENT_LINK_UNSUPPORTED`）；至少不能把过滤后的空列表解释为页面没有附件。补测试：仅 HTTP、混合 HTTP/HTTPS、编辑/提交草稿后提示仍可见。

### 范围边界：附件条件无法建立引用（已按 B 缩小范围，不再作为代码阻塞）

**位置：** DISC-07 只建正文来源 [`discovery.py:343`](https://github.com/sgase233/huice/blob/00c02f1/services/api/app/routes/discovery.py#L343)；`_criteria_for_draft` 把所有 span 强改到 `draft.sources[0]` [`discovery.py:834`](https://github.com/sgase233/huice/blob/00c02f1/services/api/app/routes/discovery.py#L834)；抽取只读 `kind=body` [`agents/extraction.py:280`](https://github.com/sgase233/huice/blob/00c02f1/services/api/app/agents/extraction.py#L280)。

**现状：** M1 已采纳 M3 的建议 B：文档、契约和提示都明确“附件内容不能作为候选草稿的引用来源”，S03/S04/S05 暂不纳入 E2 自动发现验收；条件只在附件时走离线转换 + 人工导入（仅 PDF/UTF-8 文本，DOC/DOCX/ZIP 需转换）。**这解决了“提示让人误以为可补录”的口径问题，但没有实现附件链路。**

**M3 判断：** 不再把“附件链路未实现”作为本 PR 的代码阻塞；但验收必须把 S03/S04/S05 排除写进清单，且不能把 `ATTACHMENT_NOT_EXTRACTED` 当作“附件已核对”。若负责人要求这三项进入 E2 演示/验收，则仍需补最小附件来源/引用链路。

### P2-1 附件内容变化不进入判重

`content_hash` 和 `body_fingerprint` 都只覆盖正文；`attachment_urls` 不参与哈希或版本判断。若 S03 的申报指南附件在正文 HTML 不变时更新，系统仍会判为同内容重复，不提示版本变化。对“关键信息只在附件”的来源，这会漏掉政策修订。当前因 S03/S04/S05 已排除出 E2 自动发现验收，风险被缩小；但若后续把它们纳入，必须先补附件指纹。

### P2-2 60 字正文闸门只是启发式

[`looks_like_article`](https://github.com/sgase233/huice/blob/00c02f1/services/api/app/discovery/fetch.py#L567) 以规范化后 ≥60 字判正文。D/F 已知框架页被拦住；但长导航页、带少量正文的入口页仍可能通过，合法的短通知（<60 字）也可能被误判为 `unsupported_list`。需用真实栏目样本验证，不能把“D/F 样本通过”写成“栏目可列取”。

### P2-3 多窗口无法表达

[`CandidateMetadata`](https://github.com/sgase233/huice/blob/00c02f1/services/api/app/schemas.py#L701) 只有一个 `applicationWindow`。S03 有两批网络填报和两批书面材料窗口；当前只能填合并区间，容易让时间线误读。即使 S03 暂不纳入 E2 自动发现，人工导入/后续附件支持时仍需多窗口或“合并值 + 分批说明”。

### P2-4 人工导入格式限制需在验收中写明

[`ingestion.py:182`](https://github.com/sgase233/huice/blob/00c02f1/services/api/app/agents/ingestion.py#L182) 的 `_decode_text` 只接受 UTF-8 文本；PDF 单独解析；DOCX/DOC/ZIP 会因二进制被 415 拒绝。M1 已在文档/契约写明“仅 PDF 或 UTF-8 文本，DOC/DOCX/ZIP 需先转换”；验收时要把“离线转换”作为人工路径的前置条件，不能假设系统能直接读附件包。

### P2-5 验收证据缺口

- `version_changed` 测试只覆盖长条款变化，未覆盖短句日期/金额/人数/范围（见 P1-A）。
- 没有同 URL 内容变化的真实政策页面样本；S08/S09 是不同 URL/版本。
- 没有“提交与过期竞争”的并发测试；没有配置并发 PUT、30 分钟墙钟超时的测试。
- 没有真实站点抓取、真实模型、云端部署、010 迁移、三方联合验收证据。
- 真实栏目列表分页、robots/授权、浏览器渲染/JS 仍未验证。

### P3-1 PR 描述未同步

描述仍写 1161/142，且接口表漏掉 DISC-06 PATCH；最新 M1 评论是 1311/13。建议合并前更新描述。

### P3-2 契约未并入正式 OpenAPI，幂等作用域文档分叉

- E2 契约仍在 `contracts/openapi.e2-draft.yaml`，未并入 `contracts/openapi.yaml`，前端类型未据此生成；M2 明确要求签收后再集成。
- [`EXTENSION_INTERFACES.md:31`](../EXTENSION_INTERFACES.md#L31) 写“作用域包含操作者”，实现是“按操作、不含操作者”，M2 已接受实现但要求同步文档。契约签收前必须统一。

### P3-3 本 PR 没有前端

`apps/web/**` 未改动。DISC-AT-07 的用户可见反馈、候选列表/详情/编辑/提交页面、运行记录页面都要 M2 实现；当前只能说后端接口具备，不能评价 UI 验收。

## 4. 附件范围与发布口径

### 4.1 附件是否本轮必做

1. **全平台全格式自动附件链路：不必作为本 PR 合并的绝对前置。** #47 明确的是“提交前抽取结构化条件”，未明确要求自动解析附件；M1 指出共享 `agents/extraction.py` 只读正文，改它会牵动 API-04，适合单独 PR。
2. **首批样本范围内：S03/S05 是必做或必须排除。** S03 的申报窗口只在附件 1，S05 的窗口只在 `.doc`，S04 的认定标准/知识产权指标在 DOCX。M1 已在 `docs/IMPLEMENTATION_AND_INTERFACE.md` §4.5 第 7 条明确：S03/S04/S05 暂不纳入 E2 自动发现验收。
3. **当前口径：** 本轮选 B（明确排除）。这不是“附件已覆盖”，而是“附件链路未实现、相关来源不在 E2 自动发现验收范围”。验收清单必须逐项写明排除。

### 4.2 存在 ATTACHMENT_NOT_EXTRACTED 时是否阻断发布

**当前实现：不阻断。** `publish_policy` 只拦条件级 findings 或 `POLICY_BLOCKING_CODES`（[`admin_policies.py:407`](https://github.com/sgase233/huice/blob/00c02f1/services/api/app/routes/admin_policies.py#L407)），`ATTACHMENT_NOT_EXTRACTED` 不在其中。

**M3 口径：**
- 接受当前“不阻断”的取舍，因为该码会被 `PRESERVED_SOURCE_FINDING_CODES` 永久保留且没有解除机制；直接阻断会让所有带附件政策永远无法发布。
- 但必须同时记录：**提示不等于附件已核对；系统没有逐附件确认。** 在本轮，S03/S04/S05 不纳入 E2 自动发现验收；附件型候选若被提交/发布，只能是人工路径的明确例外，不能算 DISC-AT-05 通过。
- 后续若要把附件纳入，需先补逐附件 disposition（审核员、时间、URL/哈希/大小/类型、处置结论、附件引用），再让未处置附件阻断提交/发布；不要用一条不可解除的全局 finding 直接阻断。

### 4.3 提示与附件计数的可见性（最新 head）

| 场景 | 是否可见 | 依据 |
|---|---|---|
| 候选详情 DISC-05 | 是 | `_candidate_detail` 派生 |
| 候选编辑 DISC-06 响应 | 是 | 同上，编辑不掉 |
| 候选列表 DISC-04 | 部分 | 新增 `attachmentCount`，但没有 HTTP-only 的“未纳入”提示 |
| 提交 DISC-07 草稿 | 是 | 作为来源级 finding 落库 |
| 草稿 API-06 保存条件 | 是 | `70e00d5` 起由 `_preserved_source_findings` 保留 |
| 草稿 API-05 重新抽取 | 是 | 同上 |
| 发布 API-07 详情 | 是（持久化后可见） | `PolicyDetail.validationFindings` |
| 发布闸门 | 不检查、不阻断 | 只拦 criterion 级/`POLICY_BLOCKING_CODES` |
| 解除/逐附件确认 | 无 | 没有 disposition/acknowledgment |
| HTTP-only 附件 | **否** | 过滤后 `attachmentCount=0`，无 finding |

## 5. 真实样本与证据

### 5.1 首批栏目与样本（访问日期：2026-11-10；2026-11-12 复核）

| 组 | 栏目入口 | 2026-11-10 结果 | 2026-11-12 复核 | 样本 | 可用性判断 |
|---|---|---|---|---|---|
| A | `https://www.gov.cn/zhengce/zhengceku/` | 403 / 843B | 未重试 | S01、S02 正文 200 | 栏目不可列取；只能单篇 |
| B | `https://www.sz.gov.cn/szzt2010/wgkzl/jcgk/jcygk/zdzcjc/` | 200 / 36,032B | 未重试 | S03、S05 正文 200 | 入口可下载；列表/分页未验证 |
| C | `https://www.sz.gov.cn/cn/xxgk/zfxxgj/tzgg/` | 200 / 33,739B | 未重试 | S04 正文 200 | 同上 |
| D | `https://www.sz.gov.cn/zfgb/` | 200 / 959B（疑似框架页） | 200 / 959B / 0 links | S06、S07 正文 200 | 根页不可作栏目；`looks_like_article` 已拦 |
| E | `https://www.most.gov.cn/xxgk/xinxifenlei/fdzdgknr/fgzc/gfxwj/` | 200 / 24,960B | 未重试 | S08 正文 200 | 列表未验证 |
| F | `https://www.gov.cn/gongbao/` | 200 / 898B（疑似框架页）；2016 子栏目 403 | 200 / 898B / 1 link（currentissue.htm） | S09 正文 200 | 根页不可作栏目；`currentissue` 也非正文 |
| G | `https://www.chinatax.gov.cn/chinatax/n810341/n810765/` | 403 / 144,403B | 未重试 | S10 正文 200 | 栏目不可列取；只能单篇 |

**来源确认：** `source_register.csv` 确认的是 9 个政策正文 URL，不是上述栏目入口。`DISCOVERY_SOURCES.md` 明确标注“M3 草案、未签收”，并在 §2.1/§9 说明固定正文/附件 URL 列表只是样本验证/受限方案，不提供新政策发现能力。因此：首批栏目入口都不是已确认的可自动列取来源；能确认的只有 gov.cn 域名白名单和 9 个单篇正文 URL。

### 5.2 已核实事实

- **S01 / S10（同一公告跨站）**：2026-11-10 与 2026-11-12 两次抓取，S01 27,618B、S10 27,492B，原始 SHA-256 与 CSV 一致；两页都含同一关键句。按 `00c02f1` 的 `body_fingerprint` 复现，两者指纹同为 `6bf01b236c584db2ff32eb319dc13bcf2293704362cb5aecd4a47608789f0d38`。**跨站同文判重用例成立。**
- **S03（智能终端）**：正文 200 / 38,992B；附件 ZIP 200 / 2,487,736B，SHA-256 与 CSV 一致；ZIP 内为 2 个 DOCX + 1 个 PDF；附件 1 DOCX 文本含“2025 年 6 月 13 日至 6 月 30 日 18 时”等窗口；正文只说系统 6 月 13 日开放，未给截止日。已核实：申报窗口只在附件 1，且分两批；附件包超过 2 MiB。**按 2026-11-12 口径，S03 暂不纳入 E2 自动发现验收。**
- **S05（绿色制造）**：正文 200 / 36,775B；`.doc` 200 / 95,232B，SHA-256 与 CSV 一致。2026-11-10 用 antiword 读到“网络填报 2024-12-21 至 2025-01-06 18 时”；本次只复核下载与哈希，未在当前 locale 下逐字重读。**按 2026-11-12 口径，S05 暂不纳入 E2 自动发现验收。**
- **S04（专精特新）**：正文 200 / 64,474B，正文含申报时间 2025-10-15 至 2025-11-10；3 个 DOCX 均 200，大小 23,196 / 103,906 / 12,119B。附件内容未逐份读取；“附件 1 认定标准、附件 2 知识产权指标”来自页面说明，属未核实推断。**按 2026-11-12 口径，S04 暂不纳入 E2 自动发现验收。**
- **S06/S07**：正文 200，但年度申请指南（含窗口）未取得；不得推算。
- **S08/S09**：2008 版与 2016 版高企认定办法，两个不同 URL、不同版本；2016 版明文废止 2008 版。不能当作“同 URL 内容变化”样本。

### 5.3 三类判重样本现状

| 场景 | 现有样本 | 是否跑过系统 | 结论 |
|---|---|---|---|
| 同 URL 同内容 | S01/S03/S04/S05/S10 两日原始哈希一致 | 否，只做了 curl/哈希 | 样本具备，但未通过 runner 执行 |
| 不同 URL 同内容 | S01 vs S10 | 否；用 `00c02f1` 的 `body_fingerprint` 离线复现 | 指纹相同，代码判重路径成立；仍需真库/HTTP 用例 |
| 同 URL 内容变化 | 无真实样本；S08/S09 是不同 URL/版本 | 否；现有测试只覆盖长条款变化 | **短句变化会被指纹误判为重复（P1-A）** |

**仍缺：** 同 URL 内容变化的真实政策页面样本；附件变化样本；S04 三个 DOCX 的逐条内容；S03 附件 2/3 与 PDF；S05 `.doc` 完整条件；真实 `Fetcher` 对以上页面的抓取结果（含重定向链、Content-Type、附件链接识别）；真实 DeepSeek 抽取与 token 计量；云端 E2 部署、010 在空库/旧库的迁移与回滚证据；robots/服务条款/访问授权结论；浏览器渲染/JS 结论；双人来源复核；DISC-AT-01—07 的联合验收记录、runId、截图或响应证据。

## 6. DISC-AT-01—07 验收边界

| ID | 已具备的实现 | 已有证据 | 未执行/缺口 | M3 判定 |
|---|---|---|---|---|
| DISC-AT-01 | 设置 GET/PUT + ETag、timer/CLI 到点判断、advisory lock、stale run 回收、轮换 | 本地单测/隔离库声明；M2 未复跑 | 真实 systemd timer、30 分钟墙钟超时、配置并发 PUT、多进程重叠、停机恢复、云端 | 未验收 |
| DISC-AT-02 | HTTPS/gov.cn、DNS 全地址校验、逐跳重定向、大小/超时/频率、内容类型嗅探、框架页闸门 | 单测覆盖 SSRF/DNS/重定向/大小/超时/类型/频率、框架页 | 真实 DNS/TLS/重定向链、robots/授权、真实频率、prompt-injection 专项；附件不是抓取目标，附件 SSRF 无执行对象 | 部分实现，未验收 |
| DISC-AT-03 | 正文结构化抽取、sourceUrl/fetchedAt/contentHash、原文定位、missingFields、extraction_failed、候选不写政策表 | 本地 DB/HTTP 测试；无真实模型 | 附件条件无法引用（已按 B 排除相关来源）；真实模型、真实页面、附件文本未进入抽取 | 正文路径部分具备；附件路径不具备；未验收 |
| DISC-AT-04 | content_hash + body_fingerprint + fingerprint + discovery_page + version_changed | 同 URL 同内容、跨站同文（离线指纹）、框架页有测试 | **同 URL 短句变化会被指纹误判重复（P1-A）**；同 URL 内容变化无真实样本；真库/HTTP 跨站用例仍缺 | 不成立/未验收，P1 |
| DISC-AT-05 | DISC-06 编辑、DISC-07 只建草稿、引用闸门、幂等、事务、无孤儿 | 本地并发/幂等/提交测试 | 附件型条件无法补录（已按 B 排除）；附件提示不阻断；HTTP-only 附件无提示；无 candidate→publish 端到端测试 | 正文路径部分具备；附件路径不具备；未验收 |
| DISC-AT-06 | 轮换与启动同事务、空轮询不轮换、submitted 保留、失败状态区分 | 本地轮换/失败测试 | 提交 vs 过期竞争无并发测试；真实多进程/云端无证据 | 部分实现，未验收 |
| DISC-AT-07 | 丢弃不建政策、终态原因区分、手动导入独立、disabled、`unsupported_list` | 本地丢弃/失败/disabled/框架页测试 | 无前端；无真实 UI 反馈；无“推荐停止时手动导入”端到端回归 | 后端部分具备；端到端未验收 |

**真实网站、真实模型、云端部署、数据库迁移、三方联合验收：均未看到证据。** PR 描述也自认“未连真实网站抓取、未跑真实模型、未云端部署、010 未执行”。因此不能把任何一项写成“通过”。

## 7. M3 结论与边界

**结论：**
- `00c02f1` 已处理 M3 在 `70e00d5` 上提出的三项主要问题：跨站同文判重、框架页误判、附件口径。M2 最后一轮仍提出两个 P1：短句指纹漏变更、HTTP 附件提示消失；M3 复核确认两项成立。
- M3 不签收 E2，也不建议以“E2 完成”口径合并或发布。P1-A（同 URL 短句变化被误判重复）直接违反 DISC-AT-04，是本轮最需要修的业务问题；P1-B（HTTP-only 附件提示消失）会让不完整政策被误认为完整。
- 附件范围已按 B 收敛：S03/S04/S05 暂不纳入 E2 自动发现验收；验收清单必须写明排除，不能把提示当作附件已核对。
- 如果本轮不做附件链路，DISC-AT-03/05/07 中涉及附件样本的部分不能写“通过”；DISC-AT-04 还需先修 P1-A；DISC-AT-01/02/06 仍缺真实环境和并发证据。
- 如果负责人要求 S03/S04/S05 进入 E2 演示/验收，则必须补最小附件来源/引用链路 + 逐附件确认，不能只靠提示。

**边界：**
- 本结论只代表 M3 对政策事实、业务口径和验收边界的审查；不替代 M1 的技术实现审查、M2 的前端/契约审查，也不代替负责人做发布决定。
- 没有修改任何 M1/M2 代码、契约、迁移或数据；没有自行宣称三方签收。
- M3 本机没有项目 Python 依赖（`fastapi`/`psycopg` 未安装），未复跑 M1 报告的 1311 项 pytest；本地测试结果按 M1 声明记录，M2 也未独立复跑数据库测试。
- 没有把未执行的真实网站、真实模型、云端部署、010 迁移和联合验收写成“通过”。
