# 部署指导（M2 交接）

本文面向接手部署的人，说明「惠策协同」在单台华为云 ECS 上的标准部署方式。更细的运维记录与历史排障见 [`infra/deploy/README.md`](../infra/deploy/README.md)；两者冲突时以本文件和当前代码为准。

## 1. 架构与边界

```
浏览器 ──HTTPS(443)──> nginx ──/api/v1/──> uvicorn 127.0.0.1:8000 ──> PostgreSQL(RDS)
                          └──静态文件──> /opt/huice/web
                                                        └──> Redis（可选）
                                                        └──> OBS / 本地对象目录
```

- API 只绑回环，公网只能经 nginx 进来。
- 单机、单 worker；不要开多 worker（生命周期锁与采集证据按单进程设计）。
- 数据库、Redis、OBS 由外部提供；本仓库不创建这些云资源。
- 前端与后端同源部署：前端构建时必须把 API 基地址设为 `/api/v1`。

## 2. 前置条件

| 项 | 要求 |
| --- | --- |
| 操作系统 | Ubuntu 22.04/24.04（其他发行版需自行调整 systemd/nginx） |
| Python | 3.12，使用 `/opt/huice/venv` |
| PostgreSQL | 16+，建议启用 pgvector；库与账号由 RDS 提供 |
| Node.js | 24（仅构建前端，不装在生产机也可） |
| nginx | 1.18+，负责 443 静态站点与 `/api/v1/` 反代 |
| Redis | 可选；`REDIS_URL` 配了才启用求值缓存 |
| 对象存储 | `OBJECT_STORE_MODE=local` 或 `obs` |

## 3. 本地开发与测试

本地环境用于开发和联调，不连演示库，也不使用生产 `api.env`。

### 3.1 数据服务

```bash
cp infra/local/.env.example infra/local/.env   # 填本机口令，别提交
docker compose --env-file infra/local/.env -f infra/local/docker-compose.yml up -d
```

该 Compose 只启动 PostgreSQL 16 + pgvector 和 Redis 7，端口只绑 `127.0.0.1`。

### 3.2 后端

```bash
cd services/api
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.lock   # Windows
# Linux/macOS: .venv/bin/python -m pip install -r requirements.lock
```

本地配置从 `.env.example` 复制为 `.env`（不要提交），至少设置 `DATABASE_URL`；演示/受限模式的相关变量见第 4.3 节。数据库迁移按 `001`—`013` 顺序执行，或参考 [infra/local/README.md](../infra/local/README.md)。

```bash
.venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### 3.3 前端

```bash
cd apps/web
cp .env.example .env.local        # VITE_API_BASE_URL=http://127.0.0.1:8000/api/v1
npm ci
npm run dev
```

本地演示可用首页的 `?mode=demo` 合成样例；它不依赖演示门禁 cookie。生产构建必须用 `/api/v1`（见第 4.6 节）。

### 3.4 测试与契约检查

```bash
# 后端：无库用例
cd services/api && .venv/Scripts/python -m pytest -q

# 含真实库/Redis
export HUICE_TEST_DATABASE_URL=postgresql://...   # 指向可清空的测试库
export HUICE_TEST_REDIS_URL=redis://...
.venv/Scripts/python -m pytest -q

# 前端
cd apps/web && npm run build && npm test

# 契约与类型
python contracts/generate_types.py --check
```

CI（`.github/workflows/ci.yml`）会跑后端 + 契约 + 前端构建/测试 + 数据 CSV 检查；提交前建议本地至少跑后端全量与 `npm run build`。

## 4. 首次部署

### 4.1 放代码

代码目录固定为 `/opt/huice/app`。推荐用 `git bundle`（ECS 直连 GitHub 常被拦）：

```bash
# 本机
git bundle create /tmp/huice.bundle main
scp /tmp/huice.bundle root@<ECS_IP>:/tmp/

# ECS
git clone /tmp/huice.bundle /opt/huice/app
cd /opt/huice/app && git checkout -f main
```

如果目标目录已有手工文件而不是 git 检出，先备份再覆盖：

```bash
tar czf /root/app-before-$(date +%F-%H%M).tgz -C /opt/huice/app .
```

### 4.2 虚拟环境

```bash
python3 -m venv /opt/huice/venv
/opt/huice/venv/bin/python -m pip install -U pip
/opt/huice/venv/bin/python -m pip install -r /opt/huice/app/services/api/requirements.lock
/opt/huice/venv/bin/python -m pip check
```

### 4.3 环境变量

运行时配置放 `/etc/huice/api.env`，权限 `0640 root:huice`。**不要把真实值写进仓库、文档或聊天记录。**

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `APP_ENV` | 是 | `local` 或 `demo`；`demo` 与 `restricted` 绑定、`local` 与 `local` 绑定（见 §4.7.1） |
| `API_HOST` / `API_PORT` | 是 | `127.0.0.1` / `8000` |
| `DATABASE_URL` | 是 | PostgreSQL 连接串；不要贴进命令行参数 |
| `ADMIN_TOKEN` | 是 | 运维 Bearer，≥16 位；只读后台接口 |
| `DEMO_SESSION_SECRET` | 是（restricted） | ≥32 位，演示门禁签名密钥 |
| `DEMO_ACCESS_MODE` | 是 | `local` 或 `restricted`；公网长期用 `restricted`，演示期可临时切 `local`（见 §4.7.1） |
| `CORS_ORIGINS` | 是 | 前端真实源，禁止 `*` |
| `PUBLIC_ORIGIN` | 建议 | 登录/注册的 Origin 校验；必须与访问地址完全一致 |
| `MODEL_MODE` | 是 | `fixture` 或 `deepseek` |
| `OBJECT_STORE_MODE` | 是 | `local` 或 `obs` |
| `OBS_BUCKET` / `OBS_ENDPOINT` / `OBS_AK` / `OBS_SK` | obs 模式必填 | 只落服务器 |
| `REDIS_URL` | 可选 | 不配则走无缓存路径 |
| `REVIEWER_SIGNUP_KEY` | 可选 | 配了才允许自助注册审核员 |
| `RUN_MAX_DURATION_SECONDS` | 可选 | 默认 7200；中断扫描阈值 |
| `RUN_LIFECYCLE_CONTRACT_ENABLED` | 可选 | 默认 0；与契约窗口同窗开启 |
| `COLLECTION_JOURNAL_DIR` | 可选 | 默认 `/var/lib/huice`；指标覆盖证据目录 |

> 取值时用 `systemd-run -p EnvironmentFile=/etc/huice/api.env`，不要 `source` 整个文件——shell 与 systemd 对引号、`$`、反引号的解析不同，可能读到不同的连接串。

### 4.4 数据库迁移

服务首次启动前或停机时执行：

```bash
bash /opt/huice/app/infra/deploy/migrate.sh
```

- 脚本只执行 `schema_migration` 里**未登记**的迁移，顺序固定 `001`→`013`；已登记的跳过。
- 失败会停在失败项之前；修好后重跑即可断点续。
- 最后会打印 13 个迁移 id 是否全部登记；**按名字核对，不要只数总数**。
- 服务未启动时脚本用 systemd 自身解析 `api.env`；不要手工 `psql` 重放旧 SQL（`005` 在库已推进时会炸）。

### 4.5 第一个审核员

全新库没有审核员，注册接口也不会放行。由服务器 root 建第一个审核员：

```bash
read -r -p "username: " ADMIN_INIT_USERNAME
read -r -s -p "password: " ADMIN_INIT_PASSWORD; echo
umask 077
printf 'ADMIN_INIT_USERNAME=%s\nADMIN_INIT_PASSWORD=%s\n' \
  "$ADMIN_INIT_USERNAME" "$ADMIN_INIT_PASSWORD" > /run/huice-admin-init.env
systemd-run --pipe --wait --collect \
  -p EnvironmentFile=/etc/huice/api.env \
  -p EnvironmentFile=/run/huice-admin-init.env \
  /opt/huice/venv/bin/python /opt/huice/app/infra/deploy/create-admin.py
rm -f /run/huice-admin-init.env
unset ADMIN_INIT_PASSWORD
```

口令不要写进 `api.env`，也不要留在 shell 历史里。之后用登录页的「修改密码」改掉初始口令。

### 4.6 前端构建与 nginx

前端在构建机执行（Windows 用 `cmd.exe //c`，Git Bash 会把 `/api/v1` 转成 `D:/Git/api/v1`）：

```bat
cd apps\web
cmd.exe //c "set VITE_API_BASE_URL=/api/v1&& npm ci&& npm run build"
```

Linux/macOS：

```bash
cd apps/web
VITE_API_BASE_URL=/api/v1 npm ci
VITE_API_BASE_URL=/api/v1 npm run build
```

打包并部署（用原子替换，避免 `scp -r` 在目标下多套一层 `dist/`）：

```bash
tar czf /tmp/web.tar.gz -C apps/web/dist .
scp /tmp/web.tar.gz root@<ECS_IP>:/tmp/

# ECS
rm -rf /opt/huice/web.new && mkdir -p /opt/huice/web.new
tar xzf /tmp/web.tar.gz -C /opt/huice/web.new
chown -R huice:huice /opt/huice/web.new
mv /opt/huice/web /opt/huice/web.before-$(date +%F-%H%M) 2>/dev/null || true
mv /opt/huice/web.new /opt/huice/web
```

nginx 需要：

- `443` 站点根目录 `/opt/huice/web`，SPA 回退到 `index.html`；
- `/api/v1/` 反代到 `127.0.0.1:8000`，保留 `Host`、`X-Forwarded-*`；
- `index.html` 不缓存，`/assets/` 长缓存。

模板与脚本：`infra/deploy/nginx-huice-site.conf`、`infra/deploy/enable-tls.sh --web-root /opt/huice/web`。

### 4.7 演示门禁

`DEMO_ACCESS_MODE=restricted` 时，所有请求（包括登录）先要 `huice_demo_session`：

```bash
systemd-run --pipe --wait --collect -p EnvironmentFile=/etc/huice/api.env \
  -p WorkingDirectory=/opt/huice/app \
  /opt/huice/venv/bin/python /opt/huice/app/infra/deploy/issue-session.py --hours 12
```

输出 `value:` 与 `Set-Cookie:`。每个浏览器/设备配一次即可，不需要每个用户登录都配；过期或换设备再签。不要把这个值写进文档或仓库。

#### 4.7.1 演示期临时切成 local（免 cookie）

如果要求「打开站点就到了登录页、不配任何 cookie」，可以把门禁临时关掉：`/etc/huice/api.env` 里 `APP_ENV=local`、`DEMO_ACCESS_MODE=local`。

- 两个变量**必须成对改**：`APP_ENV=demo` 强制 `restricted`、`APP_ENV=local` 强制 `local`，只改一个服务会启动失败（只报变量名）。`local` 模式还要求 `API_HOST` 是回环地址。
- 改完 `systemctl restart huice-api`。验证：`/api/v1/health` 200；不带 cookie 的 `POST /api/v1/auth/login` 返回 `401`（身份校验失败），而不是 `403 DEMO_SESSION_REQUIRED`。注意不带 cookie 的 `GET /api/v1/policies` 在 `local` 下是 `401`，不是 `200`——未登录没有身份。

切换前先处理三条副作用：

1. **会话 cookie 不再带 `Secure`**（`local` 模式下 `session_cookie_attributes` 置 `secure=False`）。切之前把 80 端口改成跳转，否则明文 `/api/v1` 会成为 cookie 泄露口：

   ```nginx
   server {
       listen 80;
       listen [::]:80;
       server_name _;
       return 301 https://$host$request_uri;
   }
   ```

   改完 `nginx -t && systemctl reload nginx`。仓库模板 `infra/deploy/nginx-huice.conf` 是反代形态（纯 http 部署也要用它），所以这一步在切 `local` 时是手工改动，恢复 `restricted` 后可按需要还原。
2. **企业注册入口对公网开放**；审核员注册仍受 `REVIEWER_SIGNUP_KEY` 约束。
3. **演示门禁与 demo 配额失效**：未登录请求的身份是 `None`，公开列表也要登录后才能读。

回滚：改回 `APP_ENV=demo` + `DEMO_ACCESS_MODE=restricted`（或还原 `/etc/huice/api.env` 的备份）后重启；切回后每个浏览器都要按本节重新配演示 cookie。

### 4.8 定时任务

```bash
cp /opt/huice/app/infra/deploy/huice-discovery.{service,timer} /etc/systemd/system/
cp /opt/huice/app/infra/deploy/huice-collection-heartbeat.{service,timer} /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now huice-discovery.timer huice-collection-heartbeat.timer
```

- `huice-discovery.timer`：按配置周期抓取政策候选（需迁移 `010`）。
- `huice-collection-heartbeat.timer`：每 5 分钟写采集生命周期日志（需 `013` 与 `/var/lib/huice` 权限）。
- 同一套库只能有一套定时器；两套部署同时跑会重复抓取、重复写采集证据。

### 4.9 启动与冒烟

```bash
cp /opt/huice/app/infra/deploy/huice-api.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now huice-api
sleep 2
curl -s http://127.0.0.1:8000/api/v1/health
curl -sk -o /dev/null -w '%{http_code}\n' -H 'Host: <ECS_IP>' https://127.0.0.1/
```

期望 `health` 返回 `{"status":"ok","version":"…"}`，HTTPS 首页 200。

### 4.10 日期来源回填与审核基线

首次上线还要建立两类历史基线：

1. **日期来源（迁移 `011` 之后）**：先干跑，核对匹配版本与绑定文件，M3 复核后 `--apply` 只写旁表：

```bash
systemd-run --pipe --wait --collect -p EnvironmentFile=/etc/huice/api.env   -p WorkingDirectory=/opt/huice/app   /opt/huice/venv/bin/python /opt/huice/app/infra/deploy/backfill-date-provenance.py
# 复核数据文件 reviewStatus/reviewEvidence 后加 --apply
```

2. **审核事件基线（迁移 `012` 之后）**：给当时已发布版本补 `actor_kind='baseline'` 事件，只用于日终待办回放，不计审核完成：

```bash
systemd-run --pipe --wait --collect -p EnvironmentFile=/etc/huice/api.env   -p WorkingDirectory=/opt/huice/app   /opt/huice/venv/bin/python /opt/huice/app/infra/deploy/backfill-review-baseline.py
# 确认「已发布版本 N 个，将补 N 条」后再加 --apply
```

两个脚本都幂等；执行前后各留一次行数与关键时间，写进部署记录。云端采集起点 `run_lifecycle_from` 由服务启动自动写入，不要手工改。

### 4.11 备份、日志与保留

- 数据库：变更前 `pg_dump`，文件名带日期；转储只留服务器，不入仓库/聊天。
- 代码：更新前打包 `services/api`、`contracts`；前端保留上一版 `/opt/huice/web.before-*`。
- 配置：`/etc/huice/api.env` 单独备份到受限目录，权限 `0600`。
- 采集日志：`/var/lib/huice/collection-lifecycle-<host>.jsonl` 需 `huice` 组可读写；不要手工编辑，链断后导入器会拒绝整份日志。
- 日志查看：`journalctl -u huice-api`、`journalctl -u huice-discovery`；确认不含堆栈、连接串或密钥。

## 5. 更新部署

1. 备份：`services/api`、`contracts` 与 `/opt/huice/web` 各打一份 tar。
2. 后端：`git bundle` 上云 → clone 到 `/tmp` → `tar` 覆盖 `services/api contracts` → `chown -R huice:huice`。
3. 迁移：有新迁移才跑 `migrate.sh`；没有就跳过。
4. 前端：重新构建（务必带 `VITE_API_BASE_URL=/api/v1`）→ 原子替换 `/opt/huice/web`。
5. `systemctl restart huice-api` → 按第 6 节验收。
6. 不要用 `git checkout` 直接改 `/opt/huice/app`（它通常不是 git 检出）；不要覆盖 `/etc/huice/api.env`。

## 6. 验收清单

- `/api/v1/health` 200，`version` 与 `contracts/openapi.yaml` 的 `info.version` 一致。
- `https://<域名>/` 200，`index.html` 引用的 JS/CSS 均 200。
- 登录 200，`/auth/me` 返回当前用户。
- 企业画像读写、创建判断、报告下载、轨迹各成功一次。
- 审核员能读指标与完整度；删除事实接口返回 200 或预期 4xx。
- `journalctl -u huice-api -n 50` 无堆栈、无连接串、无密钥。
- `schema_migration` 已登记到最新编号。

## 7. 回滚

```bash
# 后端
tar xzf /root/api-before-<时间>.tgz -C /opt/huice/app
# 前端
rm -rf /opt/huice/web && mv /opt/huice/web.before-<时间> /opt/huice/web
systemctl restart huice-api
```

迁移是向前追加的：回滚代码不等于回滚库；除非明确评估过，不要删表/删列。

## 8. 常见问题

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `/health` 503 `DEPENDENCY_UNAVAILABLE` | 数据库不可达/欠费/白名单 | 查 RDS 状态与安全组；不要先怀疑 DSN 格式 |
| 登录页「无法连接服务」 | 前端构建没设 `VITE_API_BASE_URL=/api/v1`，产物指向 `127.0.0.1:8000` | 用第 4.6 节命令重新构建并替换 |
| 页面「演示入口已失效」（仅 `restricted`） | 门禁 cookie 缺失/过期 | 重新 `issue-session.py`，按浏览器设置 cookie；`local` 部署不会出现这条 |
| 80 端口返回 JSON 404 | 站点走 443；80 默认 server 落到 API | 用 `https://` 访问；切 `local` 模式时应把 80 改成跳转（见 §4.7.1） |
| 登录/注册 403 `ORIGIN_MISMATCH` | `PUBLIC_ORIGIN` 与浏览器实际访问的源不一致（常见：写成 `http://` 而站点是 `https://`，或留空后取到 `CORS_ORIGINS` 第一项） | 按访问地址改 `PUBLIC_ORIGIN`（scheme + host，不带路径）后重启 |
| 迁移后仍报缺表 | 某迁移执行失败但未登记 | 看 `migrate.sh` 输出与 `schema_migration`，修好重跑 |
| `python3: Permission denied`（Windows） | WindowsApps 的 `python3` 别名 | 用 `python` 或在 Git Bash 里显式指定解释器 |

## 9. 安全清单

- `api.env` 权限 `0640 root:huice`；`ADMIN_TOKEN` 与 `DEMO_SESSION_SECRET` 不得进前端构建变量。
- 数据库、OBS 的密钥只落服务器；日志与报错不得包含连接串。
- SSH 只对运维 IP 开放；ECS 安全组只放 80/443。
- 演示门禁只解决「谁能访问演示」，不替代账号鉴权；企业数据仍按归属过滤。
- 备份文件含业务数据，按敏感数据保管，不上传仓库或聊天工具。

## 10. 关于自动化部署工具

当前仓库没有采用一键部署技能。若评估第三方工具（例如单机 ECS 部署技能），注意它们通常只负责「新建机器 + 上传代码 + 启动进程」，**不会**自动完成本项目的：

- 数据库迁移（`001`—`013`）与首轮基线；
- systemd 定时器（发现、采集心跳）与 `/var/lib/huice` 采集日志；
- 443 同源站点、证书、`/api/v1/` 反代与 SPA 回退；
- OBS、演示门禁、审核员初始化等环境配置。

这类工具默认还会新建 ECS/EIP/安全组并产生费用，且只清理自己 Terraform state 里的资源。当前 huice 已有生产式部署，**不要直接用自动化工具覆盖**；需要并行验证时，按「新机器 → 接同一 RDS → 切 DNS/EIP → 观察 → 停旧机」的蓝绿流程做。
