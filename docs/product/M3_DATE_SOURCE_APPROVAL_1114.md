# M3 对 PR #69 `9d23483` 的最终复核与批准记录（2026-11-14）

> **性质：M3 对 `date-provenance-backfill.json` 9 个条目的业务批准记录，仅批准旁表 provenance 写入，不是 VIS-06 验收通过，不是三方签收。** 批准对象是 PR #69 head `9d23483` 的数据文件；`infra/**` 的状态改写与 `--apply` 由 M1 执行。M3 未执行迁移、未连库、未运行脚本；本文所依据的云端干跑输出是 M1 在 2026-11-14T07:36:50Z 评论中提供的证据。
>
> **基线：** PR #69 head `9d23483a9c07fbb41a9983b94e67bd9d06e1f961`（2026-11-14T07:36:50Z），17 个文件；M1 最新答复见 `issuecomment-6873923139`。上一轮 M3 复核见 [M3_DATE_SOURCE_REVIEW_1114.md](M3_DATE_SOURCE_REVIEW_1114.md)。
>
> **边界：** 批准只覆盖 9 个条目的状态、来源与定位；不批准 SMART/GREEN 两个新版本的发布，不把导入来源当作条款/材料的完整覆盖，不把云端干跑写成 M3 独立执行。

## 1. 云端干跑证据核对

M1 在 `9d23483` 上补建 SMART/GREEN 两个版本后，对云端库干跑输出 10 行绑定（9 个条目，其中 TAX-SME 有两个 `verified` 字段）。逐行与数据文件比对：

| 干跑行 | 数据文件字段 | 绑定对象 | M3 核对 |
| --- | --- | --- | --- |
| `entries[0]` 深工信规〔2025〕4号 `applicationWindow` | `attachment, ordinal: 1` | 附件1.docx（23 KB） | 与数据一致；**不是 ZIP 整包**，是包内附件1；导入文本第 283—297 行含两批网络 + 两批书面窗口 |
| `entries[1]` 深工信规〔2024〕12号 `applicationWindow` | `attachment, ordinal: 1` | `11845882.doc`（8 KB） | 与数据一致；导入文本第 77—79 行含 2024-12-21—2025-01-06 18 时 |
| `entries[2]` 深工信规〔2025〕8号 `applicationWindow` | `body` | `SZ-GRANT-SRDI-2025.txt` | 与数据一致；绑通知正文，不是实施细则 |
| `entries[3]` 深科创规〔2026〕1号 `effectiveFrom` | `body` | `SZ-GRANT-RD-SUBSIDY-2026.txt` | 与数据一致；`effectiveTo` 为 `derived`，无来源绑定，符合预期 |
| `entries[4]` 深科技创新规〔2021〕5号 `effectiveFrom` | `body` | `SZ-GRANT-HIGHTECH-2021.txt` | 与数据一致；`effectiveTo` 为 `derived`，无来源绑定，符合预期 |
| `entries[5]` 财政部 税务总局公告2023年第7号 `effectiveFrom` | `body` | `CN-TAX-RD-2023-007.txt` | 与数据一致；`effectiveTo` 为 `unknown`，无来源绑定 |
| `entries[6]` 财政部 税务总局公告2023年第12号 `effectiveFrom` | `body` | `CN-TAX-SME-2023-012.txt` | 与数据一致；locator 已改为「公告第一、二条」 |
| `entries[6]` 同公告 `effectiveTo` | `body` | 同上 | 与数据一致；locator 已改为「公告第一、二、三条」 |
| `entries[7]` 国科发火〔2008〕172号 `effectiveFrom` | `body` | `国科发火〔2008〕172号.txt` | 与数据一致；`effectiveTo` 为 `derived`，无来源绑定 |
| `entries[8]` 国科发火〔2016〕32号 `effectiveFrom` | `body` | `国科发火〔2016〕32号.txt` | 与数据一致；`effectiveTo` 为 `unknown`，无来源绑定 |

**结论：10 行绑定逐条对得上，没有串版本；SMART 绑到包内附件1.docx，不是整包 ZIP；GREEN 绑到 `.doc`；SRDI 绑到通知正文；税收/办法条目绑到各自正文。** 上一轮 §6.1 的批准前置已清除。

## 2. 对 M1 三个确认项的答复

### 2.1 绑定行

**确认。** 见 §1。`entries[0]` 的「附件第 1 份」是包内附件1.docx，与数据 note 的「若整包导入则指向 ZIP」风险说明不矛盾——本次是逐份导入，绑定正确。若将来改成整包导入，必须同时改数据文件序号或导入方式，不能沿用本记录。

### 2.2 来源集合

**接受，但仅限 B1a 日期来源。**：

- SMART 只导入附件1，附件2（1.2 MB DOCX）与附件3（扫描 PDF，无文本层）未导入；GREEN 只有一份 `.doc` 附件；两份正文都很薄，不含窗口。
- 这对「窗口/日期出处」足够：SMART 窗口在附件1 第 283—297 行，GREEN 在附件第 77—79 行，均已核对。
- **不足以作为条款标注、材料要求或完整来源覆盖的依据**：不得据此说 SMART 的附件 2/3 已取得或已核对，也不得把这两个版本用于条件发布。后续补 `criteria` 前仍需补齐来源并复核。

### 2.3 草稿还是发布

**保持 `pending_parse` 草稿，本轮不发布。**：

- B1a 的旁表 provenance 挂在版本 id 上，版本是否发布不影响回填；
- `criteria_review.csv` 没有这两项，写空条款再发布等于伪造审核通过，M3 不接受；
- 发布前还有 §3 的 P1/P2 要处理（有效期语义、合并窗口、测试版本计数）。

## 3. 本轮新发现

### P1（发布前必修，不阻断本次旁表回填）：导入把申报窗口写成了政策有效期

`policy-source-import.json` 对 SMART/GREEN 设置了：

- SMART：`effectiveFrom 2025-06-13`、`effectiveTo 2025-07-07`（等于申报窗口起止）；
- GREEN：`effectiveFrom 2024-12-21`、`effectiveTo 2025-01-06`（等于申报窗口起止）。

这正是 M3 一直要求的「窗口不能当政策有效期」。回填数据本身把它们标为 `unverified`，方向正确；但 `policy_version.effective_from/effective_to` 是版本行的正式字段，发布后会被 `resolve_validity` 用于 `policy_expired`。如果这两个版本以后直接发布，系统会在申报窗口关闭后把政策判为「已过有效期」——对规程/细则本身未必成立。

**要求：**

1. 本轮旁表回填可以照常 `--apply`，不因这条停下；
2. **发布这两个版本前**，M1 必须二选一并交 M3 复核：把版本行的 `effective_from/effective_to` 改为中性的占位值（例如 `effective_from = published_at`、`effective_to = null`，并保持 provenance 为 `unverified`/`unknown`），或让 `resolve_validity`/时间线消费旁表 provenance，跳过 `unverified` 的有效期；
3. 在修复前，页面与接口不得把这两个版本的有效期显示为已核实，也不得用它们计算 `policy_expired`。

### P2（展示前必修）：SMART 版本行的 `application_window` 是合并区间

导入把 SMART 写成单一区间 `2025-06-13 00:00` 至 `2025-07-07 18:00`。原文实际是两批网络填报 + 两批书面材料：

- 网络第一批 06-13—06-30 18 时；第二批 06-20—07-07 18 时；
- 书面第一批 06-23—07-04；第二批 06-30—07-11（工作时间）。

版本行的单区间既压平了批次，也漏掉书面材料到 07-11 的窗口。**时间线/详情页必须读取 provenance 的 note，展示四段窗口与办理方式；不得只画版本行的合并区间，也不得把 07-07 当作全部受理截止。** GREEN 只有一段网络窗口，注记「来源附件，非正文」即可。

### P2（VIS-06 计数）：云端 3 个测试版本不应计入覆盖分母

云端 `policy_version` 有 12 行：9 个与 `source_register.csv` 对应的版本（7 个原有 + 2 个新建）+ 3 个 `v1.0/v2.0/v3.0`（`post_1.html` 测试数据）。干跑输出「带来源的版本 0/12」把测试数据算进了分母。

**要求：**

- VIS-06 的覆盖分母按 `source_register.csv` 的 9 个版本计，不能报成 12；
- 3 个测试版本不得出现在政策列表、时间线或演示数据中；若技术上无法排除，必须在页面/验收记录中明确标注「测试数据，不计入覆盖」；
- `--apply` 后正确口径是 **9/9**（9 个登记版本均有 provenance）；如仍按全表计数，应写 **9/12（其中 3 个为测试版本，已排除）**，不能只写 9/12。

### P3（数据卫生，请确认）：导入新建了 `policy` 行

`policy-source-import.json` 没有 `policyId`，`create_policy_version` 会为每条新建一个 `policy` UUID。SMART/GREEN 原先没有版本，预期会新建政策行；请确认云端没有因此产生重复的 `policy` 行（同标题、无版本的空壳），并在回执中说明。

### P3（证据口径）：`011` 已执行的证据

干跑查询了 `policy_version_date_provenance`（「带来源的版本 0/12」来自该表），说明该表已存在；但 M3 未独立查库。请回告 `schema_migration` 的 11 行记录，并把 PR 正文「云端 RDS 还没执行 011」的旧表述更新为实际状态。

## 4. 批准决定

**M3 批准 `date-provenance-backfill.json` 的 9 个条目写入旁表。** 批准范围仅限：

- 9 个条目的状态、`source`、`locator`、`note` 与 M3 的原文记录一致；
- 10 行 `sourceId` 绑定与 §1 核对结果一致；
- TAX-SME locator 已按 M3 的 P3 修正；
- `published_at` 不承载来源的说明已写入 `sourceNote` 与 README。

**不批准**：SMART/GREEN 两个新版本的发布、把导入来源当作完整来源覆盖、把旁表回填当作 VIS-06 验收通过。

### 4.1 `reviewEvidence`（由 M1 在 PR 分支写入）

```json
"reviewStatus": "approved-by-m3",
"reviewEvidence": {
  "reviewedBy": "wlj80176-creator",
  "reviewedAt": "2026-11-14",
  "reference": "<M3 最终批准评论 URL>"
}
```

- `reference` 用 M3 在 PR #69 的最终批准评论链接（即 M1 2026-11-14T07:36:50Z 答复后 M3 发布的批准评论）；不能只写 PR 主页面。
- `reviewedAt` 以批准评论日期为准；若跨日再改。
- M3 不改 `infra/**`；由 M1 填写后执行 `--apply`，并把「改动行数、9/9 或 9/12 口径、无已发布行被触碰」回告。

## 5. 下一步顺序

1. M1 在 PR 分支写 `reviewStatus` + `reviewEvidence`（M3 不在本分支改 `infra/**`）；
2. M1 执行 `--apply`，回告：写入行数、带来源版本计数、`policy_version` 已发布行 `xmin` 不变、退出码 0；
3. M1 回告 `schema_migration` 11 行与 3 个测试版本的处理；
4. 发布 SMART/GREEN 前，先处理 §3 的 P1/P2，并交 M3 复核；
5. VIS-06 验收仍待 M2 展示证据 + M1 同版环境证据 + M3 逐项复核，当前不产生「通过」项。

## 6. 本轮验证与未执行事项

**已做：**

- 逐行核对 M1 提供的 10 行干跑绑定与数据文件的 `source`/`locator`/状态；
- 核对导入的 SMART 附件1 文本（第 283—297 行：两批网络 + 两批书面）与 GREEN 附件文本（第 77—79 行：2024-12-21—2025-01-06 18 时）；
- 核对 `policy-source-import.json` 的 `effectiveFrom/To`、`applicationWindow`、来源顺序与 `pending_parse` 状态；
- 核对 `import-policy-sources.py` 的写入路径与 `create_policy_version` 对 `effective_from`（NOT NULL）、`application_window`、`review_status` 的处理；
- 核对数据文件的 TAX-SME locator 与 F12 说明已更新。

**未做：**

- 未执行 `011`、未连库、未运行导入脚本或回填脚本、未 `--apply`；
- 未重新下载官方原文、未浏览器核验、未双人复核/仲裁；
- 未改 `infra/**`、`data/**`、契约、代码或评测输入；
- 未把两个新版本标为可发布，未把旁表回填写成 VIS-06 验收通过或三方签收。

## 7. 本次修改文件

- 新建：`docs/product/M3_DATE_SOURCE_APPROVAL_1114.md`（本文件）。
- 更新：`docs/product/POLICY_SCOPE.md` VIS-06 节（加批准记录与发布前条件）。
- 更新：`docs/devlog/M3.md`（记录本次实际工作）。
- **未修改**：`infra/**`、`contracts/**`、`apps/web/**`、`services/api/**`、`data/**`、冻结输入。
