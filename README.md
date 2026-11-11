# 惠策协同

**项目组统一入口：[文档导航与成员任务](docs/README.md)。**M1、M2、M3 从这里读取扩展计划、接口约束、自己的第一批交付及共同验收要求；跨成员整理记录见[项目协作日志](docs/devlog/PROJECT.md)。

截至 2026-11-09，本地基线 `807e1bb` 已具备 16 个后端端点及企业画像、机会列表、政策详情、清单与报告、审核和轨迹页面，另有首页与深浅主题；固定合成样例通过 `?mode=demo` 进入。项目处于原计划倒数第二阶段之后的最终验收准备，已有阶段验证不等于当前云端及正式政策联合验收完成。功能实现与分工见[功能实现逻辑与接口协作约定](./docs/IMPLEMENTATION_AND_INTERFACE.md)，环境配置见 [infra/local/README.md](./infra/local/README.md)、[infra/README.md](./infra/README.md) 与 [apps/web/README.md](./apps/web/README.md)。

**后续开发入口：**[开发计划与 issue 决策](./docs/DEVELOPMENT_PLAN.md) → [#46—#48 扩展接口设计（待签收）](./docs/EXTENSION_INTERFACES.md) → [原功能及扩展验收](./docs/product/ACCEPTANCE.md)。扩展包含账号与数据隔离、政策发现/候选审核、五项业务图形与两项审核端趋势；第 6 项政策审核/版本变化图暂缓。现行契约与新增设计分开维护，不将计划写成已实现能力。原 11/15 截止与分阶段资料保留在[历史开发计划](./docs/brief/惠策协同_三人分工与开发计划_截至11月15日.md)。

团队 Git 分支、提交和推送要求见[工作手册](./WORKING_GUIDE.md)：M1/M2/M3 各自建分支；每次提交和远程推送前均须取得明确同意。

2026-10-24 负责人确认：允许 DeepSeek 替代 MaaS、其他 AI 编码助手替代 CodeArts；最终材料可统一写“AI 辅助编码”，工具使用说明须符合实际。完整规则及旧计划的修订口径见[工作手册“模型与编码工具规则”](./WORKING_GUIDE.md)。

`contracts/openapi.yaml` 是 23 个操作（API-01—16 + AUTH-01—07）的 OpenAPI 3.1 契约，当前为 `1.1.0-rc.1`（1.1.0-rc.1 把 E1 的账号/会话五个操作与两个按归属过滤的列表并入正式契约，新增 `User`/`SessionResponse` 等 13 个 schema；既有 16 个操作的字段与授权不变，但 `apps/web/src/api/types.ts` 需由 M2 重新生成。rc.10 把每个操作的 `security` 改成 E1 之后的真实授权——后台写只接受登录会话，运维 Bearer 降为只读，业务接口是会话或演示会话，轨迹三类身份都可读，并补 `UserSession` 安全方案；rc.10 把每个操作的 `security` 改成 E1 之后的真实授权——后台写只接受登录会话，运维 Bearer 降为只读，业务接口是会话或演示会话，轨迹三类身份都可读，并补 `UserSession` 安全方案；rc.2 将模型模式枚举 `maas` 改为 `deepseek`；rc.3 将事实字典扩到 16 键并新增 `ratio` 单位；rc.4 再扩到 18 键，`date` 算子不再只认成立日期；rc.5 新增 `manual_review` 算子表达「本条须人工核查」；rc.6 把轨迹步骤的 `evidenceRefs` 改为 `objectRefs`，与引文 `EvidenceRef` 分开；rc.7 给 `ModelUsage` 加可选 `tokensUnknown`，用来区分「上游没给用量」与「真实 0」；服务端只在未知时返回它，已知时省略，常路径响应与 rc.6 逐字段一致；rc.8 给 `PreparationItem` 加必填 `materialId` 与 `status`，把「资料缺失」拆成 `provided/missing/manual_check/policy_closed` 四态，没有事实键的材料不再被写成缺材料；rc.9 把前两个取值改名 `facts_filled/facts_missing`——服务端只知道事实有没有取值，不知道材料交没交，`provided` 这个名字多说了它证明不了的一层），`apps/web/src/api/types.ts` 由它生成；M2 复核后再定版。后端 23 个操作均已实现（逐项状态见 `contracts/README.md`）；前端画像、机会、详情、报告、审核与轨迹页面已接入，最终联合验收按实际证据签收。

- M1：`services/api/`、`infra/`、`contracts/` 与根配置。
- M2：`apps/web/`。
- M3：`data/`、`docs/product/`、`docs/ui/`、`docs/demo/`。

新环境按上述运行文档安装工具并从 `.env.example`、`infra/local/.env.example` 和 `apps/web/.env.example` 创建本机配置；后续开发沿用现有实现、契约与依赖锁，补齐对应测试、部署和演示证据。不要提交凭据或真实企业材料。

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
.\.venv\Scripts\python.exe -m pytest -q          # 583 项通过，48 项外部依赖用例跳过（2026-11-01 实测）
$env:HUICE_TEST_DATABASE_URL = "postgresql://...(本地测试库)"
.\.venv\Scripts\python.exe -m pytest -m db -q    # 需真实 PostgreSQL 16 + pgvector；现共 37 项（2026-11-01 统计），2026-10-29 带库实测 36 项通过
```

静态契约检查（从 `huice/` 运行，不连接数据库或云端）：

```powershell
python contracts/generate_types.py --check
python -m unittest discover -s services/api/tests -p test_contract.py -v
```

M1 修改契约后，由 M2 在其目录内执行 `python contracts/generate_types.py` 重新生成 `apps/web/src/api/types.ts` 并审查类型差异（`apps/web/**` 一律不在 M1 的改动范围内，生成物也一样）；在 M2 重新生成之前，`generate_types.py --check` 会如实报不一致。依赖版本记录在 `services/api/requirements.lock`，契约版本与实现范围见 `contracts/README.md`。数据库验收在本地 PostgreSQL 16.15 + pgvector 0.8.7 上执行（建表脚本与迁移 002/003/004/005/006 各连跑两遍、37 个 db 用例、导入/读回冒烟），结果见 `infra/local/README.md`；真实 Redis、OBS 桶与 DeepSeek Key 的用例由环境变量门控，默认跳过，命令见 `infra/README.md` §4.3 与 `infra/local/README.md`。前端浏览器验收由 M2 按阶段交付，第三、四阶段的记录见 `apps/web/PHASE3_ACCEPTANCE.md` 与 `apps/web/PHASE4_ACCEPTANCE.md`；审核页与轨迹页的阶段验证见 `apps/web/PHASE5_ACCEPTANCE.md` 与 `apps/web/PHASE6_ACCEPTANCE.md`；未执行的最终联合检查不计作通过。
