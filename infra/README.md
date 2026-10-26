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

后端依赖的真实版本见 `services/api/requirements.lock`（41 项，`pip freeze` 生成）。其中 FastAPI 0.161.1、Starlette 1.9.0、Pydantic 2.14.1、psycopg 3.3.6（+ pool 3.3.3）、pypdf 6.21.2、uvicorn 0.54.0。

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

# 云 RDS 或本机 PG
psql "<连接串>" -v ON_ERROR_STOP=1 -f infra/sql/001_init.sql
```

`infra/sql/001_init.sql` 建 13 张表（policy、policy_version、policy_source、policy_criterion、policy_validation_finding、enterprise、enterprise_fact、assessment_run、assessment、assessment_dependency、invalidation_event、trace_step、schema_migration）、启用 `vector` 扩展、创建 updated_at / 已发布版本保护 / 只追加表三类触发器，并在末尾写入 `schema_migration('001_init', ...)`。脚本开 `BEGIN/COMMIT`、全部使用 `IF NOT EXISTS`/`CREATE OR REPLACE`，可重复执行。

迁移约定：新变更写 `002_*.sql`，不改历史脚本；每条迁移同样登记 `schema_migration`。

## 4. 云端资源状态（截至 2026-10-23）

### 4.1 已开通

| 资源 | 参数 | 备注 |
| --- | --- | --- |
| VPC / 子网 | `huice-vpc`（`192.168.0.0/16`）、`huice-subnet`（`192.168.0.0/24`，251 个可用 IP） | 华南-广州；子网网段创建后不可修改 |
| ECS | `huice-api-01`，2 vCPU / 4 GiB，Ubuntu 24.04.4 LTS（自带 Python 3.12.3），按需计费 | 华南-广州 / **可用区7**；内网 `192.168.0.86`；公网 EIP `139.9.33.46`；2026-10-23 实测 `lsb_release`、`python3 --version`、`ip -4 addr` |
| 安全组（入方向） | TCP 22 / 80 / 443，源 `0.0.0.0/0` | 22 已实测可连（2026-10-23）；80/443 暂无服务监听，属正常 |
| 登录方式 | 密码（用户 `root`） | 密码不入文档；建议后续换成密钥对 |
| CodeArts / Skills | 本人 2026-10-23 告知已开通（未在控制台复核） | **本项目不接入**：队长 2026-10-23 决定，开发过程记录以实际使用的编码工具（Claude Code，见 `docs/devlog/M1.md`）为准 |
| Redis（DCS） | **已购买并实测连通（2026-10-23）**：`huice-redis`，Redis 7.0（`INFO server` 报 `redis_version:7.x`），**主备**，1 GB，内网 `192.168.0.38:6379` | 在 ECS `huice-api-01` 上 `AUTH` 返回 `OK`、`PING` 返回 `PONG`；密码只进服务器 `.env`，不入库不入文档 |
| RDS for PostgreSQL | **已购买、实测连通并完成建表（2026-10-23）**：PostgreSQL **16.13**，VPC `huice-vpc` / 子网 `huice-subnet`，端口 5432，内网 `192.168.0.80`，库名 `huice` | ECS 上 `psql` 连接成功；`pg_available_extensions` 有 `vector`（`default_version 0.8.0`，本地 0.8.7，同为 0.8 系列）；`001_init.sql` 以 `-v ON_ERROR_STOP=1` 执行，`BEGIN`→37 条 DDL→`INSERT 0 1`→`COMMIT` 无报错，建出 **13 张表、13 个业务触发器**（脚本内 `CREATE OR REPLACE TRIGGER` 13 条；**2026-10-24 已用 `WHERE NOT tgisinternal` 复核：`public` 下业务触发器 13 个，`pg_trigger` 全量 61 行，其余 48 行为外键内部触发器**；早期记的「24 个」为未注明的旧口径，已作废），`schema_migration` 记 `001_init`，`pg_extension.extversion = 0.8.0` |
| OBS（对象存储） | **已购买并建桶（2026-10-23）**：桶 `huice-policies`，**私有**，区域 `cn-south-1`（华南-广州），endpoint `obs.cn-south-1.myhuaweicloud.com` | 区域级服务，不进 VPC；`ObsObjectStore` 已用 `esdk-obs-python` 3.26.6 实现（`obs/<uuid>` 命名空间、失败映射 503），**尚未在真实桶上传/删除验证**；AK/SK 只存于 ECS 的 `/etc/huice/api.env` |
| Docker Desktop + WSL2（本地） | WSL 3.0.3.0 + Docker Desktop 4.96.3 | 已解除，本地 compose 可用 |

### 4.2 未完成

| 资源 | 状态 | 待办 | 负责人 | 复查日期 |
| --- | --- | --- | --- | --- |
| DeepSeek 模型调用 | **已接入，未用真实 Key 联调**（2026-10-23）：`MODEL_MODE=deepseek` 走 `app/integrations/llm.py`，OpenAI 兼容 `/chat/completions`，429→`UPSTREAM_RATE_LIMITED`、其余失败→503 并降级为规则结果 | 拿到 Key 后设 `DEEPSEEK_BASE_URL`/`DEEPSEEK_MODEL`/`DEEPSEEK_API_KEY`，在 ECS 上跑一次真实调用；未联调前云端保持 `MODEL_MODE=fixture` | M1 | 2026-10-28 |

MaaS 按队长 2026-10-23 决定不再使用：华为云账号未开通 MaaS，模型调用改由 DeepSeek 承担，故不再有华为云侧的待办。

⚠️ 公网 EIP 按小时计费，与是否被访问无关；演示结束或暂停开发时应解绑/释放，避免空转扣费。

验证缺口（本地已闭合）：`001_init.sql` 已在真实 PostgreSQL 16.15 上执行两次（第二次仅 `NOTICE ... already exists`），12 个 `@pytest.mark.db` 用例全部通过，端到端导入/读回见 [local/README.md](local/README.md)。**云端** RDS 已于 2026-10-23 在 ECS 上实测连通并完成建表（PostgreSQL 16.13、`vector` 0.8.0、13 张表、`schema_migration` 记 `001_init`）、Redis 实测 `PONG`；OBS 已购买并建桶，`obs` 路径**尚未在真实桶上验证**；MaaS 不再需要（队长 2026-10-23 决定），模型调用改由 DeepSeek 承担——客户端代码与单测已完成，**尚未用真实 Key 联调**，故 `MODEL_MODE` 暂时仍为 `fixture`。

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
| `DEMO_SESSION_SECRET` | 可留空（`local` 模式不校验会话） | 服务端机密，≥32 位 | 演示会话 cookie 的独立签名密钥，**必须与 `ADMIN_TOKEN` 不同**；`restricted` 模式缺失或过短即启动失败并只报变量名 |

切换取值后按第 6 节重启；启动失败时进程只打印变量名，不打印值。

### 5.2 清单

**A. 账号与地域（审批类，最先启动）**

- [x] 开通 RDS for PostgreSQL、Redis、OBS（2026-10-23 完成）；MaaS 与 CodeArts/Skills 按队长 2026-10-23 决定**不再作为要求**；模型调用改用 DeepSeek（公开 API，无需在华为云开通）
- [x] 选定地域并确认：计算、RDS、Redis、OBS 同地域（华南-广州）、同 VPC `huice-vpc`（2026-10-23 完成）
- [ ] 记录配额、计费方式与费用上限
- [ ] 复查日期 2026-10-28，与 `docs/devlog/M1.md` 的阻碍表同步

**B. 代码补齐（本地写不出来，必须先于云端联调）**

- [x] `ObsObjectStore.put()/delete()` 真实实现（`esdk-obs-python` 3.26.6，私有桶、`obs/<uuid>` 命名空间、失败统一映射 503）——**代码已完成，尚未在真实桶上验证**
- [x] DeepSeek 客户端（`MODEL_MODE=deepseek`，`app/integrations/llm.py`）与降级路径：模型只起草措辞与抽取建议，判断一律由确定性规则求值；调用失败时返回已审核规则结果并显式降级，不抛 500。**代码与单测已完成，尚未用真实 Key 联调**
- [ ] 契约里的 `maas` 枚举值已改为 `deepseek`（`ModelUsage.mode`、`explanationMode`，契约 `1.0.0-rc.2`），待 M2 复核；前端若按 `maas` 写过字面量需同步改
- [x] `restricted` 模式的 `huice_demo_session`：后端校验 `v1.<过期时间>.<HMAC>` 签名与有效期，任意字符串不再通过（2026-10-24 按 M2 审查 R1 修复）；签发用 `infra/deploy/issue-session.py`，签名密钥为独立的 `DEMO_SESSION_SECRET`（≥32 位，restricted 模式必填，与 `ADMIN_TOKEN` 互不派生，2026-10-26 按 M2 二审 P1 拆分）。**网关仍须负责下发该 cookie 并拦掉绕过网关的请求**，这一层尚未验收
- [ ] CORS 白名单换成真实前端源
- [x] 部署自动化：`infra/deploy/{provision.sh,huice-api.service,nginx-huice.conf}` 取代占位脚本（`deploy.ps1` 未删，仍为占位）

**C. 数据与环境差异**

- [x] RDS 建实例后先验证扩展：`SELECT * FROM pg_available_extension_versions WHERE name = 'vector';`（2026-10-23 有结果，`default_version 0.8.0`）
- [x] 记录 `vector` 版本：云上实际安装 **0.8.0**，本地 **0.8.7**，同为 0.8 系列，未发现语法差异
- [x] `001_init.sql` 在 RDS 上执行成功并登记 `schema_migration`（2026-10-23，13 张表，无超级用户专属语句报错）
- [ ] 时区复验：库内 UTC，派生 `closed` 使用 `AT TIME ZONE 'Asia/Shanghai'`，用一条已过期政策验证。**注意**：RDS 实例默认 `timezone` 为 `Asia/Shanghai`（`schema_migration.applied_at` 显示 `+08`），与本地容器（UTC）不同；应用侧 `db.py` 每连接 `SET TIME ZONE 'UTC'`，需在首次云端联调时用实际数据确认派生结果一致
- [ ] `storage_key` 命名空间：本地写 `local/<uuid>`，云端写 `obs/...`；**旧 key 不迁移**，演示数据重新导入
- [ ] 演示数据只用合成的企业画像，不复用本地数据卷

**D. 网络、域名与安全**

- [ ] RDS/Redis 关闭公网入站，只允许计算节点所在安全组
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

**执行前提**：RDS 与 Redis 已实测（本页 §4.1），且 `001_init.sql` 已在云库执行完成。MaaS 已不在范围内，模型侧首轮部署用 `MODEL_MODE=fixture`（DeepSeek 尚未用真实 Key 联调）；OBS 未在真实桶验证前保持 `OBJECT_STORE_MODE=local`。

上云前的准备项、环境变量对照与目标时间表见第 5 节；本地先行开发不等于可以推迟到 11/14 再部署——计划的云端节点是 11/08。

## 8. 回退

1. 停止 API：结束 uvicorn 进程（Ctrl+C）。
2. 数据服务：`docker compose ... down`（保留卷）或 `down -v`（连数据删除）。
3. 配置回退：恢复 `huice/.env` 上一版本；不要提交 `.env`。
4. 代码回退：按提交粒度 `git revert <commit>`，不使用 `reset --hard` 覆盖他人分支。
5. 数据库：本阶段不提供自动降级；如需回退结构，按依赖逆序 `DROP TABLE`，并同步删除 `schema_migration` 对应行。
