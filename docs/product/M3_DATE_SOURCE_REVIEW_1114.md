# M3 对 PR #69 `82090a8` 的日期来源复核（2026-11-14）

> **性质：M3 业务复核记录，不是 VIS-06 验收证据，不是三方签收，也不是对 `--apply` 的批准。** 本文复核对象是 PR #69 head `82090a8` 的 `infra/deploy/date-provenance-backfill.json`（9 个政策版本）、`backfill-date-provenance.py` 的批准证据校验与 `infra/deploy/README.md` §2.2 的版本粒度口径。M3 未执行 `011` 迁移、未连接数据库、未运行回填脚本、未对云端库干跑；`reviewStatus` 仍为 `draft-pending-m3`。
>
> **基线：** PR #69 head `82090a8`（2026-11-14T04:49:30Z 提交，`mergeable_state=clean`），作者 Hya-555；最新答复为 2026-11-14T04:51:10Z 评论（`issuecomment-6871983894`）。上一轮 M3 复核对象是 `a82caa5`，见 [M3_DATE_SOURCE_HANDOFF_1113.md](M3_DATE_SOURCE_HANDOFF_1113.md) §4。
>
> **依据：** 本文件引用的原文均来自 `data/policies/sources/` 已归档正文与 [POLICY_SCOPE.md](POLICY_SCOPE.md) 2026-11-10/11-12/11-13 核查、[POLICY_SOURCE_ATTACHMENTS_1113.md](POLICY_SOURCE_ATTACHMENTS_1113.md) 附件读取记录、`data/policies/source_register.csv` 的 9 个版本行。**本轮没有重新下载全部官方原文，也没有做浏览器核验、双人复核或数据库实测。**
>
> **边界：** 只复核业务语义、原文定位、状态口径与数据文件一致性；不改 `infra/**`（M1 范围），不改 `reviewStatus`，不写 `reviewEvidence`。批准动作由 M3 本人确认后，交 M1 在其分支填写。

## 1. 本轮结论摘要

| 项 | 结论 |
| --- | --- |
| 数据条数 | 9 条，按政策版本计：8 个 `policy_id`，`CN-TAX-GXJS` 含 2008/2016 两版。与 `source_register.csv` 的 9 个版本行一致（F1 已修） |
| 业务语义 | 9 条的字段状态、状态取值和 note 方向与 M3 的原文记录一致；未发现新的阻断性业务错误 |
| F1—F11 | 已在 `361733d` 修正并在 `82090a8` 保持：SRDI 已补、GXJS 窗口改 `not_applicable`、4 个版本补 `effectiveFrom`、SMART/GREEN locator 与 note 已改、GXJS basis 已补、计数口径已统一 |
| F13 | 结构上已处理：干跑会打印每条绑定到的 `policy_source` 行（文件名、绝对 ordinal、大小），压缩件另提示；**实际绑定结果仍待云端干跑** |
| F14 | 结构上已处理：`--apply` 必须同时满足批准值 + `reviewEvidence` 三字段，缺一返回 2；**M3 批准动作本身尚未发生** |
| F4 | M3 **接受** M1 在 README §2.2 写明的版本粒度口径，作为 B1a 的过渡约定；附展示条件，见 §5 |
| 是否可改 `approved-by-m3` | **否。** 按 README §2.2 的固定顺序，M3 批准在云端干跑之后；本轮没有干跑绑定行，无法确认 `sourceId` 实际指向，尤其 SMART 的 ZIP 附件。见 §6 |
| 对 VIS-06 的含义 | 回填数据仍只是时间线的技术前置；VIS-06/VIS-AT-04 的验收仍需 M2 展示证据、M1 同版环境证据与 M3 逐项复核，当前不产生「通过」项 |

## 2. 对 M1 2026-11-14T04:51Z 答复的核查

### 2.1 F14：批准证据从「一句话」变成「状态 + 三字段」

脚本现状（`82090a8`）：

- `APPLY_REVIEW_STATUSES = ("approved-by-m3",)`；
- `REVIEW_EVIDENCE_FIELDS = ("reviewedBy", "reviewedAt", "reference")`；
- `reviewEvidence` 必须是对象；缺字段、字段为空、`reviewedAt` 不是合法 `YYYY-MM-DD`、夹带未知字段，均判数据文件不合法（退出码 2）；
- 只有批准值、没有 `reviewEvidence` 时，`--apply` 同样返回 2 且一行不写；干跑不受影响。

**M3 意见：** 口径正确，与 M3 在上一轮 F14 中提出的「批准必须能追到人、时间、记录」一致。`reviewEvidence.reference` 允许 PR 评论链接、仓库文件或提交，满足可追溯要求。**本轮不填**，因为批准尚未发生；建议值见 §7，供最终批准时使用。

### 2.2 F13：干跑打印绑定行与压缩件提示

脚本现状：解析出处后逐条打印

```
entries[i] <文号> applicationWindow → 附件第 n 份（policy_source.ordinal=k）：<文件名>（<大小>）
```

绑定到 `.zip`/`.rar`/`.7z` 时另提示「整包算一份附件」与「包内文件」的语义差异。

**M3 意见：** 这是本轮最有价值的结构性改进——把「第几份附件」与数据库绝对 `ordinal` 的语义差、以及 SMART 附件包是整包还是逐份，交给干跑输出而不是靠猜。**但脚本打印正确不等于实际库里的行正确**；M3 必须看到云端库的这 9 行输出才能确认，尤其 SMART 的附件包。

### 2.3 F4：版本粒度口径

`infra/deploy/README.md` §2.2 现在写明：`version` 记**规程/细则文号**，`sourceUrl` 是载有窗口的**年度申报通知**页；窗口出处写通知正文或附件，note 点明「窗口来源不是版本文号对应的规程」；把年度通知单独建 `policy_version` 不在回填里做。

**M3 意见：** 接受这一口径作为 B1a 与 VIS-06 的过渡约定，理由与条件见 §5。M1 已把选择权交回 M3，本轮给出明确答复，不再悬置。

## 3. F1—F14 处置表（对象：`82090a8`）

| 编号 | 上一轮级别 | 现状 | M3 判定 |
| --- | --- | --- | --- |
| F1 缺 SRDI | P1 | 已补 `深工信规〔2025〕8号`：窗口 `verified`（正文「三、申报时间」）、有效期 `unverified`、note 区分初审节点与主窗口 | **已关闭**（业务语义）；绑定待干跑 |
| F2 GXJS 窗口误标 `unknown` | P1 | v1/v2 均改为 `not_applicable`，note 写「资格认定类政策，无申报窗口」 | **已关闭** |
| F3 4 个版本缺 `effectiveFrom` | P1 | RD-SUBSIDY 第十七条、HIGHTECH 第二十一条、GXJS v1 第二十条、GXJS v2 第二十三条，均为 `verified` + `body` + locator | **已关闭** |
| F4 版本—来源对应 | P1/P2 | README §2.2 写明口径；3 条 note 均点明「窗口来源不是版本文号对应的规程」 | **M3 接受**，附展示条件（§5） |
| F5 SMART locator/批次类别 | P2 | locator 改「附件1 七、受理时间（二）1/2」；note 补两批网络对应类别 | **已关闭** |
| F6 GREEN locator 依赖行号 | P2 | locator 改「附件 .doc 七、受理时间（二）1」；note 保留文件名与 `7d021ee0…` | **已关闭** |
| F7 RD-SUBSIDY note 缺 URL、空值语义 | P2 | note 补 S7a/S7b 两个 URL 与附件1 第七条；`applicationWindow` 改 `unknown` 并写明「本版本没有窗口依据」 | **已关闭**（`unknown` 语义可接受，见 §4.4） |
| F8 TAX-RD 缺 note | P2 | 已补「公告未写截止日；『长期实施』不在公告正文；外部依据未取得」 | **已关闭** |
| F9 GXJS v1 basis 不完整 | P2 | basis 已写「2016 版第二十三条自 2016-01-01 实施并明文废止 v1；推导终点 2015-12-31」 | **已关闭** |
| F10 GREEN/SRDI 缺「窗口不能当有效期」note | P3 | 两条均已补 | **已关闭** |
| F11 政策数/版本数混用 | P3 | `sourceNote` 与条目数统一为「9 个版本（8 个 policy_id）」 | **已关闭** |
| F12 `published_at` 不在 B1a 模型 | P3 | 仍未承载 | **不阻断 B1a；VIS-06 完整性缺口**（§6.3） |
| F13 ZIP 附件序号 | P2 | 干跑打印绑定行 + 压缩件提示；数据 note 已声明需干跑确认 | **结构已关闭；证据待干跑**（§6.1） |
| F14 批准证据形式 | P2 | 脚本强制 `reviewEvidence` 三字段；README §2.2 写明 | **结构已关闭；M3 签名待最终批准**（§6.2、§7） |

## 4. 逐条复核

> **读法：** 「业务复核」指状态取值、原文定位、note 与 M3 的原文记录是否一致；**不等于** `sourceId` 在库中的实际绑定已验证。绑定结果以云端干跑打印为准。

### 4.1 SMART、SRDI、GREEN（3 条资金政策）

| 版本 | 字段 | 数据状态 | M3 核对 | 结论 |
| --- | --- | --- | --- | --- |
| 深工信规〔2025〕4号（SMART） | `applicationWindow` | `verified` + 附件第 1 份 + locator「附件1 七、受理时间（二）1/2」 | 与 [POLICY_SOURCE_ATTACHMENTS_1113.md](POLICY_SOURCE_ATTACHMENTS_1113.md) §4 的两批网络 + 两批书面窗口一致；note 已写批次类别、北京时间、「不是有效期」、「不是规程正文」、ZIP 提示 | 业务复核通过；**绑定待干跑**（ZIP 还是 DOCX） |
| 同上 | `effectiveFrom/To` | `unverified` | 原文未单列有效期，登记值等于申报窗口，不能升级 | 业务复核通过 |
| 深工信规〔2025〕8号（SRDI） | `applicationWindow` | `verified` + `body` + locator「正文『三、申报时间』」 | `data/policies/sources/SZ-GRANT-SRDI-2025.txt` 第 39—40 行：2025-10-15—11-10；第 76 行初审 2025-12-10 前是推荐节点，note 已分开 | 业务复核通过；绑定待干跑 |
| 同上 | `effectiveFrom/To` | `unverified` | 原文未单列有效期，登记值等于窗口 | 业务复核通过 |
| 深工信规〔2024〕12号（GREEN） | `applicationWindow` | `verified` + 附件第 1 份 + locator「附件 .doc 七、受理时间（二）1」 | 附件读取记录 §5：2024-12-21—2025-01-06 18:00；note 已写附件名与哈希、「不是正文」「不是规程」「不是有效期」 | 业务复核通过；绑定待干跑 |
| 同上 | `effectiveFrom/To` | `unverified` | 原文未单列有效期，登记值等于窗口 | 业务复核通过 |

### 4.2 RD-SUBSIDY、HIGHTECH（2 条资金办法）

| 版本 | 字段 | 数据状态 | M3 核对 | 结论 |
| --- | --- | --- | --- | --- |
| 深科创规〔2026〕1号（RD-SUBSIDY） | `effectiveFrom` | `verified` + `body` + 第十七条「自印发之日起施行」 | `SZ-GRANT-RD-SUBSIDY-2026.txt` 第 79 行；落款 2026-02-24 | 业务复核通过 |
| 同上 | `effectiveTo` | `derived`，basis「有效期 5 年；按 2026-02-24 起算得 2031-02-23，满 5 年可能差一日」 | 同一第十七条；一日差异已如实写入，未伪装 `verified` | 业务复核通过；展示须标「推算」 |
| 同上 | `applicationWindow` | `unknown`，note 补 S7a/S7b 与「指南不属于本版本 source」 | 2026 年度窗口 2026-06-10—07-09 18:00 确在年度指南；该版本 `policy_source` 只有办法正文，不能绑定；`unknown` 表示「本版本没有窗口依据」，可接受 | 业务复核通过 |
| 深科技创新规〔2021〕5号（HIGHTECH） | `effectiveFrom` | `verified` + 第二十一条「自2021年9月8日起施行」 | `SZ-GRANT-HIGHTECH-2021.txt` 第二十一条 | 业务复核通过 |
| 同上 | `effectiveTo` | `derived`，basis「有效期 5 年；2026-09-08 / 满 5 年 2026-09-07 一日差异」 | 同一第二十一条；一日差异保留 | 业务复核通过；展示须标「推算」 |
| 同上 | `applicationWindow` | `unknown` | 年度指南未取得；不得从办法日期推算 | 业务复核通过 |

### 4.3 税收政策（3 条，4 个版本）

| 版本 | 字段 | 数据状态 | M3 核对 | 结论 |
| --- | --- | --- | --- | --- |
| 财政部 税务总局公告2023年第7号（TAX-RD） | `effectiveFrom` | `verified` + 公告第三条 | 归档正文第 56 行「本公告自2023年1月1日起执行」 | 业务复核通过 |
| 同上 | `effectiveTo` | `unknown` + note | 公告未写截止日；「长期实施」不在公告正文 | 业务复核通过 |
| 同上 | `applicationWindow` | `not_applicable` | 税收制度性政策，无申报窗口 | 业务复核通过 |
| 财政部 税务总局公告2023年第12号（TAX-SME） | `effectiveFrom` | `verified` + locator「公告第一条」 | 第一条确有 2023-01-01 起；但小型微利企业所得税在第三条，第一条是个体工商户个税条款，locator 不完整 | **业务复核通过，locator 建议补**（§6.4） |
| 同上 | `effectiveTo` | `verified` + locator「公告第一、二条」 | 第一、二条均写 2027-12-31；第三条（小型微利企业所得税）也写「延续执行至2027年12月31日」，locator 漏第三条 | **业务复核通过，locator 建议补**（§6.4） |
| 同上 | `applicationWindow` | `not_applicable` | 自行判别、申报享受 | 业务复核通过 |
| 国科发火〔2008〕172号（GXJS v1） | `effectiveFrom` | `verified` + 第二十条 | 归档正文第二十条「本办法自2008年1月1日起实施」 | 业务复核通过 |
| 同上 | `effectiveTo` | `derived`，basis 已补 2016 版第二十三条 | 2016 版第二十三条明文废止 v1；推导 2015-12-31 | 业务复核通过 |
| 同上 | `applicationWindow` | `not_applicable` | 资格认定类，无申报窗口 | 业务复核通过 |
| 国科发火〔2016〕32号（GXJS v2） | `effectiveFrom` | `verified` + 第二十三条 | 归档正文第二十三条「自2016年1月1日起实施」 | 业务复核通过 |
| 同上 | `effectiveTo` | `unknown` | 原文未写截止日 | 业务复核通过 |
| 同上 | `applicationWindow` | `not_applicable` | 资格认定类，无申报窗口 | 业务复核通过 |

### 4.4 关于 RD-SUBSIDY 的 `unknown`

M3 上一轮 F7 要求明确「空值 + `unverified`」的语义。`82090a8` 改为 `unknown` 并在 note 写明「本版本没有窗口依据，不是有值未核实」。M3 接受：

- `unverified` 的业务含义是「有登记值、但原文没有单列或未核实」；该版本 `applicationWindow` 没有登记值，用 `unverified` 会让读取层误以为有值。
- `not_applicable` 不成立：该政策有年度申报窗口，只是不在办法正文。
- `unknown` 最接近实际：在本版本来源内没有窗口依据；2026 年度窗口属年度指南，待补录为该版本附件后才能升级。

## 5. M3 对 F4 版本粒度的决定

**决定：接受 M1 在 `infra/deploy/README.md` §2.2 写明的过渡口径。**

- `version` 记规程/细则文号；`sourceUrl` 是载有窗口的年度申报通知页；窗口出处写通知正文或附件；note 必须点明「窗口来源不是版本文号对应的规程」。
- 不在 B1a 里把年度通知单独建成 `policy_version`。理由：会改变政策版本清单、完整度分母和前端版本数，超出回填范围，且当前样本（SMART/SRDI/GREEN）的窗口只对特定年度有效，单独版本化需要另定命名与生命周期规则。
- **条件（M2 展示 + VIS-06 验收）：**
  1. 时间线/详情页在窗口旁显示「来源：年度申报通知（正文/附件）」，不得显示成规程正文条款；
  2. 同一条时间线上「政策有效期」与「本年度申报窗口」分开画；SMART 四段窗口、GREEN 附件窗口不得压平成一条区间；
  3. 页面不得因 `verified` 就省略「来源是年度通知/附件」的标注；
  4. 若项目后续决定把年度通知独立版本化，重新打开本口径，并同步完整度分母与版本数。
- 本条只解决 B1a 的版本—来源对应，不构成版本模型定稿；`published_at` 仍不承载（F12）。

## 6. 仍未关闭的事项

### 6.1 云端干跑绑定行（阻断批准）

`011` 尚未在云端 RDS 执行，回填脚本也尚未对云端库干跑。M3 需要看到 `82090a8` 脚本对 9 条实际打印的绑定行，至少确认：

- 每条都能在对应 `policy_version` 下找到 `policy_source` 行，版本不串；
- SMART 的 `attachment ordinal=1` 绑定的是附件1 DOCX 还是 ZIP 整包；若绑到 ZIP，M3 需判断「窗口在包内附件1」是否可接受，或要求调整导入/序号；
- GREEN 绑定的是 `11845882.doc`（哈希 `7d021ee0…`），不是正文页；
- SRDI 绑定的是通知正文行，不是实施细则正文；
- 税收/办法条目绑定的是各自正文行。

**在拿到这些输出前，M3 不把 `reviewStatus` 改为 `approved-by-m3`。**

### 6.2 M3 批准动作（阻断写入）

批准值一旦写入，`--apply` 即可执行。按 README §2.2 的顺序，批准必须在干跑核对之后。M3 的最终批准记录应包含：reviewedBy、reviewedAt、reference（本轮或最终批准评论的 URL），并与数据文件 `reviewEvidence` 三字段一致。M3 不直接改 `infra/**`；由 M1 在 PR 分支按 M3 的批准记录填写。

### 6.3 F12：`published_at` 未承载（不阻断 B1a，阻断 VIS-06 完整性）

`published_at` 不在 B1a 五字段模型内。政策详情若显示「发布日期/成文日期」，当前无法区分 `verified`/`derived`/`unknown`。M3 要求：

- B1a 可以不承载，但页面/验收必须写明「发布日期来源未承载」；
- 若后续契约/旁表纳入 `published_at`，再补来源状态与 locator；
- 在此之前，不得把 `published_at` 当作 VIS-06 已覆盖。

### 6.4 TAX-SME 的 locator 精度（P3，不阻断）

- `effectiveFrom`：「公告第一条」只覆盖个体工商户个税条款；建议改为「公告第一、二条」，或另注「第三条为小型微利企业所得税，只写截止日」。
- `effectiveTo`：「公告第一、二条」漏了第三条；建议改为「公告第一、二、三条」。
- 日期值本身没有错，不影响状态；可在最终批准前由 M1 顺手修正，也可保留并记录为已知不精确项。

### 6.5 一日差异与 18:00 边界（不阻断 B1a，阻断 VIS-06 展示/契约）

- HIGHTECH/RD-SUBSIDY 的 `derived` 终点存在「满 5 年」一日差异；数据已如实标 `derived`，展示须标「推算」，不得给确定失效日。
- SMART/GREEN 的「18 时」未写时区；按北京时间。18:00:00 是否含在窗口内仍未定；B1a 的 jsonb 只存状态/出处/locator/note，不存窗口值，故不阻断回填；但 VIS-06 时间线展示与 `ApplicationWindow` 的 Timestamp 语义仍待 M1/M2 确认。

## 7. 最终批准时的 `reviewEvidence` 建议值

> **本轮不写入。** 等 §6.1 干跑绑定行核对通过后，由 M3 本人确认，再由 M1 在 PR 分支填写。

```json
"reviewStatus": "approved-by-m3",
"reviewEvidence": {
  "reviewedBy": "wlj80176-creator",
  "reviewedAt": "2026-11-14",
  "reference": "<M3 最终批准评论的 URL，例如 https://github.com/sgase233/huice/pull/69#issuecomment-...>"
}
```

- `reviewedBy` 以 M3 本人 GitHub 账号为准（M1 示例写的是 `wlj80176-creator`，M3 需确认）。
- `reviewedAt` 填**最终批准日**，不是本文日期；若跨日，以批准评论日期为准。
- `reference` 必须是能打开的复核记录：最终批准评论，或合并后的本文/交接文件链接。不能只写 PR 主页面。

## 8. 本轮验证与未执行事项

**已做：**

- 将 `82090a8` 的 9 条与 `source_register.csv` 的 9 个版本逐条对齐，确认条目数、版本、状态与 M3 原文记录一致。
- 对照 `data/policies/sources/` 归档正文核对：TAX-RD 第三条、TAX-SME 第一/二/三条、HIGHTECH 第二十一条、RD-SUBSIDY 第十七条、SRDI 正文「三、申报时间」与第 76 行初审节点、GXJS v1 第二十条、GXJS v2 第二十三条。
- 对照附件读取记录核对 SMART 两批网络 + 两批书面窗口、GREEN `.doc` 窗口与哈希。
- 核查 `82090a8` 脚本的 `reviewEvidence` 校验、干跑绑定打印、压缩件提示与退出码，以及 README §2.2 的版本粒度、序号语义和执行顺序。
- 确认 F1—F11 已修，F13/F14 结构已修；F12 仍在；新增 TAX-SME locator 精度一条 P3。

**未做：**

- 未执行 `011`、未连接数据库、未运行 `backfill-date-provenance.py`、未对云端库干跑、未 `--apply`。
- 未重新下载全部官方原文；未做浏览器核验、双人复核/仲裁、VIS-06 页面验收。
- 未改 `data/**`、`contracts/**`、`infra/**`、`apps/web/**`、`services/api/**`、`source_register.csv`、`criteria_review.csv`、`labels.csv` 或冻结输入。
- 未改 `reviewStatus`/`reviewEvidence`；未把任何字段写成 VIS-06 验收通过或三方签收。

## 9. 本次修改文件

- 新建：`docs/product/M3_DATE_SOURCE_REVIEW_1114.md`（本文件）。
- 更新：`docs/product/POLICY_SCOPE.md` VIS-06 节（加指向本文的复核记录）。
- 更新：`docs/devlog/M3.md`（记录本次实际工作）。
- **未修改**：`infra/**`、`contracts/**`、`apps/web/**`、`services/api/**`、`data/**`、冻结输入。
