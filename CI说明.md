# 持续集成（CI）说明

工作流位于 `.github/workflows/ci.yml`。PR 目标为 `main` 时、`main` 有新推送时，以及在 GitHub Actions 页面手动运行时触发。PR 更新后会取消同一 PR 的旧运行。GitHub 页面上的各项检查显示成功或失败，点击失败项可查看日志。

## 检查内容

- **Backend and contract**：Python 3.12 按 `services/api/requirements.lock` 安装依赖并执行 `pip check`；检查 OpenAPI 生成的前端类型是否同步；运行后端测试，包括临时 PostgreSQL 16 + pgvector、Redis 集成测试。现有数据库夹具在独立测试 schema 中将所有 SQL 迁移按文件名顺序执行两遍。
- **Frontend**：Node.js 24 执行 `npm ci`、`npm run build` 和 `npm test`。前端浏览器端到端测试当前要求本机 Edge，暂未纳入 CI。
- **CSV format**：检查 `data/` 下 CSV 的 UTF-8 编码、表头重复、CSV 格式及各行列数。它不核对政策内容、业务含义或双人复核状态。

所有 PR 都运行全部检查，以便发现跨成员文件和契约的影响。数据库和 Redis 是每次运行临时启动的服务，数据库账号与密码仅用于 CI 测试；切勿把它们用作真实环境凭据。测试会创建、删除测试 schema，**不要把 `HUICE_TEST_DATABASE_URL` 指向业务库**。

## 边界与注意事项

- 通过 CI 只代表上述检查通过，不代表政策来源准确、业务规则正确或完成双人标注；仍需按团队分工人工审查。新增业务行为应补充能发现实际错误的测试。
- 不运行依赖真实 OBS 或 DeepSeek 的测试；这些联调需按项目说明在受控环境另行执行。CI 不部署应用，不使用真实企业资料和服务密钥。
- 修改 OpenAPI 后运行 `python contracts/generate_types.py` 并提交生成结果，由 M2 复核；修改数据库结构时同时维护迁移和数据库测试。
- 当前使用公开仓库的标准 GitHub 托管运行器；若仓库改为私有或选择收费运行器，应检查账户额度和计费。参见 [GitHub Actions 计费](https://docs.github.com/en/billing/concepts/product-billing/github-actions)。
- 工作流本身不会自动阻止合并。先确认各检查稳定运行，再由有权限的负责人决定是否在 `main` 分支保护中将这些检查设为必需。变更分支保护不属于本文件配置。
- 本地调试命令和环境配置见根目录 `README.md`、`contracts/README.md` 和 `infra/local/README.md`；不要提交 `.env`、密钥或测试数据库数据。
