# ECS 部署（venv + systemd + nginx）

本目录是 M1 的云端部署方式：代码放 `/opt/huice/app`，`systemd` 守护 uvicorn，`nginx` 监听 80 反代到 `127.0.0.1:8000`。API 进程**只绑回环**，公网只能经由 nginx 进来。

| 文件 | 安装位置 | 作用 |
| --- | --- | --- |
| `huice-api.service` | `/etc/systemd/system/` | 守护 uvicorn，`Restart=always`、开机自启 |
| `nginx-huice.conf` | `/etc/nginx/conf.d/` | 监听 80，转发 `/`，`client_max_body_size 60m` |
| `provision.sh` | 在 ECS 上执行 | 装包、建用户、建 venv、装依赖、装配置并启动 |
| `enable-tls.sh` | 在 ECS 上执行（`provision.sh` 之后再跑） | 签自签证书、装 443 站点配置、失败回滚（§3.2）；`--web-root` 时改装同源站点（§3.3） |
| `nginx-huice-tls.conf` | 由 `enable-tls.sh` 装到 `/etc/nginx/conf.d/` | 监听 443（自签证书），转发 `/`，代理参数同 80 |
| `nginx-huice-site.conf` | 同上，`enable-tls.sh --web-root` 时改装这一份 | 443 同源站点：前端静态文件 + `/api/v1/` 反代（与上一行**互斥**，同一个文件名） |
| `/etc/huice/api.env` | 由 `provision.sh` 生成模板 | 全部环境变量与密钥，权限 `640 root:huice` |

## 1. 上传代码

在 **Windows 侧 Git Bash** 打包（排除虚拟环境、依赖、本地对象与 `.env`，避免把密钥传上云）：

```bash
cd "/d/Study/比赛/华为ICT大赛"
tar --exclude=huice/services/api/.venv \
    --exclude=huice/apps/web/node_modules \
    --exclude=huice/.git \
    --exclude=huice/.local-objects \
    --exclude=huice/.env \
    --exclude=huice/infra/local/.env \
    --exclude='*.pyc' \
    -czf /tmp/huice-src.tar.gz huice

scp /tmp/huice-src.tar.gz root@139.9.33.46:/tmp/
```

在 ECS 上解包：

```bash
mkdir -p /opt/huice/app
tar -xzf /tmp/huice-src.tar.gz -C /opt/huice/app --strip-components=1
ls /opt/huice/app/services/api/requirements.lock
```

> 这一份是**未提交快照**。11/15 的「从空环境复现部署」必须改用已推送的提交重新做一遍，不能拿这份充当证据。

## 2. 安装

```bash
bash /opt/huice/app/infra/deploy/provision.sh
```

脚本可重复执行，不会覆盖已存在的 `/etc/huice/api.env`。它会先装 `python3-dev` / `build-essential`——`crcmod`（`esdk-obs-python` 的依赖）在 PyPI 没有 Linux 预编译包，缺 gcc 会在编译阶段失败。

脚本还会创建 `${APP_ROOT}/.local-objects` 并交给 `huice` 用户：unit 用 `ProtectSystem=strict`，只对这一个路径开放写权限，而只读父目录下服务自己 `mkdir` 会失败。自定义目录用 `HUICE_OBJECT_DIR=... bash provision.sh`，同时要改 `huice-api.service` 的 `ReadWritePaths`。

依赖从华为云 PyPI 镜像安装（`repo.huaweicloud.com`），避免 ECS 直连 pypi.org 超时；需要换源时加环境变量：

```bash
PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple bash /opt/huice/app/infra/deploy/provision.sh
```

### 2.1 数据库结构必须先于服务启动（硬前提）

代码直接读写 `invalidation_event.fact_period_start/fact_period_end`（`004_invalidation_event_period.sql` 引入），且企业事实的键/单位要与契约 `1.0.0-rc.4` 起的 **18 键**字典对齐（`005` 由 10 键扩到 16 键并引入 `ratio`，`006` 再由 16 键扩到 18 键、新增 `specificMarketSince`/`independentLegalEntity`），**服务启动前云 RDS 必须已执行 001—006 全部迁移**：

```bash
for f in 001_init 002_evaluation_and_idempotency 003_trace_step_details 004_invalidation_event_period 005_fact_dictionary_extension 006_fact_dictionary_extension_rc4; do
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "/opt/huice/app/infra/sql/$f.sql" || break
done
psql "$DATABASE_URL" -tAc "SELECT id FROM schema_migration ORDER BY id"   # 应输出六行
```

漏掉 `006` 不会在启动时报错，而是在**写入地方通知企业事实时**才炸：`enterprise_fact_key_check` 与 `enterprise_fact_key_unit` 仍是 16 键版本，`specificMarketSince`/`independentLegalEntity` 会被约束直接拒绝（500），而 SRDI-001、RDSUB-001/002 正好要用这两键。

顺序反了不会「降级运行」，而是直接报错：`PATCH /enterprises/{id}/facts/{key}` 的失效事件写入与 `GET /assessment-runs/{runId}` 的事件读取都会因缺列抛 `UndefinedColumn`，分别表现为 500 与历史判断读不出来。反向顺序（先迁移后升级代码）安全，脚本本身可重复执行。

## 3. 填配置

```bash
vi /etc/huice/api.env
chown root:huice /etc/huice/api.env && chmod 640 /etc/huice/api.env
```

云端演示的取值（密码只在服务器上填，不进仓库、不进文档）：

| 变量 | 取值 |
| --- | --- |
| `APP_ENV` | `demo` |
| `API_HOST` / `API_PORT` | `127.0.0.1` / `8000`（与 unit 一致） |
| `DATABASE_URL` | `postgresql://root:<密码>@192.168.0.80:5432/huice` |
| `REDIS_URL` | `redis://:<密码>@192.168.0.38:6379/0`（也可用控制台「连接地址」的域名形式 `redis://:<密码>@redis-4be07660-....dcs.myhuaweicloud.com:6379/0`；2026-10-26 在 ECS 上实测 `--askpass PING` → `PONG`） |
| `CORS_ORIGINS` | 前端真实源；前端未部署前用 `http://139.9.33.46` |
| `DEMO_ACCESS_MODE` | `restricted`（`APP_ENV=demo` 时强制） |
| `ADMIN_TOKEN` | ≥16 位随机串，仅服务端使用；只用于后台 Bearer |
| `DEMO_SESSION_SECRET` | ≥32 位随机串，仅服务端使用；**必须与 `ADMIN_TOKEN` 不同**（取值相同同样启动失败），用于签发与校验演示会话 cookie。restricted 模式必填，缺失、过短或与 `ADMIN_TOKEN` 相同时启动失败并只报变量名 |
| `MODEL_MODE` | `fixture`（首轮部署；联调通过后改 `deepseek`，此时还需 `DEEPSEEK_BASE_URL`/`DEEPSEEK_MODEL`/`DEEPSEEK_API_KEY`） |
| `OBJECT_STORE_MODE` | `local` 或 `obs`（`obs` 还需 `OBS_BUCKET`/`OBS_ENDPOINT`/`OBS_AK`/`OBS_SK`） |
| `LOCAL_OBJECT_DIR` | `.local-objects`（`obs` 模式下不生效） |

```bash
systemctl restart huice-api
```

### 3.1 演示会话 cookie（`restricted` 模式）

`APP_ENV=demo` 时 `/api/v1/admin/*` 除了 Bearer 还要求签名未过期的 `huice_demo_session`；任意字符串不再通过。签发：

```bash
set -a; . /etc/huice/api.env; set +a      # 仅为取到 DEMO_SESSION_SECRET，注意当前 shell 会持有它
/opt/huice/venv/bin/python /opt/huice/app/infra/deploy/issue-session.py --hours 12
```

**必须用虚拟环境的解释器**：脚本会导入 `app.security`，后者在模块级导入 FastAPI，而依赖只装在 `/opt/huice/venv`。用系统 `python3` 运行会直接抛 `ModuleNotFoundError`（脚本会打印提示并以退出码 2 结束，而不是抛堆栈）。

输出两行：cookie 值和可直接使用的 `Set-Cookie` 头。把 `Set-Cookie` 配到网关/演示入口（或在浏览器开发者工具里手工设置 cookie），之后带着它访问后台接口。

签名密钥是**独立的 `DEMO_SESSION_SECRET`**，与后台 Bearer 使用的 `ADMIN_TOKEN` 互不派生：持有 Bearer 不能签发会话，持有会话密钥也不能调用后台接口。两个值都要按机密对待，**不要把密钥或输出写进日志、文档或群聊**。

### 3.2 自签 HTTPS（评审演示，ICP 备案前的临时方案）

`restricted` 模式的会话 cookie 带 `Secure`，浏览器在 `http://<EIP>`（非 localhost）**不会保存**它，页面会一直 403 `DEMO_SESSION_REQUIRED`。备案下来之前，用自签证书把 443 打开：

```bash
sudo bash /opt/huice/app/infra/deploy/enable-tls.sh 139.9.33.46
```

- 脚本用 IP SAN（`subjectAltName=IP:...`）签一年期自签证书到 `/etc/huice/tls/huice.{crt,key}`，装 `nginx-huice-tls.conf`，`nginx -t` 通过后重载。私钥权限 600，只落服务器，**不进仓库、不贴聊天**。
- **装配置前先备份同目录下的既有 443 配置**（`huice-api-tls.conf.bak`）；`nginx -t` 不通过时自动把备份恢复回去，原本没有 443 配置就删掉新装的那份，然后退出。重跑脚本最坏情况是「还是原来那份」，不会变成「原配置没了、新配置又不通」。脚本只动 443 这一份站点配置，80 的 `huice-api.conf` 不碰。
- **华为云安全组需含 TCP 443 入方向**（`infra/README.md` 的规则清单里已列；实例上如果没有，补一条），否则外网访问超时——表现为「连不上」，不是证书问题。
- 已有证书默认保留，避免重跑脚本把在演示的证书换掉；换 EIP 或要重签时加 `--force`，有效期用 `TLS_DAYS` 调整。
- 浏览器第一次访问 `https://<EIP>` 会拦「您的连接不是私密连接」：高级 → 继续前往。过完这次，同一浏览器配置后续不再拦；换电脑、换无痕窗口会再来一次。
- **自签证书不提供可信身份**：它只解决 `Secure` cookie 无处可落的问题，浏览器的地址栏仍标「不安全」，也没有任何第三方能验证这台服务器是谁。评审演示可以，别用它承载真实企业数据。
- **443 的这份配置只代理 API**（`/` → `127.0.0.1:8000`），不含前端静态文件。要让 `https://<EIP>` 直接打开前端页面，加 `--web-root` 重跑脚本，见 §3.3。
- **只支持前端与 API 同源的拓扑**。会话 cookie 是 `SameSite=Lax`，只在同一个站点（同一注册域名、同一协议）的请求上发送：前端和 API 都在 `https://<EIP>` 下时正常；前端挂在别的域名（本地开发服务器 `http://localhost:5173` 调云端 API 也算）就是跨站，浏览器**不会**带这个 cookie——即使 `CORS_ORIGINS` 里填了那个源、预检也通过，接口照样 403 `DEMO_SESSION_REQUIRED`。`CORS_ORIGINS` 只决定浏览器允不允许读响应，决定不了 cookie 发不发。站点判定不看端口：同域名换端口仍是同站，但那是跨源，那个源照样要列进 `CORS_ORIGINS`。评审演示请把前端与 API 放在同一个源下。
- 写会话 cookie 只走受控入口：把 §3.1 输出的 `Set-Cookie` 配到演示网关，由服务端下发（带 `HttpOnly; Secure; SameSite=Lax`）；没有网关时，用 F12 → Application → Cookies → `https://<EIP>` 新建 `huice_demo_session`，值取 §3.1 的 `value:`，勾 `Secure`、`HttpOnly`、`SameSite=Lax`，Path `/`。**不要**在页面 Console 里用 `document.cookie` 写：那样写进去的 cookie 不带 `HttpOnly`，页面里任何脚本都能把它读走。
- 前端页面在 https 下不能调 http 接口，否则被浏览器当混合内容拦掉。
- 80 端口保留不动，§4 的健康检查与 curl 冒烟不受影响。

验证：

```bash
curl -k -i https://127.0.0.1/api/v1/health     # ECS 上
curl -k -i https://139.9.33.46/api/v1/health   # 自己电脑上，期望 200 + X-Trace-Id
```

### 3.3 同源静态站点（前端与 API 共用一个 443）

`restricted` 模式要求前端与 API 同源（§3.2 末段），演示时把前端构建产物也挂到 443 上，`/api/v1/` 反代到本机 API：

下面 1)～3) 在**仓库根目录**执行（`git rev-parse --show-toplevel` 所在的目录；Windows 侧用 Git Bash，与 §2 一致），4) 在 ECS 上执行：

```bash
# 1) 前端构建在 M2 侧做：构建时必须把 API 指到相对路径，
#    否则页面打包进去的仍是 http://127.0.0.1:8000/api/v1，打开后一片请求失败。
#    cd 放在子 shell 里，执行完仍停在仓库根目录，后面的相对路径不受影响。
(cd apps/web && npm ci && VITE_API_BASE_URL=/api/v1 npm run build)

# 2) 只打包 dist 的内容：-C ... . 不带 dist/ 这一层，解包后才正好是站点根目录里的
#    index.html 与 assets/；包写在系统临时目录，构建产物不进仓库。
PACK="$(mktemp -d)/web-dist.tar.gz"
tar -czf "$PACK" -C apps/web/dist .
tar -tzf "$PACK" | head   # 自查：应为 ./index.html、./assets/...，没有 dist/ 前缀

# 3) 上传后在 ECS 上解包到站点根目录；首次部署与更新用的是同一条命令。
scp "$PACK" root@139.9.33.46:/tmp/web-dist.tar.gz
ssh root@139.9.33.46 'mkdir -p /opt/huice/web && tar -xzf /tmp/web-dist.tar.gz -C /opt/huice/web && chmod -R o+rX /opt/huice/web'
```

在 ECS 上：

```bash
# 4) 装上同源站点（证书已存在会保留，不会重签）
sudo bash /opt/huice/app/infra/deploy/enable-tls.sh 139.9.33.46 --web-root /opt/huice/web
```

- **别用 `scp -r apps/web/dist root@<host>:/opt/huice/web` 拷产物**：目标已存在时它会在 `/opt/huice/web` 下再建一层 `dist/`，而 nginx 的 `root` 是 `/opt/huice/web`，于是更新后仍在服务旧版本；目标不存在时又变成「把 dist 另存为 `/opt/huice/web`」，首次与更新两种情形结果还不一样。上面的 tar 写法把内容直接铺到根目录，两种情形同一条命令、结果一致。
- 装的是 `nginx-huice-site.conf`：`root /opt/huice/web`、SPA 回退 `try_files $uri $uri/ /index.html`、`/api/v1/` 反代 `127.0.0.1:8000`、`client_max_body_size 60m` 与 80/443 现有配置一致。
- **它与「只反代 API」的那份写到同一个文件名**（`/etc/nginx/conf.d/huice-api-tls.conf`）：给 `--web-root` 就是同源站点，不给就退回仅 API。两份都是 `listen 443` + `server_name _`，同时安装会触发 `conflicting server name "_"`，脚本靠「只写一个文件名」避免这种并存。
- **已装成同源站点后，不带 `--web-root` 重跑会被拦下**：那等于把 443 切回「仅反代 API」，前端页面随即 404，而 `/api/v1/` 照旧正常，现场很容易误判成前端故障。只重装或续证书请带上原来的 `--web-root`（提示里会给出当前配置里的目录）；确实要切回仅 API 才加 `--force`，此时脚本会打印一条警告。
- `/assets/` 下是带内容哈希的构建产物，缓存 30 天；`index.html` 明确 `no-cache`，发布后刷新即可拿到新的资源清单。资源缺失时直接 404，不会退回 `index.html`。
- 前端目录必须是绝对路径，且只用字母、数字与 `/ _ . -`（空格、分号会破坏 nginx 语法，竖线和 `&` 会被 sed 当成分隔符/替换标记），脚本先校验再安装；`nginx -t` 不通过时仍按 §3.2 的备份回滚。
- 同源部署下请求不再跨源，`CORS_ORIGINS` 不再是必经环节；填 `https://<EIP>` 也无害。
- 静态文件只在 443 上；80 端口仍只反代 API，用 `http://<EIP>` 打不开前端页面。
- 换前端版本时重复 1→3（解包覆盖 `index.html` 与新的哈希资源即可，配置不用改）；4) 只在首次安装或换目录时才要重跑。旧哈希的 `/assets/` 文件留在原地不影响访问，要清理就先 `rm -rf /opt/huice/web/assets` 再解包。

验证分两种情形，都在 ECS 上做：

```bash
# 首次部署（/opt/huice/web 原本不存在）
ls /opt/huice/web/index.html /opt/huice/web/assets     # 两个都在
ls /opt/huice/web/dist                                 # 应报 No such file or directory：没有多套一层
curl -k -s -o /dev/null -w '%{http_code}\n' https://127.0.0.1/          # 200

# 更新（web 根已存在，换过一版前端：先记下资源名，重跑 1)～3) 后再看）
grep -o 'assets/index-[^"]*\.js' /opt/huice/web/index.html              # 资源名变了
curl -k -s https://127.0.0.1/ | grep -o 'assets/index-[^"]*\.js'        # 页面引用的是新资源
ls /opt/huice/web/dist                                                  # 仍不存在
```

两者最后都再确认一次：`curl -k -i https://127.0.0.1/` 返回 `index.html`，`curl -k -i https://127.0.0.1/api/v1/health` 仍是 200。

2026-11-04 在 ECS 上按这两种情形各跑过一次：首次部署后 `/opt/huice/web` 下直接是 `index.html` + `assets/`、`/opt/huice/web/dist` 不存在；模拟换一版（改资源名）解包到已存在的站点根目录后，`index.html` 引用的是新资源名、`/` 与 `/assets/<新文件>` 均 200、仍未生成 `dist/`，验完已还原原产物。

## 4. 验证

ECS 本机：

```bash
curl -i http://127.0.0.1:8000/api/v1/health      # 期望 200 + X-Trace-Id
curl -i http://127.0.0.1/                        # 经 nginx，期望同上
```

公网（在你自己电脑上）：

```bash
curl -i http://139.9.33.46/api/v1/health
```

健康检查内部会对 RDS 执行 `SELECT 1`，**返回 200 即证明应用→RDS 的内网链路通**；返回 503 `DEPENDENCY_UNAVAILABLE` 说明数据库连不上，先看 `journalctl -u huice-api`（日志不含连接串）。

## 5. 已知限制（M1）

- `DEMO_ACCESS_MODE=restricted` 要求请求带 `huice_demo_session` cookie。**签发脚本已实现**（`infra/deploy/issue-session.py`，用法见 §3.1）；云端真实链路已于 2026-11-02 验证（健康检查 200、401/403 分层、写路径冒烟通过）。仍待自动化的是**网关侧的 cookie 下发**——目前要按 §3.2 由受控入口下发（临时可在 DevTools 里手工添加，见 §3.2 最后几条）；`Secure` 所需的 TLS 由 §3.2 的自签 443 提供。前端与 API 必须同源（§3.2 的 `SameSite=Lax` 限制；同源站点的装法见 §3.3）。在注入 cookie 之前，云端 `/api/v1/admin/*` 会返回 403 `DEMO_SESSION_REQUIRED`，只有 `/api/v1/health` 可用——这不是部署故障。
- `OBJECT_STORE_MODE=obs` 已于 2026-10-27 在真实桶上联调通过（`-m obs` 5 项，见 `infra/README.md` §4.3）；首轮部署仍先留在 `local`，填好 `OBS_BUCKET`/`OBS_ENDPOINT`/`OBS_AK`/`OBS_SK` 后再切 `obs`，切换后按 §4 复测一次导入。
- 未配置域名与正式证书：大陆地域用域名走 80/443 需要 ICP 备案，当前直接用 EIP 演示，评审期临时走 §3.2 的自签 443；备案后换正式证书并去掉浏览器的自签警告。
- `infra/deploy/deploy.ps1` 仍是占位文件，本阶段以本文档的手工步骤为准。

## 6. 回滚

```bash
systemctl stop huice-api          # 停服务
cp /opt/huice/app.bak/* /opt/huice/app/   # 恢复上一份代码（若事先备份）
systemctl restart huice-api
```

数据库只增不改：新变更按 `infra/sql/` 现有最大序号顺延（当前最大为 `006_fact_dictionary_extension_rc4.sql`，下一份是 `007_*.sql`），不 `DROP` 已有表、不改历史脚本。配置回退恢复 `/etc/huice/api.env` 上一版本即可。

## 7. 安全约束

- `/etc/huice/api.env` 含数据库与 Redis 密码、OBS 密钥，权限必须是 `640 root:huice`，不得复制到仓库目录、不得贴进聊天记录。
- `ADMIN_TOKEN` 只做服务端校验，不作为任何前端构建变量。
- 启动失败时日志只输出变量名；若看到具体值，按缺陷处理。
- nginx 只暴露 80 与评审期自签的 443（§3.2），ECS 安全组不额外开放 8000。
