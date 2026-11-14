# M3 对 PR #69 `2a26e53` 的第六轮复核与 A/B 口径更正（2026-11-15）

> **性质：M3 对 M2 新 P1、M1 `2a26e53`（SRDI 展示口径 + `v1.0` 降级脚本）的业务复核记录。** 本文更正「M3 决定 A」的表述：**M3 的目标决定仍是 B，A 只是 B 获 M2/负责人同意前的临时展示口径。** 本文不是 VIS-06 验收，不是三方签收，不批准 `v1.0` 执行。
>
> **基线：** PR #69 head `2a26e53f6b579744274be852104c36d67a8a4aa9`（2026-11-14T16:49:42Z），13 个提交。M2 复审 `issuecomment-6880892267`（16:44:14Z，暂不建议合并）；M1 回告 `issuecomment-6880960415`（16:49:42Z）。PR 仍 `mergeable=false / dirty`；`cb81ee9` 之后 `2a26e53` 的 check-runs 仍为 0。
>
> **边界：** M3 未连库、未执行 `v1.0` 降级或 SRDI 修正；未独立复核 M1 的测试与云端结果。SRDI 的 B 仍需 M2 与项目负责人同意。
>
> **后续（2026-11-15）：** M1 已在 `3b1d198` 把 A/B 口径改回「B 目标、A 临时」，并解决与 `main` 的冲突、CI 转绿；M2 又提出首次插入绑定与降级脚本指纹两条 P1。M3 的处理与 SRDI 补细则来源条件见 [M3_DATE_SOURCE_ROUND7_1115.md](M3_DATE_SOURCE_ROUND7_1115.md)。

## 1. 结论先行

| 项 | 本轮结论 | 状态 |
| --- | --- | --- |
| 「M3 决定 A」的表述 | **不准确。B 是目标，A 是 B 未获批前的临时口径。** `2a26e53` 的 commit message 与 `IMPLEMENTATION_AND_INTERFACE.md`/README 不能把 A 记成 M3 的最终决定 | 需更正记录 |
| M2 P1：覆盖计划未固定目标值/数据文件 | **同意，合并前必修。** 计划必须固定获批数据文件摘要与每条 `expectedNew`，执行时逐条比对 | 阻断合并 |
| M2 P1：附件绑定未固定到正式执行 | **同意，合并前必修。** 计划必须固定获批的 `sourceId`（及内容哈希/文件名），附件重排或替换时整批停止 | 阻断合并 |
| `v1.0` 降级脚本 | 六条条件基本映射；补 row lock/rowcount、完整引用检查、source 行数与父 policy 快照 | 方向可接受，未执行 |
| SRDI 展示用词 | 接受「申报窗口已关闭」；不要求再改「申报期已结束」；不得出现「政策已过期」 | 关闭用词问题 |
| PR 合并 | 仍与 `main` 冲突、head CI 未出、M2 两条 P1 未修 | **不同意合并** |
| VIS-06 | B1b、M2 展示证据、M3 页面复核、F12 未完成 | **仍未验收** |

## 2. A/B 口径更正：A 是临时，B 是目标

M3 第五轮的原话是：

> **M3 的业务决定：B（受控修正）**……B 获准前按 A 的展示口径。

因此 `2a26e53` 的 commit message「按 A 执行」和把 A 写进 `IMPLEMENTATION_AND_INTERFACE.md`/README 作为规则，**不能理解为 M3 已放弃 B**。当前状态应记录为：

- **SRDI 的有效期字段仍是错的**（`effective_from/to` = 2025-10-15—11-10，等于申报窗口）；A 只保证页面/报告不把它显示成「政策已过期」；
- **B 未被批准、未执行**，需要 M2 与项目负责人同意；B 的目标值按实施细则第二十四条，不是 `published_at`/NULL 草率占位（见 [M3_DATE_SOURCE_ROUND5_1115.md](M3_DATE_SOURCE_ROUND5_1115.md) §3）；
- 若项目最终决定永久采用 A（不执行 B），必须由 M3、M2 与负责人重新确认，并在验收记录里写明「SRDI 有效期字段仍为窗口值，`policy_expired` 可能由 API/`staleReasons` 产生，仅展示层抑制」；**不能用「M3 决定 A」结案**。

请 M1 在 README/`IMPLEMENTATION_AND_INTERFACE.md` 的相应段落补一句「A 是 B 未执行前的临时口径，B 仍待 M2/负责人同意」；`2a26e53` 的 commit message 不必改写，但 PR 记录以本条为准。

### 2.1 用词

M1 问是否把「申报窗口已关闭」改成「申报期已结束」。**M3 接受「申报窗口已关闭」，不要求再改**；只要全文统一、详情写清本年度受理截止日（2025-11-10），且不出现「政策已过期/政策失效」即可。

### 2.2 A 的接口边界

README 写「不得据此产出 `policy_expired`」。M3 补充：在 B 执行前，**API/`staleReasons` 仍可能按 `effective_to=2025-11-10` 产生 `policy_expired`**；如果只做展示层抑制，必须：

- 在页面/报告把原因映射为「申报窗口已关闭」；
- 不把原始 `policy_expired` 直接透出给企业；
- 在验收记录里明确「API 层仍可能含 `policy_expired`，待 B 修复」，不能写成引擎已不再产生。

如果要求 API 层也不产出，那就只能做 B（或改 `resolve_validity` 消费 provenance，属契约/实现变更），不是 A 的文案能解决的。

## 3. M2 两条 P1：M3 同意，且直接关系批准有效性

### 3.1 覆盖计划固定目标值/数据文件摘要

M3 批准的是 `date-provenance-backfill.json` 在 `29dfe23`/`cb81ee9` 的 9 条内容（逐字节一致）。当前计划只固定 `expectedOld`，新值来自执行时的数据文件；如果 M3 复核后数据文件被改（状态、locator、note），而库里旧值未变，执行会写入**未经 M3 复核的新值**。

**要求：**

- 计划包含 `dataFileSha256`（或 M3 批准记录中的同一摘要）与每条 `expectedNew`（解析后的完整 provenance）；
- 执行时校验：数据文件摘要一致、每条 `expectedNew` 与当前数据文件一致、实际写入值与 `expectedNew` 一致；
- 任一项不一致整批回滚，不能只校验 `expectedOld`；
- 补「计划生成后目标值变化」用例。

### 3.2 附件绑定固定到正式执行

M3 的 locator 批准（例如 SMART「附件1 七、受理时间（二）1/2」、GREEN「附件 `.doc` 七、受理时间（二）1」）依赖干跑时绑定到的具体 `policy_source` 行。脚本每次按 `ordinal` 重新解析第 N 份；若干跑后附件列表/顺序变化，正式执行可能把 `verified` 绑到另一份附件。

**要求：**

- 计划固定每条 `verified` 的 `sourceId`（以及 `file_name`/内容哈希）；
- 执行时校验 `sourceId` 与内容哈希未变，附件重排/替换整批停止；
- 补「附件重排」「附件替换」用例。

**在这两条修好并由 M2 复审前，M3 不同意合并 PR #69。** 这不改变 M3 对 9 条旁表 provenance 的业务批准；修的是「执行时是否仍写入 M3 复核过的那份值与绑定」。

## 4. `v1.0` 降级脚本：方向可接受，补三项

`retire-test-policy-version.py` 按 M3 第三轮六条条件实现：

- 只处理数据文件逐条列出的 `(source_url, version)`，且必须是 `published`；
- 只降级为 `pending_parse`，**不删除任何行**——符合 M3「有引用只降级、优先不删」的要求，M3 接受「永远只降级」；
- 只对目标行临时 `DISABLE/ENABLE TRIGGER policy_version_guard_published`；由于 `ALTER TABLE` 在事务内持 ACCESS EXCLUSIVE 锁，其他会话被阻塞到提交，实际不会并发写入；
- 提交前后比对「除目标行外已发布行的 `xmin` 指纹」；
- 提交后另开连接对已发布行试 UPDATE，必须被 `CheckViolation` 拒绝。

**M3 要求补三项（P2/P3，执行前）：**

1. **目标行 `SELECT ... FOR UPDATE` + UPDATE rowcount=1 校验**：当前 select 与 update 之间若状态被改，UPDATE 命中 0 行仍会 `retired += 1` 并报「已降级 1 个」。请锁行并校验命中数，否则整批回滚。
2. **引用检查补全**：当前只查 `assessment` / `policy_review_event`；M3 第三轮要求还包括「发现候选或其他表引用」。请列出并检查所有引用表（至少 `discovery_candidate` 及父 `policy`），在回告里逐项给计数；有引用也只降级，但必须让执行者看到。
3. **快照补字段**：除 id/version/status/revision/xmin 外，补 `policy_source` 行数、父 `policy` id、`reviewed_at`/审核人（如保留审计需要）；回告中给出这些值。

另外：

- 脚本头部「只在该语句范围内禁用」的表述不精确——实际是事务内 `DISABLE`→`UPDATE`→`ENABLE`，靠 ACCESS EXCLUSIVE 锁保证并发安全；建议在注释里写清，避免执行者误以为触发器只对一条 UPDATE 失效。
- 守卫恢复验证若库里已无其他已发布行会跳过；当前有 7 条，可用；建议在跳过时明确报「无法验证」而不是静默成功。

**执行前提不变**：M2 与项目负责人明确同意、维护窗口、快照留档、回告前后 12 行/8 已发布/4 未发布、目标行 id/状态、指纹、守卫验证结果。M3 不单独授权，`2a26e53` 未执行的事实正确。

## 5. 合并状态

1. PR 仍与 `main`（`a1ec7e1`，PR #74/#75）冲突：`infra/README.md`、`infra/deploy/README.md`、`infra/deploy/migrate.sh`、`services/api/tests/test_migrate_script.py`；`2a26e53` 未解决冲突。
2. head `2a26e53` 的 check-runs 为 0；重推后需重跑三项 CI。
3. M2 明确「暂不建议合并」，两条 P1 未修；M3 同意。
4. M1 的 `m1/e3-combined-verify` 1431 passed 是 M1 侧组合证据，不能替代 PR head 的 CI 或 M2 复审。

## 6. VIS-06

B1a 已完成；本轮仍不产生「通过」项：

- B1b 读侧、M2 展示证据、M3 页面复核、F12 `published_at` 未完成；
- SMART/GREEN 未发布；SRDI 的有效期字段在 B 前仍错误，A 只是展示口径；
- `source_register.csv` 的 SMART/SRDI/GREEN 有效期仍等于窗口；它是冻结评测输入（sha256 含 `1d4c2b1e…`），本轮不改。

## 7. 本轮验证与未执行事项

**已做：**

- 读取 M2 `6880892267` 与 M1 `6880960415`，核对 `2a26e53` 的 5 个文件；
- 核对 SRDI 的 A 规则与 M3 第五轮 B 决定的差异；
- 核对 `retire-test-policy-version.py` 的六条条件映射与并发/引用边界；
- 核对 PR 仍 dirty、head CI 为 0。

**未做：**

- 未连库、未执行 `v1.0` 降级或 SRDI 修正；
- 未独立复核 M1 的测试、云端引用计数或 `xmin`；
- 未取得 M2/负责人对 `v1.0` 或 SRDI B 的同意；
- 未改 `infra/**`、`docs/IMPLEMENTATION_AND_INTERFACE.md`、`data/**`、契约、代码或评测输入。

## 8. 本次修改文件

- 新建：`docs/product/M3_DATE_SOURCE_ROUND6_1115.md`（本文件）。
- 更新：`docs/product/POLICY_SCOPE.md` VIS-06 节（加第六轮复核）。
- 更新：`docs/product/M3_DATE_SOURCE_ROUND5_1115.md`（补后续指向本文）。
- 更新：`docs/devlog/M3.md`。
- PR #69 复核评论：https://github.com/sgase233/huice/pull/69#issuecomment-6881056565
- **未修改**：`infra/**`、`docs/IMPLEMENTATION_AND_INTERFACE.md`、`contracts/**`、`apps/web/**`、`services/api/**`、`data/**`、冻结输入。
