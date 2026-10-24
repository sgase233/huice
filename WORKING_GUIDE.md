# 惠策协同工作手册

本手册适用于 `huice/` 项目。功能范围、接口、逐文件分工见[功能实现逻辑与接口协作约定](./docs/IMPLEMENTATION_AND_INTERFACE.md)；当前应用仍是代码骨架。

## 模型与编码工具规则（2026-10-24 负责人确认）

本节经项目负责人在当前会话明确确认并授权写入，作为旧策划、需求和开发计划中 MaaS、CodeArts 必选要求的修订依据。

- **模型服务**：允许以 DeepSeek 替代华为云 MaaS，不再以开通或接入 MaaS 作为项目验收要求。当前实现使用 `MODEL_MODE=fixture/deepseek`；真实 DeepSeek 调用仍须完成联调并保留实际结果，fixture 不能算作真实调用通过。
- **编码工具**：允许使用其他 AI 编码助手或智能体替代 CodeArts，CodeArts/Skills 不再是本项目开发与验收的必选工具。仍须保留实际开发、人工复核、测试和提交记录。
- **最终材料**：可以不逐一列出其他编码助手名称，统一表述为“采用 AI 辅助编码，结合人工审查与测试完成开发”。只有实际使用过 CodeArts 的工作才能注明“使用 CodeArts”；不将其他工具的工作改写为 CodeArts 产出，也不改造已有真实记录。
- **适用边界**：上述调整不改变确定性规则负责资格判断、接口契约审查、文件分工、真实验证以及逐次提交/推送授权要求；也不代表已经满足任何外部赛事的工具要求。

## 开始工作：每位成员自行建立分支

M1、M2、M3 **各自按照以下步骤新建自己的工作分支，并始终在自己的分支下修改文件**。不要多人共用一个工作分支，也不要直接在 `main` 上开发。

```powershell
cd C:\Users\刘佳熙\Desktop\ICT\huice
git fetch origin
git switch main
git pull --ff-only origin main
git switch -c m1/backend-简短任务名   # M1 示例；M2 改为 m2/frontend-简短任务名，M3 改为 m3/policy-简短任务名
git branch --show-current          # 核对自己当前所在分支
```

已存在同名分支时，先检查分支归属和状态，再由该成员切换或另取名称；不要强制重建、覆盖别人的分支。准备修改前阅读自己的文件范围；跨成员文件由所有者修改，接口变更先同步 OpenAPI 和说明文件。

## 提交与推送必须先征得同意

**每一次 `git commit` 和每一次向远程仓库执行 `git push`，执行者都必须在操作之前取得项目负责人或此次提交授权人的明确同意。**同意应指向具体的分支、待提交文件/变更摘要和推送目标；仅讨论任务、完成代码或获得过往提交许可，不视为对下一次提交或推送的同意。`git commit --amend`、`git merge`、`git rebase` 后推送以及创建远程分支同样遵守该规则；禁止未授权的强制推送。

**每次提交和推送都要用中文大致注明本次改动。**提交时，`git commit -m` 必须写能看出改动内容的中文摘要，例如 `文档：补充分支与接口协作说明`，不能只写“更新”“修复”或无含义编号。推送前，在征求同意的说明中用中文写明拟推送的分支、提交和主要改动；推送后，在对应 PR、团队工作记录或给授权人的反馈中用中文注明实际推送的分支、提交号及改动概述。`git push` 本身没有提交说明参数，不能把中文说明伪装成命令参数；需用上述可追溯的文字记录。

获得同意前，可以在个人分支编写代码、运行测试、检查差异和准备提交说明。检查：

```powershell
git status --short
git diff --check
git diff
git diff --cached
git branch --show-current
```

确认 `.env`、`.env.local`、真实企业材料、密钥、数据库转储及本地运行产物未进入暂存区。经明确同意后，只暂存审过的文件、提交到自己的分支，并推送该分支：

```powershell
git add -- <已核对的文件路径>
git diff --cached --check
git diff --cached --stat
git commit -m "文档：补充本次具体改动"
git push -u origin <当前工作分支名>
```

推送前仍须分别确认远程推送已获同意，并附上本次推送的中文改动概述；提交同意不自动覆盖推送同意。后续通过 PR 交叉审查并按团队约定合并，不能自行将未审分支推到 `main`。紧急修复也先说明变更范围并征得同意。

## 文件责任

- **M1**：`services/api/**`、`contracts/**`、`infra/**`、`docs/IMPLEMENTATION_AND_INTERFACE.md`、项目根配置/README 及 `docs/devlog/M1.md`；维护本手册的 Git 操作说明。
- **M2**：`apps/web/**` 与 `docs/devlog/M2.md`；审查契约变更和生成类型。
- **M3**：`data/**`、`docs/product/**`、`docs/ui/**`、`docs/demo/**`、`docs/diagrams/**` 与 `docs/devlog/M3.md`；审查政策事实和中文含义。

`docs/brief/**` 是本次导入的原始策划/需求/评审/分工资料，供全员只读引用；修订先讨论后指定提交者。

远程仓库：`https://github.com/sgase233/huice.git`。首次初始化/导入和推送由本次用户请求明确授权；本手册的逐次同意规则适用于之后每位成员的工作。
