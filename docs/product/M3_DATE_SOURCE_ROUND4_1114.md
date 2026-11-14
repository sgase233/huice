# M3 对 PR #69 `29dfe23` 的第四轮复核与两项关闭（2026-11-14）

> **性质：M3 对第三轮决定执行结果的业务复核记录。** 本文关闭第三轮 §2（草稿有效期修正）与 §3（`effectiveTo` 状态一致性 + 受控 `--overwrite`），并确认 B1b 版本号。**不是 VIS-06 验收通过，不是三方签收，不批准 SMART/GREEN 发布，不批准 `v1.0` 清理。** M3 未连库、未执行迁移或脚本；执行结果依据 M1 在 PR #69 的回告。
>
> **基线：** PR #69 head `29dfe233122c4e6c88a4af3d79e66b5d0591a8b8`（2026-11-14T15:36:39Z），8 个提交、20 个文件；M1 回告 2026-11-14T15:36:55Z（`issuecomment-6879866711`）与 2026-11-14T15:42:09Z（`issuecomment-6879952496`）。GitHub API 显示 PR **open、`mergeable_state=clean`**；head 三项 CI 均 `success`（CSV 15:36:55Z、Frontend 15:37:02Z、Backend and contract 15:38:44Z）。
>
> **边界：** 本文只核对业务语义、数据文件与回告一致性；`infra/**` 的代码、迁移与执行由 M1 负责。M3 不把云端回告写成 M3 独立执行的证据，不把 B1a 关闭写成 VIS-06 通过或三方签收。

## 1. 结论先行

| 项 | 本轮结论 | 状态 |
| --- | --- | --- |
| 第三轮 §2 草稿有效期修正 | 两行均改为 `effective_from = published_at`、`effective_to = NULL`；`revision` 已换值；已发布行 `xmin` 指纹不变；退出码 0 | **关闭** |
| 第三轮 §3 `effectiveTo` 状态 + `--overwrite` | 数据文件 SMART/GREEN 的 `effectiveTo` 为 `unknown`；GREEN note 已改；冲突集合恰好两条；旁表仍 9 行、其余 7 条未被覆盖 | **关闭** |
| 第三轮 §5 B1b 版本号 | 按 M2 在 PR #71 的复审结论记为 `1.3.0-rc.1`；M3 文档同步 | **关闭** |
| 第三轮 §4 `v1.0` 清理（B） | 仍待 M2 与项目负责人明确同意；M3 不代替、M1 未执行 | 保持待办 |
| VIS-06 | B1b 读侧、M2 展示证据、M3 页面复核、F12 `published_at` 均未完成 | **仍未验收** |
| 新发现 | `SRDI` 与 `source_register.csv` 仍把申报窗口登记为政策有效期，需在 VIS-06 展示前处理 | 新增待办 |

## 2. 第三轮 §2 草稿有效期修正：核对通过

M1 在 15:42:09Z 回告的执行结果：

| 版本 | `review_status` | `effective_from` | `effective_to` | `revision` |
| --- | --- | --- | --- | --- |
| 深工信规〔2025〕4号（SMART） | `pending_parse` | 2025-05-28（原 2025-06-13） | `NULL`（原 2025-07-07） | `264abcb3-eadd-422d-95bc-f51c57735b49` |
| 深工信规〔2024〕12号（GREEN） | `pending_parse` | 2024-11-30（原 2024-12-21） | `NULL`（原 2025-01-06） | `f9086a26-fdd9-4cdd-ab6c-ff5aac2a68aa` |

逐项核对：

| 第三轮条件 | M1 回告 / 数据核对 | M3 判定 |
| --- | --- | --- |
| `revision`/ETag 同步换值 | 两行 `revision` 均为新 UUID；脚本 UPDATE 增加 `revision = gen_random_uuid()` | 通过 |
| UPDATE 命中行数 | 脚本取 `cursor.rowcount`，不等于 1 回滚并退出 3；M1 报「每条命中 1 行」 | 通过（脚本约束层面） |
| 两行 `review_status` | 均仍为 `pending_parse` | 通过 |
| `effective_from = published_at` | 2025-05-28 / 2024-11-30，与 `policy-source-import.json` 的 `publishedAt` 一致 | 通过 |
| 已发布行 `xmin` | 8 行，`md5(...) = 0f72239df6b6c2fc853a02b2857c4e92`，与执行前一致 | 通过（依据 M1 回告） |
| 退出码 | 0 | 通过 |

**结论：第三轮 §2 关闭。** 两行仍是草稿，修正后仍不得发布，直到 §3 与 VIS-06 展示条件满足。

## 3. 第三轮 §3 `effectiveTo` 状态与受控覆盖：核对通过

`29dfe23` 对 `date-provenance-backfill.json` 的实际改动只有 SMART/GREEN 两条：

| 版本 | `effectiveFrom` | `effectiveTo` | `applicationWindow` | GREEN note |
| --- | --- | --- | --- | --- |
| 深工信规〔2025〕4号 | `unverified` | **`unknown`** | `verified`（附件1） | 未改（原文已写「该窗口不是政策有效期」） |
| 深工信规〔2024〕12号 | `unverified` | **`unknown`** | `verified`（`.doc` 附件） | 已删「登记值等于申报窗口」，改为「版本行用 `published_at` / null 占位，provenance 仍为 unverified/unknown；窗口不是有效期」 |

- 与 M3 第三轮要求一致：置空后没有登记值 → `unknown`；`effectiveFrom` 保持 `unverified`（`published_at` 是占位，不是已核实起点）。
- 其余 7 条的 `source`/`locator`/状态未改；`reviewStatus: approved-by-m3` 与 `reviewEvidence` 三字段保持不变。
- M1 回告冲突集合**恰好是 SMART/GREEN 两条**，未出现第三条；旁表仍 9 行，已发布 8 行、`xmin` 指纹不变，其余 7 条未被覆盖。

**结论：第三轮 §3 关闭。** 本次 `--overwrite` 是对已批准数据的受控修正，不是新的批准，也不扩大批准范围。

## 4. `29dfe23` 代码与测试复核（M3 侧重）

M3 核对的是脚本行为是否守住第三轮条件，不做完整代码审查：

- **ETag**：UPDATE 同时写 `revision = gen_random_uuid()`；测试 `test_it_bumps_the_revision_so_stale_etags_cannot_undo_it` 断言执行前后 `revision` 不同。方向正确。
- **rowcount**：`cursor.rowcount != 1` 时回滚并返回 3；`WHERE review_status <> 'published'` 在 select 与 update 之间状态变化时会把命中数降为 0，从而回滚。
- **已发布行不可变**：写前写后比对 `(id, xmin)` 的 md5；不一致回滚。
- **数据文件测试**：`test_shipped_entries_never_treat_window_as_validity` 已把 SMART/GREEN 的 `effectiveTo` 断言改为 `unknown`，并断言 GREEN note 不再含「登记值等于申报窗口」。
- CI 三项在 head 上均为 `success`。

### 4.1 非阻断的文档/健壮性问题（P3）

1. **脚本 docstring 未随 `revision` 改动更新**：仍写「只改 `effective_from` / `effective_to` / `updated_at`」，实际还改了 `revision`。建议补一句，避免审计时误读改动范围。
2. **脚本不是幂等的，但 docstring 声称「含已是目标值、无需改动」**：代码没有比较当前值，重复执行会再次 UPDATE、再次 bump `revision`，并再次打印「已改写 2 行」。本次只执行一次，不影响已回告结果；但若日后重跑，回告的「已改写」不能理解为内容发生变化。建议加 `IS DISTINCT FROM` 条件或在应用前跳过已达目标值的行，并同步 docstring。
3. **README/PR 正文有过期表述**：README §2.2 仍写「云端那两行草稿要先修正」；PR 正文「尚未完成」仍写「云端这两行仍是旧的窗口日期」「F4 需 M1/M3 确认」「F13 需干跑确认」「F14 形式待定」。这些在 15:42 执行后已不成立（F4 的过渡口径 M3 已在 [M3_DATE_SOURCE_REVIEW_1114.md](M3_DATE_SOURCE_REVIEW_1114.md) §5 决定，F13/F14 已由 `9d23483`/`82090a8` 关闭）。合并前请更新为实际状态，避免审计记录与执行结果矛盾。
4. **修改后的重新干跑输出未回告**：M1 在 15:36 说会「重新干跑后再 `--apply`」。执行回告只给了 `--apply` 输出；请补一句干跑退出码/输出，或说明直接执行的理由。
5. **`published_at` 为空时无保护**：脚本把 `effective_from` 直接设为 `published_at`；这两行已验证非空，但脚本没有 `published_at IS NOT NULL` 前置校验。P4，可顺手补。

## 5. B1b 契约版本号：按 `1.3.0-rc.1` 记录

M2 在 PR #71 的复审（2026-11-13T15:53:04Z）写明：

> E2 `1.2.0-rc.1`、日期来源 B1b `1.3.0-rc.1` 的正式契约切换安排。

M1 在 PR #69 的 15:36:55Z 回告中引用同一结论，并说明 `contracts/README.md` 与实现文档已按此写。M3 据此：

- `M3_DATE_SOURCE_HANDOFF_1113.md` §1.4 与 `POLICY_SCOPE.md` VIS-06 节由 `1.2.0-rc.1` 改为 **`1.3.0-rc.1`**；
- 第三轮 §5 的「待定」关闭；
- 若 M2 后续另改版本号，重新打开并同步 M3 文档与前端类型生成计划。

M3 不改 `contracts/**`；正式契约与 `types.ts` 切换由 M1/M2 执行。

## 6. 仍未关闭的事项

### 6.1 `v1.0` 测试版本（第三轮 §4 方案 B）

M3 仍选 B，但**不单独授权**。当前状态：

- `v1.0` 是 `post_1.html` 测试数据，`published`，会出现在政策列表/时间线（M1 已更正此前「测试版本不会出现」的说法）；
- 执行必须取得 **M2 与项目负责人的明确同意**，在维护窗口进行，先查引用（有引用只降级 `pending_parse` 不删除），临时绕过守卫后立即恢复并验证「已发布行 UPDATE 被拒」，回告前后 12 行 / 8 已发布 / 4 未发布、该行 id/状态、`xmin` 指纹与守卫验证结果；
- 3 个遗留空壳 `policy` 行本轮仍不清理。

演示前若 B 无法排期，退而求其次的做法是 M2 在政策列表/演示数据中明确排除测试版本并留下证据；这不能替代 B，也不能写成「测试数据已清理」。

### 6.2 VIS-06

B1a 已完成，但 VIS-06 仍不产生「通过」项：

1. B1b 读侧（`dateProvenance` 进 `PolicySummary`/`PolicyDetail`/`PolicyDraft`）未完成；
2. M2 页面展示证据未提供（时间线/详情必须读 provenance note：SMART 四段窗口、GREEN 附件窗口，不得用版本行合并区间）；
3. M3 逐项复核页面展示未执行；
4. F12：`published_at` 来源未承载，页面必须写明「发布日期来源未承载」；
5. 18:00 截止是否含当日仍未定；`derived` 终点须标「推算」。

### 6.3 新发现：`SRDI` 与 `source_register.csv` 仍把申报窗口当有效期

M1 本轮只修正了 SMART/GREEN 两行草稿。M3 核对登记表发现：

| 对象 | `effective_from/to` | `application_open/close` | 问题 |
| --- | --- | --- | --- |
| `SZ-GRANT-SRDI-2025`（深工信规〔2025〕8号） | 2025-10-15 — 2025-11-10 | 2025-10-15 — 2025-11-10 | 与 SMART/GREEN 同型：有效期字段等于申报窗口；该版本已 `published`，`resolve_validity` 可能在 2025-11-10 后报 `policy_expired` |
| `SZ-GRANT-SMART-2025` | 2025-06-13 — 2025-07-07 | 2025-06-13 — 2025-07-07 | 登记表仍与云端修正后的 `publishedAt`/`NULL` 不一致 |
| `SZ-GRANT-GREEN-2025` | 2024-12-21 — 2025-01-06 | 2024-12-21 — 2025-01-06 | 同上 |

**M3 未查库**，不能确认云端 SRDI 版本行的实际值；请 M1 回告 SRDI `policy_version` 的 `effective_from/effective_to` 与来源（是否也等于窗口）。在确认前：

- 页面不得把 SRDI 的有效期显示为已核实，也不得用窗口关闭推导「政策已过期」；
- 登记表是冻结评测输入（`EVALUATION_FREEZE.md` 记录其 sha256 含 `1d4c2b1e…`），**本轮不改 `source_register.csv`**；若需修正 SMART/GREEN/SRDI 的有效期语义，须与评测快照重出、`inputs`/`verify` 更新一起安排，不能只改一处。

### 6.4 PR #69 合并

就 M3 负责的业务语义与数据范围，PR #69 在 head `29dfe23` 上**没有剩余的 M3 阻断项**：§2/§3 已关闭，数据文件与回告一致，CI 绿。但：

- 合并是代码/迁移/契约流程，由 M1/M2 决定；M3 不代替代码审查；
- 合并前请更新 §4.1 第 3 条的 PR 正文与 README 过期表述；
- 合并 PR #69 不等于 SMART/GREEN 可以发布，也不等于 VIS-06 验收或三方签收。

## 7. 本轮验证与未执行事项

**已做：**

- 经 GitHub API 读取 PR #69 head `29dfe23` 的提交、CI 状态、评论与数据文件；
- 核对 `date-provenance-backfill.json` 的 SMART/GREEN 状态与 note，确认只有这两条被改；
- 核对 `fix-draft-effective-dates.py` 的 `revision` bump、rowcount 校验、已发布行指纹与 published 拒绝；
- 核对 M1 的 §2/§3 执行回告与第三轮条件逐项对应；
- 核对 PR #71 中 M2 的版本号结论；
- 核对 `source_register.csv` 的 SMART/SRDI/GREEN 有效期与窗口字段。

**未做：**

- 未连库、未执行 `011`/导入/回填/草稿修正/`--overwrite`/`v1.0` 清理；
- 未独立复核云端 `xmin`、`revision` 查询结果，未重跑后端测试或 CI；
- 未取得 M2/负责人对 B 的同意，未替代他们决定；
- 未改 `infra/**`、`data/**`、契约、代码或评测输入；
- 未把本轮写成 VIS-06 验收通过或三方签收。

## 8. 本次修改文件

- 新建：`docs/product/M3_DATE_SOURCE_ROUND4_1114.md`（本文件）。
- 更新：`docs/product/POLICY_SCOPE.md` VIS-06 节（加第四轮复核与剩余条件）。
- 更新：`docs/product/M3_DATE_SOURCE_HANDOFF_1113.md` §1.4（B1b 版本号改 `1.3.0-rc.1`）。
- 更新：`docs/product/M3_DATE_SOURCE_DECISIONS_1114.md` §5（补版本号结论与指向本文）。
- 更新：`docs/devlog/M3.md`（记录本次实际工作）。
- PR #69 复核评论：待发布后回填链接。
- **未修改**：`infra/**`、`contracts/**`、`apps/web/**`、`services/api/**`、`data/**`、冻结输入。
