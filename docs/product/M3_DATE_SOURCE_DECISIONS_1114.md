# M3 对 PR #69 `35caa43` 的第三轮决定（2026-11-14）

> **性质：M3 对草稿有效期修正、`v1.0` 测试版本处置与 provenance 状态一致性的业务决定，不是 VIS-06 验收通过，不是三方签收。** 依据是 M1 在 2026-11-14T15:17:10Z / 15:17:50Z / 15:18:30Z 的三条回告与 head `35caa4349ddd8677a8fa3422b25cd338db7af7af`。M3 未连库、未执行脚本；执行结果由 M1 回告。
>
> **基线：** B1a 已收口（见 [M3_DATE_SOURCE_CLOSEOUT_1114.md](M3_DATE_SOURCE_CLOSEOUT_1114.md)）；本轮只处理收口后的三项决定与一处版本号不一致。

## 1. 12 行状态勾稽：确认

M1 补齐的明细：

| 归类 | 行数 | 具体 |
| --- | --- | --- |
| 登记版本 · 已发布 | 7 | 国科发火〔2008〕172号、〔2016〕32号、深工信规〔2025〕8号、深科创规〔2026〕1号、深科技创新规〔2021〕5号、财政部 税务总局公告2023年第7号、第12号 |
| 登记版本 · 草稿 | 2 | 深工信规〔2025〕4号（SMART）、深工信规〔2024〕12号（GREEN），均 `pending_parse`、有来源 |
| 测试版本 · 已发布 | 1 | `v1.0` |
| 测试版本 · 草稿 | 2 | `v2.0`、`v3.0`（`pending_parse`） |
| 合计 | 12 | 已发布 8 / 未发布 4 |

**勾稽成立**：8 = 7 个登记版本 + `v1.0`；未发布 4 = 2 个新草稿 + `v2.0`/`v3.0`。VIS-06 的分母仍按 9 个登记版本计（已覆盖 9/9），`v1.0` 不进入任何分母。M1 已更正「测试版本不会出现在列表」的说法：该句只对 `v2.0`/`v3.0` 成立，`v1.0` 已发布、会出现在政策列表。**这条勾稽关闭。**

## 2. 草稿有效期修正：同意执行 `--apply`，附四项条件

M1 的做法与 M3 在收口记录 §4.1 的要求一致：

- 按 `(source_url, version)` 精确定位两行，命中数 ≠ 1 就停；
- 任一行是 `published` 就整批拒绝（退出码 4），不依赖触发器兜底；
- 只改 `effective_from = published_at`、`effective_to = NULL`、`updated_at = now()`，`WHERE review_status <> 'published'`；
- 写前写后比对已发布行 `(id, xmin)` 的 md5，不一致回滚；
- 默认干跑；干跑输出两行「旧窗口日期 → 占位值」。

**M3 同意执行 `--apply`。条件：**

1. **`revision`/ETag 一致性**：脚本只改 `effective_from/effective_to/updated_at`，未提 `revision`。`policy_version.revision` 是后台草稿编辑的 `If-Match`/ETag 键；如果这两行草稿可能已被打开，更新后旧 ETag 可能覆盖这次修正。请先确认：需要同步 bump `revision` 就补上；不需要就在回告里说明理由（例如这两行从未被后台打开）。
2. **更新行数校验**：每条 `UPDATE` 的 rowcount 应为 1；请检查，避免状态在 select 与 update 之间变化时仍报「已改写 2 行」。
3. **回告**：两行的 `review_status`、`effective_from/to`、已发布行 `xmin` 指纹前后一致、退出码。
4. **执行前不放行发布**；这两行改完后仍不得发布，直到 §3 与 VIS-06 展示条件满足。

## 3. provenance 状态一致性：`effectiveTo` 应改 `unknown`，并受控覆盖

版本行把 `effective_to` 置空后，数据文件里 SMART/GREEN 的 `effectiveTo` 仍是 `unverified`。按 M3 的五值口径：

- `unverified` = 字段有登记值，但原文没有单列该字段，或登记值来自其他字段；
- `unknown` = 原文未给、未取得，或本次没有可核对依据。

**`effectiveTo` 置空后没有登记值，应改为 `unknown`；`effectiveFrom` 保持 `unverified`**（`published_at` 是占位值，不是已核实的有效期起点）。

同时：

- GREEN 的 note「原文未单列有效期，登记值等于申报窗口」在修正后不再成立，改为「版本行用 `published_at`/`null` 占位，provenance 仍为 `unverified`/`unknown`；窗口不是有效期」；
- SMART 的 note 没有这句，但应补一句「版本行不得把窗口日期当有效期」的现状说明（如已有等价表述可保留）；
- `sourceNote` 里「登记表里与窗口同值的有效期为 `unverified`」仍适用于 SRDI 等未改版本，可保留，但不要再暗示 SMART/GREEN 也是窗口值。

**执行方式**：旁表已写入 `unverified`，改数据文件后再次回填会命中「值不同 → 冲突（退出码 3）」。M3 **明确授权本次针对这两条的 `--overwrite`**（这是对已批准数据的受控修正，不是新的批准）；请先确认冲突集合**恰好是 SMART/GREEN 两条**——若还有别的条目冲突，立即停下回告，不要整批 `--overwrite`。请回告冲突/覆盖结果与两条最终 provenance。若你选择先删这两行旁表再回填，同样要回告删除/重写的前后值。

**范围**：只改 SMART/GREEN 两条的 `effectiveTo` 状态与 GREEN note；不改 `source`/`locator`、不改其余 7 条。M3 的批准记录仍适用于这次受控修正。

## 4. `v1.0` 测试版本：M3 选择 B（受控清理）

**M3 的业务意见：B。** 理由：

- A（不动）会让一条测试政策出现在政策列表/时间线，与「测试版本不得出现」冲突，VIS-06/演示不可接受；
- C（换干净库）会丢掉已完成的 9 条旁表与全部演示数据，代价不成比例；
- B 能保留审计痕迹并消除可见测试政策，是当前最小代价。

**但 B 是例外操作，M3 不单独授权。条件：**

1. **必须同时取得 M2 与项目负责人的明确同意**；M3 不代替他们同意，M1 不得单方面绕过 `policy_version_guard_published`；
2. 在维护窗口执行；执行前保存该行快照（id、version、review_status、source 行数、xmin）；
3. 先查引用：若 `v1.0` 被评估 run、发现候选或其他表引用，**只降级为 `pending_parse`，不删除**；确认无引用后才可删除；不得删除任何带版本的登记政策行；
4. 只对该行临时禁用/绕过触发器；操作后立即恢复，并用一次「对已发布行的 UPDATE 应被拒绝」的检查证明守卫重新生效；
5. 回告：操作前后 12 行/8 已发布/4 未发布的行数、该行 id 与状态、已发布行 `xmin` 指纹、守卫验证结果；
6. 在 PR/记录中写明为什么绕守卫（该行是 `post_1.html` 测试数据，非登记政策）、谁批准、何时执行，作为一次性例外，不成为先例。

## 5. B1b 契约版本号：待确认

M1 在 2026-11-14T15:18:30Z 写「B1b 随 `1.3.0-rc.1` 一次进场」，但既有记录（[M3_DATE_SOURCE_HANDOFF_1113.md](M3_DATE_SOURCE_HANDOFF_1113.md) §1.4、[POLICY_SCOPE.md](POLICY_SCOPE.md) VIS-06 节）写的是 `1.2.0-rc.1`。两者不一致。

**M3 不自行改版本号。** 请 M1/M2 在 E2 的 `1.2.0-rc.1` 窗口确定后，明确 B1b 的进场版本，并同步 `contracts/README.md`、M3 文档与前端类型生成计划。在确认前，M3 记录为「B1b 读侧字段未进正式契约；版本号待定（M1 最新说法 `1.3.0-rc.1`，旧记录 `1.2.0-rc.1`）」。

## 6. VIS-06 状态

B1a 已完成；本轮三项决定不改变 VIS-06 的验收状态：

- B1b 读侧（`dateProvenance` 进 `PolicySummary`/`PolicyDetail`/`PolicyDraft`）未完成；
- M2 页面展示证据未提供；
- M3 逐项复核页面展示未执行；
- `published_at` 来源未承载（F12）仍是完整性缺口；
- `v1.0` 清理与草稿修正完成前，不产生「通过」项。

## 7. 本轮验证与未执行事项

**已做：**

- 核对 M1 的 12 行明细，确认 8 = 7 + `v1.0`、4 = 2 + `v2.0`/`v3.0`；
- 核对 `fix-draft-effective-dates.py` 的定位、published 拒绝、UPDATE 条件、xmin 指纹与干跑输出；
- 核对 `draft-effective-dates.json` 的两条目标与 `effective_from=publishedAt`/`effective_to=NULL`；
- 核对数据文件 SMART/GREEN 的 `effectiveTo` 仍为 `unverified`，与置空后的版本行不一致；
- 核对 GREEN note 的「登记值等于申报窗口」在修正后不再成立；
- 核对 M1 的 B1b 版本号与既有记录不一致。

**未做：**

- 未连库、未执行草稿修正脚本、未 `--apply`、未执行 `v1.0` 清理；
- 未取得 M2/负责人对 B 的同意，未替代他们决定；
- 未改 `infra/**`、`data/**`、契约、代码或评测输入；
- 未把本轮决定写成 VIS-06 验收通过或三方签收。

## 8. 本次修改文件

- 新建：`docs/product/M3_DATE_SOURCE_DECISIONS_1114.md`（本文件）。
- PR #69 决定评论：https://github.com/sgase233/huice/pull/69#issuecomment-6879655509
- 更新：`docs/product/POLICY_SCOPE.md` VIS-06 节（加第三轮决定）。
- 更新：`docs/devlog/M3.md`（记录本次实际工作）。
- **未修改**：`infra/**`、`contracts/**`、`apps/web/**`、`services/api/**`、`data/**`、冻结输入。
