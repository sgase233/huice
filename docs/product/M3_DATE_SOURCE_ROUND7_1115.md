# M3 对 PR #69 `3b1d198` 的第七轮复核与 SRDI 细则来源确认条件（2026-11-15）

> **性质：M3 对 M1 `3b1d198` 修复与 M2 新两条 P1 的业务复核记录，并给出 SRDI「补细则来源」的确认条件。** 不是 VIS-06 验收，不是三方签收；`v1.0` 与 SRDI 的 B 仍须 M2 与项目负责人同意。
>
> **基线：** PR #69 head `3b1d198e253ed712c2bd0c65cbce30ca85b4f32e`（15 个提交，base 已合入 `main` `a1ec7e1`）；GitHub 显示 `mergeable=clean`，head 三项 CI 均 `success`（CSV 17:10:31Z、Frontend 17:10:51Z、Backend 17:12:55Z）。M1 回告 `issuecomment-6881226314`（17:10:31Z）；M2 复审 `issuecomment-6881322385`（17:18:01Z，仍要求两条 P1）。
>
> **边界：** M3 未连库、未执行回填/降级/SRDI 修正；未独立复核 M1 的测试与云端结果。M3 不替代 M2 的代码复审，也不替代 M2/负责人对 B 的同意。

## 1. 结论先行

| 项 | 本轮结论 | 状态 |
| --- | --- | --- |
| M1 更正「M3 决定 A」 | README 与 `IMPLEMENTATION_AND_INTERFACE.md` 已改为「B 目标/A 临时口径」；接受「申报窗口已关闭」用词 | **关闭记录问题** |
| M2 P1：首次回填没有固定获批来源绑定 | **同意，合并前必修。** 首次插入也必须先有覆盖/执行计划，固定数据文件摘要、`expectedNew`、`sourceId`/`contentHash` | 阻断合并 |
| M2 P1：降级脚本指纹没有操作前基线 | **同意，合并前必修。** 操作前保存「排除全部目标行」的已发布行指纹，操作后同范围比较；不一致回滚 | 阻断合并 |
| SRDI 补细则来源 | **可以做，但须 M1 先贴具体行形状**；只新增 `policy_source`，不改已发布 `policy_version.source_url`；会改变来源计数与 SRDI provenance，需 M3 重新复核 | 待 M1 提案 |
| `v1.0` / SRDI B | 仍需 M2 与负责人同意；脚本加固后仍未执行 | 保持待办 |
| PR 合并 | 冲突已解、CI 绿；但 M2 两条 P1 未修、未复审 | **仍不同意合并** |
| VIS-06 | B1b、M2 展示证据、M3 页面复核、F12 未完成 | **仍未验收** |

## 2. 记录更正：A/B 口径

`3b1d198` 的 README 与 `docs/IMPLEMENTATION_AND_INTERFACE.md` 已写：

- **B 是目标处置**（按细则第二十四条，需 M2 与负责人同意，尚未执行）；
- **A 只是 B 未执行前的展示口径**；
- API 层 `validity` 仍是 `closed`、`staleReasons` 仍可能含 `policy_expired`，只做展示层映射时验收记录必须写明「API 层仍可能含，待 B 修复」。

这与 M3 第六轮要求一致。用词统一为「申报窗口已关闭」，**M3 接受**；记录问题关闭。

## 3. M2 两条 P1：M3 同意，且都关系批准有效性

### 3.1 首次回填也必须固定「写什么、绑到哪一行」

`3b1d198` 的覆盖路径已固定 `dataFileSha256`、`expectedNew`、`sourceId`/`contentHash`；但**旁表没有行时的普通 `--apply` 首次插入**仍按执行时的数据文件与附件顺序解析并直接插入。M3 核对干跑后、执行前若附件重排/替换或数据文件变化，首次插入仍可能写入未经 M3 复核的值或绑定。

**要求（合并前）：**

- 首次插入同样先生成执行计划，固定数据文件 sha256、解析后的完整 `expectedNew`、每个 `verified` 来源的 `sourceId`/`contentHash`；
- 正式写入前逐项比较，任一变化整批回滚；
- 补「首次插入前附件重排」「内容替换」「数据文件变化」用例，并覆盖正常首次插入路径。

M3 批准的是 `date-provenance-backfill.json` 的 9 条内容与干跑绑定；首次插入不固定这些，就等于把批准后的执行交给运行时状态。

### 3.2 降级脚本的指纹基线

`retire-test-policy-version.py` 在目标降级**之后**才分别计算全部已发布行指纹与排除目标行的指纹。此时目标已不是 `published`，两次查询通常是同一集合，无法证明其他已发布行在操作前后未变。

**要求（执行前）：**

- 在任何 UPDATE 之前，先算「排除全部目标行」的已发布行指纹并保存；
- 操作后、提交前，用同一排除集合再算一次并比较；
- 不一致整批回滚；回告里给前后两个值；
- 补「指纹变化时回滚」用例。

该脚本尚未执行，M2 也明确不推断云端已发生修改；本条是执行前的保护。

## 4. SRDI 补细则来源：M3 的确认条件

M1 提出把细则 `post_12435148` 新增为 SRDI 版本的 `policy_source`，再据此设置有效期。M3 的原则性意见：

**可以做，但必须满足以下条件，且 M1 先贴出具体行形状再执行。**

1. **只新增来源行，不改已发布版本行**：不要 UPDATE `policy_version.source_url`（已发布行受 `policy_version_guard_published` 保护）。新增 `policy_source` 行不属于改已发布版本字段，但会改变该版本的来源集合与计数，必须单独说明。
2. **来源语义分开**：
   - 细则正文（`post_12435148`）= 该版本的有效期依据（第二十四条）；
   - 2025 年申报通知（`post_12433720`）= 申报窗口依据；
   - provenance 中 `effectiveFrom/To` 绑细则行，`applicationWindow` 绑通知行，note 点明两者不是同一份文件。
3. **来源行内容**：请 M1 贴出拟插入的 `kind`/`ordinal`/`file_name`/`source_url`/`content_hash`/`byte_size`/`storage_key`。既有记录：细则页面 200、49,372 B、SHA-256 `047835c1…`（M3 既有记录；本轮未重算）。**不要把申报通知的哈希或提取文本哈希写成细则原件哈希**；若入库的是提取文本，按第六轮口径如实登记 `.txt`/`text/plain` 与文本哈希，原件哈希另记。
4. **计数影响**：VIS-06 的分母仍是 9 个登记版本（不是来源行数）；但来源完整度、材料计数或页面的「来源 N 份」会变化。请列出受影响的统计口径与页面字段，不能静默改数。
5. **provenance 是新的数据变更**：SRDI 的 `effectiveFrom/To` 状态、locator、note 会改，**不能沿用旧 `approved-by-m3`**。请提交新数据后由 M3 重新复核并更新 `reviewEvidence`；`source_register.csv` 是冻结评测输入，本轮仍不改。
6. **B 的值**：细则第二十四条「自印发之日起施行，有效期5年」。先核对印发日：
   - 取得印发日 → `effective_from` 该日（`verified`），`effective_to` 5 年后前一日（`derived`，注明「5 年减 1 日」）；
   - 未取得 → `effective_from=published_at`、`effective_to=NULL` 占位，note 写明 5 年有效期未承载、印发日待核实，provenance 保持 `unverified`/`unknown`。
7. **授权**：B 仍是已发布行的例外操作，须 M2 与项目负责人明确同意；补来源行本身不等于批准 B，也不等于批准发布。

如果 M1/M2 认为时间不够，M3 接受先不补来源、保持 A 的展示口径，但验收记录必须写「SRDI 有效期字段仍为窗口值，B 未执行」，不能写成已解决。

## 5. 合并状态

- 冲突已解（base `a1ec7e1`），head `3b1d198` 三项 CI 绿（M2 已核对，M3 亦看到 API 结果）。
- M2 仍要求两条 P1 修复后再合并；**M3 同意，当前不同意合并**。
- 两条 P1 修好后需 M2 再复审；M3 的 B1a 数据批准不变（`date-provenance-backfill.json` 未在 `3b1d198` 改动）。

## 6. VIS-06

B1a 已完成；本轮仍不产生「通过」项：

- B1b 读侧、M2 展示证据、M3 页面复核、F12 `published_at` 未完成；
- SMART/GREEN 未发布；SRDI 在 B 前仍不正确，A 只是展示口径；
- `source_register.csv` 的 SMART/SRDI/GREEN 有效期仍等于窗口；冻结评测输入本轮不改。

## 7. 本轮验证与未执行事项

**已做：**

- 读取 M1 `6881226314`、M2 `6881322385`，核对 `3b1d198` 的 5 个文件；
- 核对首次插入路径仍未使用计划、降级脚本指纹仍在降级后计算；
- 核对 head CI 三项 success、PR base 已到 `a1ec7e1`；
- 核对 README/实现文档已改为 B 目标/A 临时口径。

**未做：**

- 未连库、未执行回填/降级/SRDI 修正；
- 未独立复核 M1 测试、云端引用计数或 `xmin`；
- 未取得 M2/负责人对 `v1.0` 或 SRDI B 的同意；
- 未改 `infra/**`、`docs/IMPLEMENTATION_AND_INTERFACE.md`、`data/**`、契约、代码或评测输入。

## 8. 本次修改文件

- 新建：`docs/product/M3_DATE_SOURCE_ROUND7_1115.md`（本文件）。
- 更新：`docs/product/POLICY_SCOPE.md` VIS-06 节。
- 更新：`docs/product/M3_DATE_SOURCE_ROUND6_1115.md`（补后续指向本文）。
- 更新：`docs/devlog/M3.md`。
- PR #69 复核评论：https://github.com/sgase233/huice/pull/69#issuecomment-6881382005
- **未修改**：`infra/**`、`docs/IMPLEMENTATION_AND_INTERFACE.md`、`contracts/**`、`apps/web/**`、`services/api/**`、`data/**`、冻结输入。
