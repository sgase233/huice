# 云资源、本地环境与部署（M1）

本文件记录 M1 阶段真实使用或计划使用的版本、本地数据服务、云资源阻碍与回退步骤。**未执行的检查一律标注“未执行”，不写推测的通过结论。**

环境基线与变量约束见根目录开发计划的第 4.1 节；本地命令详见 [local/README.md](local/README.md)。

## 1. 本机工具版本（2026-10-23 实测）

| 工具 | 实测版本 | 计划基线 | 结论 |
| --- | --- | --- | --- |
| 操作系统 | Windows 11 10.0.26200 | — | 可用 |
| Python | CPython 3.12.10（`services/api/.venv`） | 3.12 | 一致 |
| 默认 `python` | 3.14.3 | 3.12 | 偏差：创建 venv 一律用 `py -3.12` |
| Git | 2.54.0.windows.1 | — | 可用 |
| Node.js | v24.15.0 | 22 LTS | 偏差：仅影响前端（M2），M1 后端不受影响 |
| Docker | Docker Desktop 4.96.3；CLI 29.9.2；Compose v5.6.1 | Docker Desktop | 一致（引擎需手动启动） |
| WSL2 | WSL 3.0.3.0，默认版本 2，内核 6.18.54.1 | — | 已就绪 |

后端依赖的真实版本见 `services/api/requirements.lock`（48 项，`pip freeze` 生成；2026-10-26 新增 `redis 8.1.0` 后重新冻结）。其中 FastAPI 0.161.1、Starlette 1.9.0、Pydantic 2.14.1、psycopg 3.3.6（+ pool 3.3.3）、pypdf 6.21.2、uvicorn 0.54.0、redis 8.1.0。

## 2. 本地数据服务

| 服务 | 镜像 | 端口（仅回环） | 数据卷 |
| --- | --- | --- | --- |
| PostgreSQL 16 + pgvector | `pgvector/pgvector:pg16` | `127.0.0.1:5432` | `huice-db-data` |
| Redis 7 | `redis:7.4-alpine` | `127.0.0.1:6379` | `huice-redis-data` |

镜像使用固定 tag，2026-10-23 在本机拉到的 digest：`pgvector/pgvector:pg16` = `sha256:7b822b0aac60967beb1ea5e576b8602c94c300a157d187f385ae3e0da199b90a`，`redis:7.4-alpine` = `sha256:57e4255cc971b1c8d6f8a6ca283ac01bc3ecdd914273ef6bfaede87aaecd6541`。国内直连 Docker Hub 会超时，需在 Docker 引擎配置 `registry-mirrors`，写法见 [local/README.md](local/README.md)。

启动、初始化、验收与清理命令见 [local/README.md](local/README.md)；验收已在真实 PostgreSQL 16.15 + pgvector 0.8.7 上执行并记录。

## 3. 数据库初始化与迁移

```powershell
# 本地容器（/sql 为 infra/sql 的只读挂载）
docker compose --env-file infra/local/.env -f infra/local/docker-compose.yml exec db `
  psql -U huice -d huice_dev -v ON_ERROR_STOP=1 -f /sql/001_init.sql
docker compose --env-file infra/local/.env -f infra/local/docker-compose.yml exec db `
  psql -U huice -d huice_dev -v ON_ERROR_STOP=1 -f /sql/002_evaluation_and_idempotency.sql
docker compose --env-file infra/local/.env -f infra/local/docker-compose.yml exec db `
  psql -U huice -d huice_dev -v ON_ERROR_STOP=1 -f /sql/003_trace_step_details.sql
docker compose --env-file infra/local/.env -f infra/local/docker-compose.yml exec db `
  psql -U huice -d huice_dev -v ON_ERROR_STOP=1 -f /sql/004_invalidation_event_period.sql

# 云 RDS 或本机 PG
psql "<连接串>" -v ON_ERROR_STOP=1 -f infra/sql/001_init.sql
psql "<连接串>" -v ON_ERROR_STOP=1 -f infra/sql/002_evaluation_and_idempotency.sql
psql "<连接串>" -v ON_ERROR_STOP=1 -f infra/sql/003_trace_step_details.sql
psql "<连接串>" -v ON_ERROR_STOP=1 -f infra/sql/004_invalidation_event_period.sql
```

`infra/sql/001_init.sql` 建 13 张表（policy、policy_version、policy_source、policy_criterion、policy_validation_finding、enterprise、enterprise_fact、assessment_run、assessment、assessment_dependency、invalidation_event、trace_step、schema_migration）、启用 `vector` 扩展、创建 updated_at / 已发布版本保护 / 只追加表三类触发器，并在末尾写入 `schema_migration('001_init', ...)`。脚本开 `BEGIN/COMMIT`、全部使用 `IF NOT EXISTS`/`CREATE OR REPLACE`，可重复执行。

`infra/sql/002_evaluation_and_idempotency.sql` 把评估运行与幂等所需的结构补齐、修正与契约不一致的枚举，同样可重复执行：

- `enterprise_fact.source` 的 CHECK 由 `user_input|verified_document` 改为契约里的 `user_input|synthetic_material`（合成演示材料原先无法入库）。
- `assessment_run` 增加 `period_start`、`period_end`、`supersedes_run_id`（契约 `AssessmentRun` 必带 `period` 与 `supersedesRunId`），并更新 `guard_assessment_run_update()` 把这三列也一并冻结。
- 新增只追加表 `idempotency_record(scope, key, request_fingerprint, response, revision)`，供 API-05 按 `Idempotency-Key` 防重复抽取与重复计费；沿用 001 的 `reject_mutation()` 触发器禁止改删。
- `assessment` 增加 `snapshot jsonb`（对象形状 CHECK），让条件结果/缺失事实/补问/解释随 run 一起成为历史；`trace_step.status` 的历史值（`succeeded/failed/skipped`）先搬到契约的 `AgentStatus` 枚举（`ok/needs_review/retryable_error/fatal_error`）再换约束，期间临时摘掉只追加触发器、改完立即恢复。

`infra/sql/003_trace_step_details.sql` 让 `trace_step` 能原样支撑 API-14：

- 增加 `input_summary`、`output_summary`（契约要求两个摘要分开传）、`policy_id`、`policy_version`、`error_code`、`human_action`。
- 把导入步骤写入的 `stage='policy_import'` 归一化为契约枚举里的 `ingestion`（在临时摘掉只追加触发器的窗口内完成，随后恢复），并新增 `trace_step_stage_check` 固定九个阶段值。
- 历史行的 `input_summary` 用原 `summary` 回填，`output_summary` 回填空串；新写入的行两个摘要都完整。

执行 003 后基础表仍为 14 张（只加列）。已执行过 001/002 的库（含云 RDS）按序执行 002、003 即可，**不要重跑或改写 001**。

`infra/sql/004_invalidation_event_period.sql` 让失效事件带上事实期间：

- `invalidation_event` 增加 `fact_period_start`、`fact_period_end` 两列，并加约束 `invalidation_event_fact_period_check`：两列要么都有值、要么都为空，有值时结束不早于开始。
- 新增索引 `invalidation_event_run_idx (run_id, created_at)`，对应 `get_run` 的读取路径。
- 背景：写入端本来就按 key + 期间 命中依赖，但事件行只存 `fact_key`，读取端只能按键匹配，同一个键的两个期间会被一锅端。补列后读取按 key + 期间 精确匹配；004 之前写入的旧行期间为空，读取按整键保守匹配。

执行 004 后基础表仍为 14 张（只加列）。已执行过 001—003 的库（含云 RDS）按序补执行 004 即可。

> **2026-10-26 修正**：002 早期版本第 4 节用 `ALTER TABLE trace_step DROP TRIGGER IF EXISTS ...` 删触发器，这不是合法语法（`DROP TRIGGER` 是独立语句；`ALTER TABLE` 下也没有 `DROP TRIGGER IF EXISTS` 形式），执行会在该行报 `syntax error at or near "IF"`。已改为独立语句 `DROP TRIGGER IF EXISTS trace_step_no_update_delete ON trace_step;`，并在修正后连续执行两遍验证（退出码 0，`trace_step_status_check` 为 `ok/needs_review/retryable_error/fatal_error`）。由于脚本整体在一个事务里，旧的报错不会留下半成品结构；若 `schema_migration` 里已有 002 行但结构未变，先按 [local/README.md](local/README.md) 的「002 迁移」一节核对触发器与约束，再重跑 002。

迁移约定：新变更写 `004_*.sql` 之后的下一个序号，不改历史脚本；每条迁移同样登记 `schema_migration`。

## 4. 云端资源状态（截至 2026-10-23）

### 4.1 已开通

| 资源 | 参数 | 备注 |
| --- | --- | --- |
| VPC / 子网 | `huice-vpc`（`192.168.0.0/16`）、`huice-subnet`（`192.168.0.0/24`，251 个可用 IP） | 华南-广州；子网网段创建后不可修改 |
| ECS | `huice-api-01`，2 vCPU / 4 GiB，Ubuntu 24.04.4 LTS（自带 Python 3.12.3），按需计费 | 华南-广州 / **可用区7**；内网 `192.168.0.86`；公网 EIP `139.9.33.46`；2026-10-23 实测 `lsb_release`、`python3 --version`、`ip -4 addr` |
| 安全组（入方向） | TCP 22 / 80 / 443，源 `0.0.0.0/0` | 22 已实测可连（2026-10-23）；80/443 暂无服务监听，属正常 |
| 登录方式 | 密码（用户 `root`） | 密码不入文档；建议后续换成密钥对 |
| CodeArts / Skills | 本人 2026-10-23 告知已开通（未在控制台复核） | **本项目不接入**：队长 2026-10-23 决定，开发过程记录以实际使用的编码工具（Claude Code，见 `docs/devlog/M1.md`）为准 |
| Redis（DCS） | **已购买并实测连通（2026-10-23）**：`huice-redis`，Redis 7.0（`INFO server` 报 `redis_version:7.x`），**主备**，1 GB，内网 `192.168.0.38:6379` | 在 ECS `huice-api-01` 上 `AUTH` 返回 `OK`、`PING` 返回 `PONG`；密码只进服务器 `.env`，不入库不入文档。**2026-10-26 复测**：`/dev/tcp/192.168.0.38/6379` 可连、`redis-cli --askpass PING` → `PONG`，控制台显示小版本 `7.2.11.5`，白名单已含 ECS 内网 `192.168.0.86`（2026-10-24 的「连接被拒」不复现）。2026-10-26 起判断链路有版本化求值缓存（键前缀 `huice:eval:v1:`，TTL 300 秒），`redis==8.1.0` 已入 `requirements.lock`，部署时填好 `REDIS_URL` 即真实读写；Redis 不可达时自动按「未命中重算」执行 |
| RDS for PostgreSQL | **已购买、实测连通并完成建表（2026-10-23）**：PostgreSQL **16.13**，VPC `huice-vpc` / 子网 `huice-subnet`，端口 5432，内网 `192.168.0.80`，库名 `huice` | ECS 上 `psql` 连接成功；`pg_available_extensions` 有 `vector`（`default_version 0.8.0`，本地 0.8.7，同为 0.8 系列）；`001_init.sql` 以 `-v ON_ERROR_STOP=1` 执行，`BEGIN`→37 条 DDL→`INSERT 0 1`→`COMMIT` 无报错，建出 **13 张表、13 个业务触发器**（脚本内 `CREATE OR REPLACE TRIGGER` 13 条；**2026-10-24 已用 `WHERE NOT tgisinternal` 复核：`public` 下业务触发器 13 个，`pg_trigger` 全量 61 行，其余 48 行为外键内部触发器**；早期记的「24 个」为未注明的旧口径，已作废），`schema_migration` 记 `001_init`，`pg_extension.extversion = 0.8.0`；**2026-10-26 23:47 补齐 `002_evaluation_and_idempotency.sql` 与 `003_trace_step_details.sql`**（`schema_migration` 三行齐全，`trace_step` 六列与两个 CHECK 约束、`trace_step_no_update_delete` 触发器已核对）；**`004_invalidation_event_period.sql` 尚未执行**，11/08 部署时补齐 |
| OBS（对象存储） | **已购买并建桶（2026-10-23）**：桶 `huice-policies`，**私有**，区域 `cn-south-1`（华南-广州），endpoint `obs.cn-south-1.myhuaweicloud.com` | 区域级服务，不进 VPC；`ObsObjectStore` 已用 `esdk-obs-python` 3.26.6 实现（`obs/<uuid>` 命名空间、失败映射 503），**2026-10-27 已在真实桶上实测通过**（`-m obs` 5 项：写入→独立客户端读回字节、删除后 404、两次写入键不冲突、导入正文真正落桶、不可写桶 503 且不泄露凭证，见 §4.3）；AK/SK 只存于 ECS 的 `/etc/huice/api.env`（本地联调用过的一对因失败输出误打印已作废重建） |
| Docker Desktop + WSL2（本地） | WSL 3.0.3.0 + Docker Desktop 4.96.3 | 已解除，本地 compose 可用 |

### 4.2 未完成

| 资源 | 状态 | 待办 | 负责人 | 复查日期 |
| --- | --- | --- | --- | --- |
| DeepSeek 模型调用 | **已接入并完成真实 Key 联调（2026-10-27）**：`MODEL_MODE=deepseek` 走 `app/integrations/llm.py`，OpenAI 兼容 `/chat/completions`，429→`UPSTREAM_RATE_LIMITED`、其余失败→503 并降级为规则结果；`-m deepseek` 3 项通过（真实补全返回文本/用量、错误 Key→503 不回显、整链判断 `explanationMode=deepseek` 而结论仍由规则给出），见 §4.3 | 在 `.env` 与云端 `/etc/huice/api.env` 填入 `DEEPSEEK_BASE_URL`/`DEEPSEEK_MODEL`/`DEEPSEEK_API_KEY` 后即可把 `MODEL_MODE` 从 `fixture` 切到 `deepseek`；云端切换随 11/08 部署验证 | M1 | 2026-11-08 |

MaaS 按队长 2026-10-23 决定不再使用：华为云账号未开通 MaaS，模型调用改由 DeepSeek 承担，故不再有华为云侧的待办。

⚠️ 公网 EIP 按小时计费，与是否被访问无关；演示结束或暂停开发时应解绑/释放，避免空转扣费。

### 4.3 外部依赖联调（OBS / DeepSeek）：已实测通过（2026-10-27）

两项外部依赖的代码早在 10/23 完成（`app/integrations/obs.py`、`app/integrations/llm.py`，含失败映射与降级），缺的只是**真实凭证下的实测**。2026-10-27 补了两个可选用例，做法与 `test_cache_redis_live.py` 一致：默认跳过，只有设了对应环境变量才联网。**同日在本机 cmd 中执行：`-m obs` → 5 passed，`-m deepseek` → 3 passed**（真桶 `huice-policies`、区域 `cn-south-1`）。

| 用例 | 门控变量 | 验证内容 |
| --- | --- | --- |
| `services/api/tests/test_obs_live.py`（`-m obs`，5 项） | `HUICE_TEST_OBS_BUCKET`、`HUICE_TEST_OBS_ENDPOINT`、`HUICE_TEST_OBS_AK`、`HUICE_TEST_OBS_SK` | 经 `ObsObjectStore` 写入后**用另一个独立 SDK 客户端**读回字节；删除后对象 404；同一内容两次写入键不冲突；`POST /admin/policies/import` 的正文真正落到桶里（契约的 `SourceDocument` 不含内部键 `storageKey`，断言取仓储入参 `storage_keys` 并与桶内键比对）；桶不可写时映射为 503 且响应/异常里不含桶名与 AK/SK |
| `services/api/tests/test_deepseek_live.py`（`-m deepseek`，3 项） | `HUICE_TEST_DEEPSEEK_API_KEY`（可选 `HUICE_TEST_DEEPSEEK_BASE_URL`、`HUICE_TEST_DEEPSEEK_MODEL`，默认 `https://api.deepseek.com`、`deepseek-chat`） | 一次极短补全返回文本/模型名/用量且不回显 Key；错误 Key → 503 `DEPENDENCY_UNAVAILABLE`，消息不含 Key 与 base URL；整链判断里 `explanationMode=deepseek`，而**结论仍是规则求值的 `not_satisfied`** |

两条命令都在**本人终端**里执行，凭证只从环境变量读入，不进仓库、不进聊天记录：

```powershell
cd huice\services\api

# OBS：先在本会话导入四项（值从控制台「我的凭证」/桶概览取，不要写进文件）
$env:HUICE_TEST_OBS_BUCKET   = "<桶名，如 huice-policies>"
$env:HUICE_TEST_OBS_ENDPOINT = "obs.cn-south-1.myhuaweicloud.com"
$env:HUICE_TEST_OBS_AK       = "<AK>"
$env:HUICE_TEST_OBS_SK       = "<SK>"
.\.venv\Scripts\python.exe -m pytest -q -m obs

Remove-Item Env:HUICE_TEST_OBS_AK, Env:HUICE_TEST_OBS_SK

# DeepSeek：只需一个 Key
$env:HUICE_TEST_DEEPSEEK_API_KEY = "<Key>"
.\.venv\Scripts\python.exe -m pytest -q -m deepseek
Remove-Item Env:HUICE_TEST_DEEPSEEK_API_KEY
```

用例只操作自己写入的 `obs/<uuid>` 键并在结束时删除，因此可以指向已有数据的桶；单次 DeepSeek 调用 `max_tokens=16`/`8`，费用可忽略。上面两条命令在 cmd 里等价于 `set HUICE_TEST_OBS_AK=<AK>`（`=` 两侧不留空格、值不加引号）。**两项均已闭合**：结果同时记入 `docs/devlog/M1.md` 2026-10-27 一行；仍未闭合的是云端 API 部署后用线上配置再跑一次（随 11/08 部署）。

⚠️ **凭证不会被 pytest 打印**：用例失败时 pytest 会显示夹具取值，所以凭证装在 `Credentials` 数据类里（`__repr__` 固定输出 `<redacted>`），带密钥的 `Settings` 只在辅助函数内部临时构造、不绑定到用例局部变量。2026-10-27 首次实测时旧版本把 `Settings(... obs_ak=..., obs_sk=...)` 直接当夹具返回，一次断言失败就把 AK/SK 打进了终端——该密钥已作废重建，新版已改为上述写法。**不要把 `pytest -l`（`--showlocals`）与这两个 `-m` 用例一起用。**

验证缺口（本地已闭合）：`001_init.sql` 已在真实 PostgreSQL 16.15 上执行两次（第二次仅 `NOTICE ... already exists`），`002_evaluation_and_idempotency.sql`、`003_trace_step_details.sql` 与 `004_invalidation_event_period.sql` 均连续执行两遍通过（2026-10-26 / 2026-10-27），`@pytest.mark.db` 用例 **34 项全部通过**（2026-10-27，见 [local/README.md](local/README.md)）；端到端导入/读回与判断链路冒烟见 [local/README.md](local/README.md)。**缓存已闭合（本地）**：`redis==8.1.0` 入锁后，`tests/test_cache_redis_live.py` 在本地 Redis 7.4 上实测键写入、`TTL≤300`、二次命中与换键（3 passed）；未闭合部分是云端部署后的命中（云端 API 尚未部署）。**云端** RDS 已于 2026-10-23 在 ECS 上实测连通并完成建表（PostgreSQL 16.13、`vector` 0.8.0、13 张表），**2026-10-26 23:47 补齐 002/003**（`schema_migration` 三行齐全，结构与本地一致；`004_invalidation_event_period.sql` 待 11/08 部署补齐）、Redis 实测 `PONG`；OBS 已购买并建桶，`obs` 路径**2026-10-27 已在真实桶上实测通过**（`-m obs` 5 项，见 §4.3）；模型调用改由 DeepSeek 承担（MaaS 按队长 2026-10-23 决定不再使用），**2026-10-27 已用真实 Key 联调通过**（`-m deepseek` 3 项）——`MODEL_MODE` 仍保持 `fixture` 作为默认值，切到 `deepseek` 由 `.env` 决定，云端切换随 11/08 部署验证。

## 5. 本地开发 → 云端部署检查清单

本地先行开发是计划允许的路径（同一份代码靠环境变量切换），但「11/08 云端受限 Demo 可访问」在关键路径上，11/15 还要求从空环境复现部署。下面 A–F 六组中，**A（账号审批）与 D（域名/备案）不受代码进度控制，必须提前启动**。

### 5.1 环境变量对照（同一份代码，两套取值）

| 变量 | 本地开发 | 云端演示 | 约束 |
| --- | --- | --- | --- |
| `APP_ENV` | `local` | `demo` | `demo` 强制 `DEMO_ACCESS_MODE=restricted`；`local` 强制 `local` |
| `API_HOST` | `127.0.0.1` | `0.0.0.0` 或容器内地址 | `local` 模式只接受回环地址，否则启动失败并报 `API_HOST` |
| `API_PORT` | `8000` | 由反向代理/容器决定 | 1–65535 |
| `DATABASE_URL` | `postgresql://huice:...@127.0.0.1:5432/huice_dev` | RDS **内网**地址 | 缺省或非 `postgresql://` 前缀即启动失败 |
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | `redis://:<密码>@<实例内网>:6379/0` | 需 `redis://`/`rediss://` 前缀 |
| `CORS_ORIGINS` | `http://127.0.0.1:5173` | 前端真实源（逗号分隔） | 拒绝 `*`、带路径或带查询串的源 |
| `DEMO_ACCESS_MODE` | `local`（免 cookie） | `restricted`（另需 `huice_demo_session` cookie） | 两种模式都**不免** Bearer |
| `MODEL_MODE` | `fixture`（或 `deepseek` + 本地 Key） | `fixture`，联调通过后改 `deepseek` | 取值 `fixture`/`deepseek`（原 `maas` 已移除，队长 2026-10-23 决定改用 DeepSeek）。`deepseek` 需 `DEEPSEEK_BASE_URL`/`DEEPSEEK_MODEL`/`DEEPSEEK_API_KEY`，且 base URL 必须是 http(s)；缺项或格式不对即启动失败并只报变量名 |
| `OBJECT_STORE_MODE` | `local` | `obs` | `obs` 需 `OBS_BUCKET`/`OBS_ENDPOINT`/`OBS_AK`/`OBS_SK`，缺任一项启动失败并只报变量名 |
| `LOCAL_OBJECT_DIR` | `.local-objects` | 不使用 | 仅 `local` 模式生效；已 gitignore |
| `ADMIN_TOKEN` | 本地随机值，≥16 位 | 服务端机密，≥16 位 | 只服务端校验，**绝不**作为前端构建变量 |
| `DEMO_SESSION_SECRET` | 可留空（`local` 模式不校验会话） | 服务端机密，≥32 位 | 演示会话 cookie 的独立签名密钥，**必须与 `ADMIN_TOKEN` 不同**；`restricted` 模式缺失、过短或与 `ADMIN_TOKEN` 相同即启动失败并只报变量名 |

切换取值后按第 6 节重启；启动失败时进程只打印变量名，不打印值。

### 5.2 清单

**A. 账号与地域（审批类，最先启动）**

- [x] 开通 RDS for PostgreSQL、Redis、OBS（2026-10-23 完成）；MaaS 与 CodeArts/Skills 按队长 2026-10-23 决定**不再作为要求**；模型调用改用 DeepSeek（公开 API，无需在华为云开通）
- [x] 选定地域并确认：计算、RDS、Redis、OBS 同地域（华南-广州）、同 VPC `huice-vpc`（2026-10-23 完成）
- [ ] 记录配额、计费方式与费用上限
- [ ] 复查日期 2026-10-28，与 `docs/devlog/M1.md` 的阻碍表同步

**B. 代码补齐（本地写不出来，必须先于云端联调）**

- [x] `ObsObjectStore.put()/delete()` 真实实现（`esdk-obs-python` 3.26.6，私有桶、`obs/<uuid>` 命名空间、失败统一映射 503）——**2026-10-27 已在真实桶 `huice-policies` 上实测（`-m obs` 5 passed）**
- [x] DeepSeek 客户端（`MODEL_MODE=deepseek`，`app/integrations/llm.py`）与降级路径：模型只起草措辞与抽取建议，判断一律由确定性规则求值；调用失败时返回已审核规则结果并显式降级，不抛 500。**2026-10-27 已用真实 Key 联调（`-m deepseek` 3 passed）**
- [ ] 契约里的 `maas` 枚举值已改为 `deepseek`（`ModelUsage.mode`、`explanationMode`，契约 `1.0.0-rc.2`），待 M2 复核；前端若按 `maas` 写过字面量需同步改
- [x] `restricted` 模式的 `huice_demo_session`：后端校验 `v1.<过期时间>.<HMAC>` 签名与有效期，任意字符串不再通过（2026-10-24 按 M2 审查 R1 修复）；签发用 `infra/deploy/issue-session.py`，签名密钥为独立的 `DEMO_SESSION_SECRET`（≥32 位，restricted 模式必填，与 `ADMIN_TOKEN` 互不派生，2026-10-26 按 M2 二审 P1 拆分）。**网关仍须负责下发该 cookie 并拦掉绕过网关的请求**，这一层尚未验收
- [ ] CORS 白名单换成真实前端源
- [x] 部署自动化：`infra/deploy/{provision.sh,huice-api.service,nginx-huice.conf}` 取代占位脚本（`deploy.ps1` 未删，仍为占位）

**C. 数据与环境差异**

- [x] RDS 建实例后先验证扩展：`SELECT * FROM pg_available_extension_versions WHERE name = 'vector';`（2026-10-23 有结果，`default_version 0.8.0`）
- [x] 记录 `vector` 版本：云上实际安装 **0.8.0**，本地 **0.8.7**，同为 0.8 系列，未发现语法差异
- [x] `001_init.sql` 在 RDS 上执行成功并登记 `schema_migration`（2026-10-23，13 张表，无超级用户专属语句报错）
- [x] `002_evaluation_and_idempotency.sql` 与 `003_trace_step_details.sql` 已在云 RDS 执行（**2026-10-26 23:47**，`schema_migration` 记 `002` 23:47:03、`003` 23:47:13）；已核对：`trace_step` 的 `input_summary/output_summary/policy_id/policy_version/error_code/human_action` 六列存在、`trace_step_status_check`（`ok/needs_review/retryable_error/fatal_error`）与 `trace_step_stage_check`（九个契约阶段）定义与本地一致、`trace_step_no_update_delete` 触发器仍在
- [ ] `004_invalidation_event_period.sql` 尚未在云 RDS 执行（11/08 部署时补齐，`schema_migration` 应增至四行）；执行后核对 `invalidation_event` 的 `fact_period_start/fact_period_end` 两列、`invalidation_event_fact_period_check` 约束与 `invalidation_event_run_idx` 索引存在
- [ ] 时区复验：库内 UTC，派生 `closed` 使用 `AT TIME ZONE 'Asia/Shanghai'`，用一条已过期政策验证。**注意**：RDS 实例默认 `timezone` 为 `Asia/Shanghai`（`schema_migration.applied_at` 显示 `+08`），与本地容器（UTC）不同；应用侧 `db.py` 每连接 `SET TIME ZONE 'UTC'`，需在首次云端联调时用实际数据确认派生结果一致
- [ ] `storage_key` 命名空间：本地写 `local/<uuid>`，云端写 `obs/...`；**旧 key 不迁移**，演示数据重新导入
- [ ] 演示数据只用合成的企业画像，不复用本地数据卷

**D. 网络、域名与安全**

- [ ] RDS 关闭公网入站，只允许计算节点所在安全组；DCS 不支持安全组，改由**白名单**控制（2026-10-26 已确认 `huice-redis` 公网访问关闭，白名单仅含 ECS 内网 `192.168.0.86`）
- [ ] OBS 桶设为私有
- [ ] 仅暴露必要端口，安全组最小开放
- [ ] 域名与证书：大陆地域用域名走 80/443 需 ICP 备案（周期按周计）；来不及就用弹性公网 IP + 非标端口演示——以华为云实际要求为准，先问清再定
- [ ] 反向代理透传 `X-Forwarded-Proto`；记录证书有效期与续签方式
- [ ] 机密走环境变量/密钥管理，不进镜像、不进 Git、不写日志

**E. 部署与回滚**

- [ ] 从空环境按本文件 + 部署脚本复现（11/15 要求）
- [ ] 记录镜像/依赖版本、目标规格、地域、VPC 与初始化命令
- [ ] 回滚：镜像回退 + 数据库只增不改（新增 `002_*.sql`），不 `DROP` 生产表
- [ ] 健康检查：正常情况下 200；停库后 503 且不含连接串

**F. 验收证据（写进 devlog / 本文件）**

- [ ] 补齐 10/21 逾期项：数据库连接、`vector` 查询、Redis 连通、OBS 测试对象上传/删除、健康检查（MaaS 合成请求按队长 2026-10-23 决定取消；DeepSeek 首次真实调用另记）
- [ ] 11/08 云端受限 Demo 可访问（脱敏截图 + 时间）
- [ ] 11/15 空环境复现记录
- [ ] 全部记录不含密钥、连接串或真实企业材料

### 5.3 时间表

| 时间 | 目标 |
| --- | --- |
| 本周 | 本地继续实现；同时发起账号开通申请 |
| 2026-10-28 | 阻碍复查：OBS 真实桶上传/删除验证；云端部署冒烟（健康检查 200） |
| 11/01–11/05 | 实现 OBS、restricted 两条路径，与云上 RDS 联调 |
| 11/08 | 云端受限 Demo 可访问（计划中的关键路径节点） |
| 11/15 | 从空环境复现部署与脱敏检查 |

### 5.4 已知会造成返工的做法

- 把 OBS endpoint、地域或模型名写死在代码/前端
- 拿本地 `.local-objects` 里的文件当演示数据
- 依赖只在本机成立的假设（固定 5432 端口、Windows 路径分隔符、本地时区）
- 迁移只在本机手工补步骤，脚本本身不足以跑通空环境

## 6. 健康检查

```powershell
# 启动 API（在 huice/ 下）
services\api\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir services/api --host 127.0.0.1 --port 8000

# 正常（2026-10-23 实测：连本地 compose 的 PostgreSQL 16.15）
curl.exe -i http://127.0.0.1:8000/api/v1/health          # 200 {"status":"ok","version":"1.0.0-rc.2"} + X-Trace-Id

# 停掉数据库后（实测：docker compose stop db 后返回 503，X-Trace-Id 与 body.traceId 一致）
curl.exe -i http://127.0.0.1:8000/api/v1/health          # 503 {"error":{"code":"DEPENDENCY_UNAVAILABLE",...}}
```

健康检查不鉴权、不返回连接串或主机名。502/503 只表示依赖不可用，不代表进程异常。

## 7. 部署（文件已就绪，尚未在云上执行）

方式：**venv + systemd + nginx**，文件在 [deploy/](deploy/)，步骤见 [deploy/README.md](deploy/README.md)。

| 文件 | 作用 |
| --- | --- |
| `deploy/provision.sh` | 在 ECS 上装包、建 `huice` 用户与 venv、装依赖（华为云 PyPI 镜像）、装 unit 与 nginx 配置 |
| `deploy/huice-api.service` | uvicorn 只绑 `127.0.0.1:8000`，`Restart=always`，`EnvironmentFile=/etc/huice/api.env` |
| `deploy/nginx-huice.conf` | 80 端口反代，`client_max_body_size 60m`，透传 `X-Forwarded-*` |

未采用 Docker：ECS 直连 Docker Hub 大概率超时，而 2C4G 用 venv 已够，少一层镜像加速配置。

`infra/deploy/deploy.ps1` 未删除，仍是占位文件；本阶段以 `deploy/README.md` 的手工步骤为准。

**执行前提**：RDS 与 Redis 已实测（本页 §4.1），云端库迁移已到 **003**（`schema_migration` 三行齐全，2026-10-26 补齐），部署时需再补 `004_invalidation_event_period.sql`。MaaS 已不在范围内，模型侧首轮部署用 `MODEL_MODE=fixture`（DeepSeek 尚未用真实 Key 联调）；OBS 未在真实桶验证前保持 `OBJECT_STORE_MODE=local`。

上云前的准备项、环境变量对照与目标时间表见第 5 节；本地先行开发不等于可以推迟到 11/14 再部署——计划的云端节点是 11/08。

## 8. 回退

1. 停止 API：结束 uvicorn 进程（Ctrl+C）。
2. 数据服务：`docker compose ... down`（保留卷）或 `down -v`（连数据删除）。
3. 配置回退：恢复 `huice/.env` 上一版本；不要提交 `.env`。
4. 代码回退：按提交粒度 `git revert <commit>`，不使用 `reset --hard` 覆盖他人分支。
5. 数据库：本阶段不提供自动降级；如需回退结构，按依赖逆序 `DROP TABLE`，并同步删除 `schema_migration` 对应行。
