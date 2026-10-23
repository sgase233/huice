# 本地数据服务（PostgreSQL 16 + pgvector、Redis 7）

本目录提供本地开发用的 `docker-compose.yml`，只服务 M1 的接口开发与联调，不代表云端拓扑。云端资源仍未开通，见 [../README.md](../README.md)。

## 前置条件

1. Docker Desktop 4.x 及以上，且引擎已启动（`docker version` 能连上 `Server`）。
2. WSL2 已安装且默认版本为 2（Windows）：`wsl --status` 显示 `默认版本: 2`。本机 2026-10-23 已满足（WSL 3.0.3.0、内核 6.18.54.1、Docker Desktop 4.96.3）。
3. 端口 `5432`、`6379` 未被占用：

```powershell
Get-NetTCPConnection -LocalPort 5432,6379 -ErrorAction SilentlyContinue |
  Select-Object LocalAddress,LocalPort,OwningProcess
```

## 首次启动

```powershell
cd huice\infra\local
Copy-Item .env.example .env
# 编辑 .env，把 POSTGRES_PASSWORD 设为本地开发专用密码（不要提交，已被 .gitignore 排除）
docker compose --env-file .env -f docker-compose.yml up -d
docker compose --env-file .env -f docker-compose.yml ps
```

镜像固定 tag，本机 2026-10-23 拉取到的 digest 如下（换机器时以实际拉取结果为准）：

| 镜像 | digest |
| --- | --- |
| `pgvector/pgvector:pg16` | `sha256:7b822b0aac60967beb1ea5e576b8602c94c300a157d187f385ae3e0da199b90a` |
| `redis:7.4-alpine` | `sha256:57e4255cc971b1c8d6f8a6ca283ac01bc3ecdd914273ef6bfaede87aaecd6541` |

国内网络直连 Docker Hub 常失败（`dial tcp ... registry-1.docker.io:443` 超时）。若拉取报错，在 Docker Desktop → Settings → Docker Engine 的 JSON 里加镜像加速，Apply & restart：

```json
"registry-mirrors": [
  "https://docker.m.daocloud.io",
  "https://docker.1ms.run",
  "https://docker.xuanyuan.me"
]
```

验证加速生效：`docker info | grep -A3 "Registry Mirrors"`。

## 初始化数据库

```powershell
# 1) 数据库版本
docker compose --env-file .env -f docker-compose.yml exec db psql -U huice -d huice_dev -c "SELECT version();"

# 2) pgvector 可用性
docker compose --env-file .env -f docker-compose.yml exec db psql -U huice -d huice_dev -c "CREATE EXTENSION IF NOT EXISTS vector;"
docker compose --env-file .env -f docker-compose.yml exec db psql -U huice -d huice_dev -c "SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';"

# 3) 建表（容器内 /sql 只读挂载了 infra/sql）
docker compose --env-file .env -f docker-compose.yml exec db psql -U huice -d huice_dev -v ON_ERROR_STOP=1 -f /sql/001_init.sql

# 4) 迁移记录与表数量
docker compose --env-file .env -f docker-compose.yml exec db psql -U huice -d huice_dev -c "SELECT id, description, applied_at FROM schema_migration;"
docker compose --env-file .env -f docker-compose.yml exec db psql -U huice -d huice_dev -c "\dt"
```

`001_init.sql` 自带 `BEGIN/COMMIT`、`IF NOT EXISTS` 与 `CREATE OR REPLACE`，可重复执行；重复执行不会重建数据。

## 检查 Redis

```powershell
docker compose --env-file .env -f docker-compose.yml exec redis redis-cli PING
```

预期输出 `PONG`。Redis 未设密码，因为端口只绑 `127.0.0.1`；若改绑其它网卡，必须同时配置 `requirepass` 并同步 `huice/.env` 的 `REDIS_URL`。

## 与 API 的连接

`huice/.env` 中的 `DATABASE_URL` 必须与本目录 `.env` 的库名/用户/密码一致，例如：

```
DATABASE_URL=postgresql://huice:<本地密码>@127.0.0.1:5432/huice_dev
REDIS_URL=redis://127.0.0.1:6379/0
```

## 数据库用例

```powershell
cd huice\services\api
$env:HUICE_TEST_DATABASE_URL = "postgresql://huice:<本地密码>@127.0.0.1:5432/huice_dev"
.\.venv\Scripts\python.exe -m pytest -m db -q
```

用例在独立 schema（`huice_test_<pid>`）中建表，结束即删除，不影响 `public` 下的开发数据。

## 清理测试数据

```powershell
# 只清开发数据、保留结构
docker compose --env-file .env -f docker-compose.yml exec db psql -U huice -d huice_dev `
  -c "TRUNCATE policy, enterprise RESTART IDENTITY CASCADE;"

# 连数据卷一起清空（会丢失全部本地数据）
docker compose --env-file .env -f docker-compose.yml down -v
```

## 停止

```powershell
docker compose --env-file .env -f docker-compose.yml stop
docker compose --env-file .env -f docker-compose.yml down   # 保留命名卷
```

## 验收输出（2026-10-23 实测）

本机环境：Windows 11 Home China 10.0.26200、WSL 3.0.3.0（默认版本 2，内核 6.18.54.1）、Docker Desktop 4.96.3、docker CLI 29.9.2、Compose v5.6.1。以下为实际执行结果：

| 检查项 | 实测输出 |
| --- | --- |
| `SELECT version()` | `PostgreSQL 16.15 (Debian 16.15-1.pgdg12+2) on x86_64-pc-linux-gnu` |
| `pg_extension` 含 `vector` | `vector | 0.8.7` |
| `001_init.sql` 首次执行 | 无错误，退出码 0 |
| `001_init.sql` 第二次执行 | 仅 `NOTICE: ... already exists, skipping`，退出码 0（幂等通过） |
| 业务表数量 | `information_schema` 中 `public` 基础表 13 张 |
| 触发器数量 | `public` 下 24 个 |
| `schema_migration` | `001_init | 政策/来源/条款、企业画像、评估运行、失效事件与追踪表 | 2026-10-23 06:11:52.557794+00` |
| `redis-cli PING` | `PONG` |
| `pytest -m db -q` | `12 passed`（真实 PostgreSQL 上执行，不再是 skipped） |
| 端到端冒烟 | API-04 导入 201（`ETag` 等于 `revision`，`status=pending_parse`）；API-15 列表 200 且无 `ETag`；API-16 按版本与不带版本均命中同一 `revision`；未知版本 404 `POLICY_NOT_FOUND` |

冒烟数据在 `public` 下，验证后已用 `TRUNCATE ... CASCADE` 清空，`schema_migration` 记录保留。原始记录同步进 [../../docs/devlog/M1.md](../../docs/devlog/M1.md)。

## 本机历史阻碍（已解除）

2026-10-23 上午曾因 WSL2 未启用、Docker Desktop 安装被文件占用（`0x80070020`）阻塞，所有 db 用例处于 skipped。同日完成 `wsl --install --no-distribution`、安装 Docker Desktop 4.96.3、配置镜像加速后解除，上表为解除后的实测结果。

替代路径（仍有效）：使用已开通的云 RDS PostgreSQL（需支持 pgvector）或本机安装的 PostgreSQL 16 + pgvector，把 `DATABASE_URL` 指向该实例；此时本目录的 compose 不参与，其余步骤相同。

## 回退

1. `docker compose --env-file .env -f docker-compose.yml down`（保留卷）或 `down -v`（连卷删除）。
2. 若改过 `huice/.env`，恢复 `DATABASE_URL`/`REDIS_URL` 到上一可用值。
3. 数据库结构回退需逐个 `DROP TABLE`，本阶段不提供自动回滚脚本；`001_init.sql` 只增不改，破坏性变更应新增 `002_*.sql`。
