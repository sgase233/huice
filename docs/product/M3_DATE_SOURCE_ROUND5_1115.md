# M3 对 PR #69 `cb81ee9` 的第五轮复核与 SRDI 处置决定（2026-11-15）

> **性质：M3 对 M2 复审意见、M1 修复与 SRDI 日期口径的业务复核记录。** 本文给出 SRDI（深工信规〔2025〕8号）的 A/B 处置意见，并核对 M2 的 P1/P2 与 M1 的修复。**不是 VIS-06 验收通过，不是三方签收，不批准 SMART/GREEN 或 SRDI 的发布。** SRDI 的 B 方案涉及已发布行，仍须 M2 与项目负责人明确同意，M3 不代替他们决定。
>
> **基线：** PR #69 head `cb81ee9cc17f8ce744e5c88c8472d791ebaad9cc`（2026-11-14T16:34:31Z），12 个提交、20 个文件。M2 复审：2026-11-14T15:55:19Z（`issuecomment-6880159672`）、16:19:21Z（`issuecomment-6880525488`）；M1 修复/回告：16:06:34Z、16:09:13Z、16:11:32Z、16:13:12Z、16:14:38Z、16:37:46Z（`3160bb6`、`2c1a7ac`、`a980687`、`cb81ee9`；最后一条 `issuecomment-6880800795` 报组合验证 1431 passed / 13 skipped / 90 subtests）。
>
> **合并状态（复核时）：** GitHub 报 `mergeable=false`、`mergeable_state=dirty`。`origin/main` 已到 `a1ec7e1`（合并 PR #74/#75），PR 基线仍是 `c84b4b7`；本地 `git merge-tree` 显示 `infra/README.md`、`infra/deploy/README.md`、`infra/deploy/migrate.sh`、`services/api/tests/test_migrate_script.py` 四处冲突。head `cb81ee9` 在复核时**没有 check-runs 结果**（total 0），不能记为 CI 通过。
>
> **边界：** M3 未连库、未执行迁移/回填/修正脚本，未独立运行后端测试或访问云端。SRDI 的云端值来自 M1 的回告；细则原文由 M3 于 2026-11-15 重新访问官方页面核对。
>
> **后续（2026-11-15）：** M1 在 `2a26e53` 把 SRDI 的 A 展示口径写入 README/实现文档，但把 commit message 写成「M3 决定 A」。M3 在 [M3_DATE_SOURCE_ROUND6_1115.md](M3_DATE_SOURCE_ROUND6_1115.md) 更正：B 是目标，A 是临时口径；另记录 M2 两条 P1 与 `v1.0` 降级脚本的补充要求。

## 1. 结论先行

| 项 | 本轮结论 | 状态 |
| --- | --- | --- |
| M2 P1：回填默认插入的并发覆盖窗口 | `3160bb6` 改为 `ON CONFLICT DO NOTHING` + rowcount 回滚 + 并发用例 | 方向通过；待 M2 复审 |
| M2 P1：草稿修正覆盖并发编辑 | `3160bb6` 加 `FOR UPDATE` + 读到的 `revision` 约束，命中 0 行整批回滚 | 通过（关闭 M3 的 ETag 担心） |
| M2 P2：提取文本被登记为原附件元数据 | 新导入改为 `.txt`/`text/plain`/文本哈希/真实对象键；云端已入库两行选 A 不改 | M3 接受 A，但附完整性限制（§3） |
| M2 P1：`--overwrite` 未限定获准集合 | `cb81ee9` 改为「覆盖计划 + 逐条旧值校验」，计划外冲突整批回滚 | 方向通过；待 M2 复审与 CI |
| M3 P3/P4（docstring、幂等、过期表述、重新干跑、`published_at` 空值） | `a980687` 已逐项修复，并补幂等用例 | **关闭** |
| SRDI（深工信规〔2025〕8号） | **选 B（受控修正），但目标值不能用 `published_at`/NULL 草率占位**：细则第二十四条明确「自印发之日起施行，有效期5年」，应据此设置；需先补细则来源并核对印发日 | 待 M2/负责人同意后执行；执行前按 A 的展示口径 |
| `v1.0` | 仍待 M2 与项目负责人同意；建议与 SRDI 的 B 合并到同一维护窗口 | 保持待办 |
| PR #69 合并 | 与 `main` 冲突、head CI 未出、M2 未复审 `cb81ee9`，三项非业务阻断 | 未就绪 |
| VIS-06 | B1b、M2 展示证据、M3 页面复核、F12 仍未完成 | **仍未验收** |

## 2. M2 复审与 M1 修复：M3 侧核对

### 2.1 回填与草稿修正的并发保护

- `3160bb6` 把默认插入改为 `INSERT ... ON CONFLICT (policy_version_id) DO NOTHING`，并发事务抢先插入时本次 `rowcount=0`、整批回滚返回 3；覆盖模式改为 `UPDATE ... WHERE policy_version_id=%s AND provenance=%s`，旧值变化则不命中；读取加 `FOR UPDATE`。
- 草稿修正 `fix-draft-effective-dates.py` 先 `SELECT ... FOR UPDATE`，UPDATE 同时约束读到的 `revision`；读后有人另存则命中 0 行、整批回滚。
- M3 业务侧只需确认：这两处不会在「M3 已核对的值」之外产生静默改写。按代码与回告，方向成立；M3 不替代 M2 的代码复审，也不把 M1 的本地测试数字当作独立验证。

### 2.2 `--overwrite` 计划化

`cb81ee9` 新增 `--write-overwrite-plan` / `--overwrite-plan`：干跑生成冲突集合与旧值，正式执行逐条校验「在计划里」且「旧值与计划一致」，计划外冲突或旧值变化整批回滚；只给 `--overwrite` 不给计划直接拒绝。这与 M3 第三轮「先确认冲突恰好 SMART/GREEN 两条，多出任何一条就停」的要求一致。**此前云端已执行的两次受控操作是 M1 回告的实际结果，本条的脚本加固不反过来推断那次执行覆盖了其他记录**（这一点 M2 已写明，M3 同意）。

M1 在 16:37:46Z 的 `issuecomment-6880800795` 报 `m1/e3-combined-verify` 组合验证 **1431 passed / 13 skipped / 90 subtests**（`011` 与 `012` 按序执行）。这是 M1 侧证据，M3 未独立重跑；PR head `cb81ee9` 本身的 check-runs 在复核时仍为 0，重推后需重跑。

### 2.3 提取文本与原附件元数据（M2 P2）

M1 已把新导入改为如实登记提取文本（`.txt`、`text/plain`、文本哈希、真实对象键），并把原附件名/哈希放在清单的 `originalName` / `originalSha256`。云端已入库的 SMART/GREEN 两行选 **A：不改**。M3 接受 A，但明确边界：

- **M3 对 9 条旁表 provenance 的批准仍然成立**：批准对象是「绑定到该版本自己的哪一条 `policy_source`」，不是原附件的二进制完整性。
- 已入库两行的 `content_hash` / `byte_size` 是**提取文本**的哈希/大小，不是原附件哈希；`file_name` 仍是首次导入时的原附件名。页面、材料完整性和 VIS-06 不得把它们当原附件完整性证据。
- SMART 的 `originalSha256` 目前没有（内层 `.docx` 哈希本轮未单独计算）；GREEN 的 `.doc` 原件 SHA-256 已记录（`7d021ee0…`）。因此 SMART 不能声称「原附件哈希已验证」，GREEN 可以引用清单中的原件哈希但不得把它写成 `policy_source.content_hash`。
- 两行未发布，发布前统一处理；**本轮不因这条重开 B1a 批准，也不要求改已入库行**。

## 3. SRDI（深工信规〔2025〕8号）：选 B，但目标值必须改

### 3.1 事实核对

M1 回告的云端值（M3 未查库）：

| 项 | 值 |
| --- | --- |
| `review_status` | `published` |
| `effective_from` / `effective_to` | 2025-10-15 / 2025-11-10 |
| `application_window` | 2025-10-15—2025-11-10（18:00 前？原文只写申报时间） |
| 8 个已发布版本中同值者 | 仅此一条 |

M3 于 2026-11-15 重新访问该版本对应的《深圳市工业和信息化局优质中小企业梯度培育管理实施细则（2025年修订版）》（`https://www.sz.gov.cn/szzt2010/zdlyzl/jjshzc/content/post_12435148.html`，页面 200、49,372 B，与既有记录 `047835c1…` 一致），第二十四条原文：

> 本实施细则自印发之日起施行，有效期5年。《深圳市工业和信息化局优质中小企业梯度培育管理实施细则》（深工信规〔2022〕7号）同时废止。

页面「信息提供日期」为 **2025-10-14**。因此：

1. **SRDI 的有效期有明确的原文条款，不是「原文未单列」**。当前回填数据把 `effectiveFrom/To` 标 `unverified` 并写「原文未单列有效期，登记值等于申报窗口」，这个 note 对**申报通知**成立，但对**细则本身**不成立；它是 F4 版本粒度问题的直接后果——版本号对应细则，`source_url` 却是年度申报通知，细则正文没有作为该版本的 `policy_source`。
2. **窗口关闭 ≠ 政策到期**。按当前库值，2025-11-10 之后 `resolve_validity` 会按有效期已过判 `closed`，`staleReasons` 可能含 `policy_expired`；这与细则「有效期5年」矛盾。
3. **不能只把窗口日期换成 `published_at`/NULL 就称为修好**。M1 的 B 方案值 `effective_from=published_at`（2025-10-13，通知落款）、`effective_to=NULL` 仍是占位，丢掉了细则第二十四条的 5 年有效期；`published_at` 也不是细则的印发日。

### 3.2 决定：B（受控修正），按细则第二十四条设置

**M3 的业务意见选 B，不是 A。** 理由：

- A 只改页面文案，库里的 `effective_to` 仍是窗口终点，`resolve_validity` / `staleReasons` / 报告仍可能报 `policy_expired`，属于把已知错误留在数据层；
- B 是唯一能让「有效期」和「申报窗口」在数据层分开的做法，与 SMART/GREEN 的处理方向一致。

**但 B 的目标值必须按以下顺序确定，不能用占位值草率收尾：**

1. **补细则来源**：把 S4b（细则正文，`post_12435148`）作为该版本的 `policy_source`，或经 M1 说明现有版本模型下如何把细则绑定为该版本的来源；`locator` 用「第二十四条」。在细则来源未绑定前，不能把有效期标 `verified`（来源必须属于该版本）。
2. **核对印发日**：细则写「自印发之日起」，页面信息提供日期是 2025-10-14，但**不等于印发日**。请从官方公报/文件版式核对实际印发日期。
   - 若取得印发日：`effective_from` = 该日（`verified`），`effective_to` = 5 年后的前一日（`derived`，basis=第二十四条，并注明「5 年减 1 日」的推算口径；与 HIGHTECH/RD-SUBSIDY 的一日差异同样标注）。
   - 若在维护窗口前无法核对印发日：允许按 M1 原方案用 `effective_from=published_at`、`effective_to=NULL` 作**中性占位**，但必须在 note 写明「细则第二十四条有效期 5 年尚未承载，印发日待核实」，provenance 保持 `unverified`/`unknown`；不得把这个占位写成真实有效期。
3. **受控执行**：SRDI 已发布，`001` 的守卫不允许 UPDATE/DELETE。B 属于例外操作，必须：
   - 取得 **M2 与项目负责人的明确同意**；M3 不代替他们同意，M1 不得单方面绕过 `policy_version_guard_published`；
   - 在维护窗口执行；执行前保存该行快照（id、version、`review_status`、`effective_from/to`、`revision`、`xmin`、source 行数）；
   - 先查引用（评估 run、发现候选、其他表）；有引用时说明读取时视图会变化，但不改写历史 run；
   - 只对该行临时绕过守卫，操作后立即恢复，并用一次「对已发布行的 UPDATE 被拒绝」的检查证明守卫重新生效；
   - 回告操作前后 12 行 / 8 已发布 / 4 未发布、该行 id/状态/日期/revision、已发布行 `xmin` 指纹、守卫验证结果；
   - 在 PR/记录中写明为什么绕守卫、谁批准、何时执行，作为一次性例外。
4. **provenance 同步**：B 执行后，`date-provenance-backfill.json` 中 SRDI 的 `effectiveFrom/To` 状态、locator、note 需要改；这是对已批准数据的变更，M1 提交新数据后由 M3 重新复核并更新 `reviewEvidence`，不能用旧批准覆盖新值。

### 3.3 B 获准前的临时展示口径（A）

在 B 获 M2/负责人同意并执行前，页面与报告按 A 口径：

- 标题/结论用 **「申报期已结束」**（推荐）或「本年度申报受理已截止」，不写「政策已过期」「政策失效」；
- 详情可写「本年度申报受理已于 2025-11-10 截止；政策有效期见实施细则第二十四条（自印发之日起 5 年，印发日待核实）」；
- 若接口 `validity=closed` 且 `staleReasons` 含 `policy_expired`，页面不得直接把原因显示为「政策到期」；应能区分「申报窗口关闭」与「政策有效期届满」，或由 M2 明确映射并留证据；
- provenance 的 `effectiveFrom/To` 本就是 `unverified`，页面不得把它们显示为已核实。

### 3.4 为什么不选永久 A

A 的文案修正无法改变 API 的 `validity`/`staleReasons` 和报告中的 `policy_expired`，也无法给后续消费者一个正确的有效期。SRDI 是已发布、会进入演示与报告的政策；把窗口终点留在有效期字段，属于 M3 一直要求避免的「窗口当有效期」。因此 A 只能作临时展示口径，不能作为最终处置。

## 4. `v1.0` 与 SRDI 合并到同一维护窗口

两条都是「已发布行 + 守卫例外」，都需要 M2 与项目负责人同意。建议：

- 一次维护窗口同时处理 `v1.0` 的 B（第三轮 §4 六条条件）与 SRDI 的 B（本文件 §3.2 条件）；
- 分别保存快照、分别验证守卫恢复、分别回告；
- 若只能做一条，先做 `v1.0`（测试数据出现在政策列表是演示硬伤），SRDI 保持 A 的展示口径并记录为已知限制；
- 未获同意前，M1 不执行任何一条。

## 5. PR #69 合并状态：三项非业务阻断

1. **与 `main` 冲突**：`origin/main` 已到 `a1ec7e1`（PR #74/#75），PR 基线 `c84b4b7`；冲突文件：`infra/README.md`、`infra/deploy/README.md`、`infra/deploy/migrate.sh`、`services/api/tests/test_migrate_script.py`。M1 需先合并 `main` 并解决冲突，尤其确认迁移编号（`011` 与 B2 的 `012`）和 `migrate.sh` 的顺序。
2. **head CI 未出**：复核时 `cb81ee9` 的 check-runs 为 0；合并/重推后需重跑三项 CI，不能沿用 `29dfe23` 的结果。
3. **M2 未复审 `cb81ee9`**：M2 最后一条复审针对 `a980687`；`cb81ee9` 的覆盖计划改动需 M2 再过一眼。

这三项不改变 M3 对 B1a 数据与业务语义的批准；数据文件 `date-provenance-backfill.json` 在 `cb81ee9` 与 `29dfe23` 逐字节一致，M3 批准仍对应同一份数据。

## 6. VIS-06 状态

B1a 已完成；本轮不产生「通过」项：

- B1b 读侧未完成；M2 页面展示证据未提供；M3 页面复核未执行；F12 `published_at` 来源未承载；
- SMART/GREEN 未发布；SRDI 的日期口径在 B 执行前仍不正确，页面只能按 A 口径；
- `source_register.csv` 中 SMART/SRDI/GREEN 的有效期仍等于窗口；它是冻结评测输入（sha256 含 `1d4c2b1e…`），本轮不改；若修正需与评测快照重出、`inputs`/`verify` 更新一起安排。

## 7. 本轮验证与未执行事项

**已做：**

- 读取 M2 两条复审、M1 五条回告与 `3160bb6`/`2c1a7ac`/`a980687`/`cb81ee9` 的改动；
- 核对 `date-provenance-backfill.json` 在 `cb81ee9` 与 `29dfe23` 逐字节一致；
- 重新访问 SRDI 细则官方页面，核对第二十四条原文与页面信息提供日期；
- 用 `git merge-tree` 确认 PR 与 `main` 的四处冲突；
- 核对 head `cb81ee9` 的 check-runs 为空。

**未做：**

- 未连库、未执行 `011`/回填/草稿修正/SRDI 修正/`v1.0` 清理；
- 未独立复核云端 `revision`/`xmin`/SRDI 查询，未重跑后端测试或 CI；
- 未取得 M2/负责人对 B 的同意，未替代他们决定；
- 未改 `infra/**`、`data/**`、契约、代码或评测输入；
- 未把本轮写成 VIS-06 验收通过或三方签收。

## 8. 本次修改文件

- 新建：`docs/product/M3_DATE_SOURCE_ROUND5_1115.md`（本文件）。
- 更新：`docs/product/POLICY_SCOPE.md` VIS-06 节（加第五轮复核与 SRDI 决定）。
- 更新：`docs/product/M3_DATE_SOURCE_ROUND4_1114.md`（补 head 后续与评论链接）。
- 更新：`docs/devlog/M3.md`（记录本次实际工作）。
- PR #69 复核评论：https://github.com/sgase233/huice/pull/69#issuecomment-6880870517
- **未修改**：`infra/**`、`contracts/**`、`apps/web/**`、`services/api/**`、`data/**`、冻结输入。
