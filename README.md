# 惠策协同

后端已实现契约全部 16 个端点（API-01—API-16，2026-10-27 合入 PR #8），前端仍是注释骨架。功能实现、参数传递、前后端约束和逐文件分工见[功能实现逻辑与接口协作约定](./docs/IMPLEMENTATION_AND_INTERFACE.md)。环境配置仍参考[原开发计划](./docs/brief/惠策协同_三人分工与开发计划_截至11月15日.md)第 4.1 节。

团队 Git 分支、提交和推送要求见[工作手册](./WORKING_GUIDE.md)：M1/M2/M3 各自建分支；每次提交和远程推送前均须取得明确同意。

2026-10-24 负责人确认：允许 DeepSeek 替代 MaaS、其他 AI 编码助手替代 CodeArts；最终材料可统一写“AI 辅助编码”，工具使用说明须符合实际。完整规则及旧计划的修订口径见[工作手册“模型与编码工具规则”](./WORKING_GUIDE.md)。

`contracts/openapi.yaml` 是 16 个接口的 OpenAPI 3.1 契约，当前为 `1.0.0-rc.3`（原14个接口加后台草稿列表/详情；rc.2 将模型模式枚举 `maas` 改为 `deepseek`；rc.3 将事实字典扩到 16 键并新增 `ratio` 单位），`apps/web/src/api/types.ts` 由它生成；M2 复核后再定版 `1.0.0`。后端 16 个接口均已实现（逐项状态见 `contracts/README.md`），前端页面尚未接完。

- M1：`services/api/`、`infra/`、`contracts/` 与根配置。
- M2：`apps/web/`。
- M3：`data/`、`docs/product/`、`docs/ui/`、`docs/demo/`。

实现时先按第 4.1 节安装工具、复制 `.env.example`、`infra/local/.env.example` 和 `apps/web/.env.example`，再按接口契约提交最小可运行版本，补测试、部署和演示证据。不要提交凭据或真实企业材料。

多智能体内部交接与调度边界见 `services/api/AGENT_PROTOCOL.md`；M1 负责实现，M2 通过 API-14 展示脱敏轨迹，M3 复核政策依据和中文结果。

## 本地运行后端

```powershell
# 1) 依赖（Python 3.12）
py -3.12 -m venv services\api\.venv
services\api\.venv\Scripts\python.exe -m pip install -r services\api\requirements.lock

# 2) 配置：复制 .env.example 为 .env，至少填 DATABASE_URL 与 ≥16 位的 ADMIN_TOKEN
Copy-Item .env.example .env

# 3) 启动（缺变量时进程启动即失败，且只报变量名）
services\api\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir services\api --host 127.0.0.1 --port 8000

# 4) 健康检查（数据库不可用返回 503，不暴露连接信息）
curl.exe -i http://127.0.0.1:8000/api/v1/health
```

本地数据库与 Redis 见 [infra/local/README.md](./infra/local/README.md)；云资源阻碍与回退步骤见 [infra/README.md](./infra/README.md)。

## 测试

```powershell
cd services\api
.\.venv\Scripts\python.exe -m pytest -q          # 468 项通过，46 项外部依赖用例跳过
$env:HUICE_TEST_DATABASE_URL = "postgresql://...(本地测试库)"
.\.venv\Scripts\python.exe -m pytest -m db -q    # 需真实 PostgreSQL 16 + pgvector；2026-10-29 实测 34 项通过
```

静态契约检查（从 `huice/` 运行，不连接数据库或云端）：

```powershell
python contracts/generate_types.py --check
python -m unittest discover -s services/api/tests -p test_contract.py -v
```

M1 修改契约后执行 `python contracts/generate_types.py`，由 M2 审查生成的类型差异。依赖版本记录在 `services/api/requirements.lock`，契约版本与实现范围见 `contracts/README.md`。数据库验收在本地 PostgreSQL 16.15 + pgvector 0.8.7 上执行（建表脚本与迁移 002/003/004 各连跑两遍、34 个 db 用例、导入/读回冒烟），结果见 `infra/local/README.md`；真实 Redis、OBS 桶与 DeepSeek Key 的用例由环境变量门控，默认跳过，命令见 `infra/README.md` §4.3 与 `infra/local/README.md`。前端浏览器验收仍待 M2 补齐，未执行的检查不计作通过。
