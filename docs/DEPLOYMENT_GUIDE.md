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
- 改完 `systemctl restart huice-api`，先看服务起来没有：`systemctl is-active huice-api`、`/api/v1/health` 200。

- 验证门禁确实关了。**只有「Origin 正确 + 请求体合法」时才该看到 `401`**；请求体不合法是 `422`、Origin 缺失或不匹配是 `403 ORIGIN_MISMATCH`，这两种不代表切换失败：

  ```bash
  API=http://127.0.0.1:8000
  ORIGIN=https://<站点地址>        # 必须等于 PUBLIC_ORIGIN

  # 1) 门禁已关：合法请求体 + 正确 Origin + 错凭据 → 401 INVALID_CREDENTIALS
  curl -s -o /dev/null -w 'login=%{http_code}\n' -X POST $API/api/v1/auth/login \
    -H 'Content-Type: application/json' -H "Origin: $ORIGIN" \
    -d '{"username":"nobody.check","password":"wrong-password-123"}'

  # 2) Origin 校验仍在（不是门禁）：403 ORIGIN_MISMATCH
  curl -s -o /dev/null -w 'no-origin=%{http_code}\n' -X POST $API/api/v1/auth/login \
    -H 'Content-Type: application/json' \
    -d '{"username":"nobody.check","password":"wrong-password-123"}'

  # 3) 请求体不合法：422 VALIDATION_ERROR
  curl -s -o /dev/null -w 'bad-body=%{http_code}\n' -X POST $API/api/v1/auth/login \
    -H 'Content-Type: application/json' -H "Origin: $ORIGIN" -d '{}'

  # 4) 未登录没有身份：401 UNAUTHENTICATED（local 下不是 200）
  curl -s -o /dev/null -w 'policies=%{http_code}\n' $API/api/v1/policies
  ```

  期望依次是 `401`、`403`、`422`、`401`。前三条都不再是 `403 DEMO_SESSION_REQUIRED`，就说明门禁关了；第 1 条只能证明「进到了身份校验」，不能证明账号可用，账号可用与否要在浏览器里登一次。

切换前先处理三条副作用：

1. **会话 cookie 的 `Secure` 由站点源决定，不由 `APP_ENV` 决定**。`PUBLIC_ORIGIN` 是 `https://` 时会话 cookie 一律带 `Secure`（`session_cookie_attributes`：`app_env != "local"` 或源是 https），浏览器不会把它发往明文 `http://`；纯 http 的本机开发仍不带 `Secure`，否则浏览器不保存。切换后请在浏览器 DevTools → Application → Cookies 里确认 `huice_session` 的 `Secure` 已勾上。

   **不要把 80 端口跳转当防泄露手段**：301 是服务器收到请求之后才回的，明文请求连同 cookie 已经发出去了，挡不住泄露；它只改善「用户输 `http://` 时看到什么」。真正的控制是两条：① 公网不暴露明文入口——安全组不放行 80，或 nginx 只 `listen 443`；② https 站点上的会话 cookie 必须带 `Secure`（上一条）。确实需要 80 只做跳转时：

   ```nginx
   server {
       listen 80;
       listen [::]:80;
       server_name _;
       return 301 https://$host$request_uri;
   }
   ```

   改完 `nginx -t && systemctl reload nginx`。仓库模板 `infra/deploy/nginx-huice.conf` 是反代形态（纯 http 部署也要用它），所以这一步在切 `local` 时是手工改动，恢复 `restricted` 后可按需要还原。**但这段跳转只能在「已登录浏览器的迁移」第 5 条的条件满足后才装回去**：旧会话没清空之前，80 应保持关闭（连跳转也不要开），否则尚未重登的用户手里的非 `Secure` cookie 仍会被明文带出。
2. **企业注册入口对公网开放**；审核员注册仍受 `REVIEWER_SIGNUP_KEY` 约束。
3. **演示门禁与 demo 配额失效**：未登录请求的身份是 `None`，公开列表也要登录后才能读。

**已登录浏览器的迁移**：新代码只影响**之后签发**的 cookie。服务器上已经存在的旧会话 cookie 不会因为部署而变成 `Secure`，在用户重新登录或清除之前，浏览器会继续把它发往明文的 `http://站点/api/...`。迁移顺序固定：

1. **先关明文入口**：安全组不放行 80，或 nginx 只 `listen 443`。必须在部署前完成——它是旧 cookie 的可控边界，301 跳转不算。
2. **先做一次「旧会话仍有效」的确认，再停旧服务**：用一台升级前已登录的浏览器经 `https://` 正常打开站点、能读到数据（此时仍是旧代码、会话未被撤销）。这一步必须放在停服之前——撤销之后同一个浏览器只会拿到 `401`，那时再验就分不清「撤销生效」和「会话本来就没用了」。确认完执行 `systemctl stop huice-api`。停了才不会有旧代码继续签发 cookie，边界取在后面才覆盖得到全部旧会话。
3. 部署新代码（**先不启动**，冒烟留到第 6 步）。
4. **从数据库时钟取边界 `T`**，然后只撤销「`T` 之前创建」的旧会话：

   ```bash
   systemd-run --pipe --wait --collect -p EnvironmentFile=/etc/huice/api.env \
     /opt/huice/venv/bin/python /opt/huice/app/infra/deploy/revoke-sessions.py --print-boundary
   ```

   输出形如 `2026-11-25T16:00:00.123456+08:00`（微秒 + 时区），下面替换 `<T>`。

   **不要用 `date -Iseconds` 取边界**：它只有秒精度，旧服务在同一秒内（`16:00:00.5`）签发的会话满足不了 `created_at < T`，会被漏掉，统计就会错误地显示清零；而且 `created_at` 由数据库时间产生，用服务器系统时钟取 `T` 还会叠加两台机器的时钟偏差。`--print-boundary` 读的是同一台数据库的 `clock_timestamp()`，与 `created_at` 同源。

   ```bash
   # 两次都用 systemd-run + --pipe --wait --collect，才能把输出拿回来
   systemd-run --pipe --wait --collect -p EnvironmentFile=/etc/huice/api.env \
     /opt/huice/venv/bin/python /opt/huice/app/infra/deploy/revoke-sessions.py \
     --before <T>                                                                        # 统计
   systemd-run --pipe --wait --collect -p EnvironmentFile=/etc/huice/api.env \
     /opt/huice/venv/bin/python /opt/huice/app/infra/deploy/revoke-sessions.py \
     --before <T> --apply                                                                # 撤销
   ```

   **为什么必须带 `--before`**：用户重新登录会**新建**带 `Secure` 的会话；按「全部活跃会话」统计，那个数只会越来越多，「旧会话清零」永远不成立。脚本只处理 `revoked_at IS NULL AND expires_at > now()` **且** `created_at < T` 的会话（不删行，与登出同语义），`--apply` 才写库，`--username <账号>` 可再收窄到某个人。`--before` 必须带时区，不带时区脚本直接退出 2。

5. **启动新服务**并做 §4.9 的冒烟。
6. **迁移验收**：至少两类浏览器——升级前已登录的、以及从未登录过的。「撤销前旧会话仍有效」这一条已在第 2 步做完，这里只验撤销之后的结果：
   - 第 4 步撤销、第 5 步启动新服务后，同一台浏览器请求 `GET /api/v1/policies` 返回 `401 SESSION_INVALID`，页面跳登录页；
   - 重新登录后 DevTools → Application → Cookies 里 `huice_session` 的 `Secure` 已勾上；
   - 从任意机器 `curl -s -o /dev/null -w '%{http_code}\n' http://<站点>/api/v1/policies` 连接不成功（既不是 301 也不是 200）。

   再用 SQL 直接核边界两侧（`created_at < T` 就是旧会话、`≥ T` 是新会话，不需要按秒窗口猜——停服前一秒创建的会话同样落在 `< T` 一侧）：

   ```sql
   -- ① 边界之前的旧会话是否已清空：应为 0
   SELECT count(*) FROM user_session
   WHERE created_at < '<T>'::timestamptz
     AND revoked_at IS NULL AND expires_at > now();

   -- ② 边界之后的会话概况（只作信息，不作判据）
   SELECT count(*) AS new_sessions,
          min(created_at) AS first_new,
          count(*) FILTER (WHERE revoked_at IS NOT NULL) AS revoked_later
   FROM user_session WHERE created_at >= '<T>'::timestamptz;
   ```

   ① 不为 0 说明撤销没覆盖完整，不能重开 80。② 只用来确认新代码已在签发会话（`new_sessions` > 0）以及发现边界取早：`first_new` 应当晚于第 5 步新服务的启动时刻，**若它更早**，说明边界取早了（或旧服务没停干净），有旧代码签发的会话被划进「新会话」一侧而没被撤销，需重新停服、重取 `T` 再做第 4 步。

   **`revoked_later` 不为 0 是正常的，不作判据**：用户登出、修改密码、重新登录都会让应用撤销会话（登出与改密都只写 `revoked_at`）。用它当「脚本误伤新会话」的证据，会把正常操作误判成迁移失败。

   **要确认脚本只动边界前的会话，用受控样本，并且锁到「本次新增的那一条」**。不能用「该账号下已撤销会话数为 0」来判：第 4 步撤销的旧会话也在同一个账号名下，那样查必然非零，会把正常迁移判成失败。

   1. 选一个受控账号，先记下它已有的会话（这些旧会话可能已被第 4 步撤销）：

      ```sql
      SELECT s.id, s.created_at FROM user_session s JOIN app_user u ON u.id = s.user_id
      WHERE u.username = '<受控账号>' ORDER BY s.created_at;
      ```

   2. 用该账号登录一次，取出**本次新增**的那条会话（创建时刻在边界之后）：

      ```sql
      SELECT s.id, s.created_at FROM user_session s JOIN app_user u ON u.id = s.user_id
      WHERE u.username = '<受控账号>' AND s.created_at >= '<T>'::timestamptz
      ORDER BY s.created_at DESC LIMIT 1;
      ```

      与第 1 步的记录比对，确认这个 `id` 是新出现的；取不到唯一一条就记为「未验证」，不要拿账号下的历史行凑判据。

   3. 执行脚本前确认这条会话仍是活跃状态：

      ```sql
      SELECT revoked_at FROM user_session WHERE id = '<会话ID>';   -- 应为 NULL
      ```

   4. 重跑 `revoke-sessions.py --before <T> --apply`（幂等，边界前已清空时应输出「撤销 0 条」），再按同一个 `id` 查一次，`revoked_at` 仍应为 `NULL`。

   **第 3–4 步之间**不要对该账号登出、改密或再次登录——这三种操作都会撤销它自己的会话，让结果失去意义。全程只用会话 `id`，不需要输出 Cookie 或令牌。第 4 步确认完再退出该账号即可（退出会撤销它自己的会话，不影响已经得出的结论）。

7. **什么时候才能重新开放 80**：判据是「`T` 之前创建的旧会话」清空——即 `revoke-sessions.py --before <T>` 统计返回 `0 条`。**不要**用「全部活跃会话为 0」：用户重登后会产生新的 `Secure` 会话，那个数不为 0 是正常的，按它作判据就永远开不了 80。另外两处容易想错：
   - **撤销服务端会话不会删除浏览器里的 cookie**。它只让那条 cookie 在服务端失效（请求变成 `401 SESSION_INVALID`），浏览器仍会保存它、继续按原属性携带。所以「撤销完就能开 80」是错的——还要等用户重登（新 cookie 带 `Secure`）或手动清掉旧 cookie；
   - **不撤销也能收敛，但前提是 80 一直关着**：`T` 之前的旧会话最迟到 `SESSION_TTL_HOURS`（默认 12 小时）自然过期，期间用户可以照常用 https；重登后的新会话带 `Secure`，不受影响，也不会把 `--before <T>` 的计数顶上去。
8. **用户侧通知**（演示/公开环境建议直接发一条）：请退出登录、清除本站 cookie 后重新登录；此后会话只经 https 携带。

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
