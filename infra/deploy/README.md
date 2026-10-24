# ECS 部署（venv + systemd + nginx）

本目录是 M1 的云端部署方式：代码放 `/opt/huice/app`，`systemd` 守护 uvicorn，`nginx` 监听 80 反代到 `127.0.0.1:8000`。API 进程**只绑回环**，公网只能经由 nginx 进来。

| 文件 | 安装位置 | 作用 |
| --- | --- | --- |
| `huice-api.service` | `/etc/systemd/system/` | 守护 uvicorn，`Restart=always`、开机自启 |
| `nginx-huice.conf` | `/etc/nginx/conf.d/` | 监听 80，转发 `/`，`client_max_body_size 60m` |
| `provision.sh` | 在 ECS 上执行 | 装包、建用户、建 venv、装依赖、装配置并启动 |
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
| `REDIS_URL` | `redis://:<密码>@192.168.0.38:6379/0` |
| `CORS_ORIGINS` | 前端真实源；前端未部署前用 `http://139.9.33.46` |
| `DEMO_ACCESS_MODE` | `restricted`（`APP_ENV=demo` 时强制） |
| `ADMIN_TOKEN` | ≥16 位随机串，仅服务端使用 |
| `MODEL_MODE` | `fixture`（首轮部署；联调通过后改 `deepseek`，此时还需 `DEEPSEEK_BASE_URL`/`DEEPSEEK_MODEL`/`DEEPSEEK_API_KEY`） |
| `OBJECT_STORE_MODE` | `local` 或 `obs`（`obs` 还需 `OBS_BUCKET`/`OBS_ENDPOINT`/`OBS_AK`/`OBS_SK`） |
| `LOCAL_OBJECT_DIR` | `.local-objects`（`obs` 模式下不生效） |

```bash
systemctl restart huice-api
```

### 3.1 演示会话 cookie（`restricted` 模式）

`APP_ENV=demo` 时 `/api/v1/admin/*` 除了 Bearer 还要求签名未过期的 `huice_demo_session`；任意字符串不再通过。签发：

```bash
set -a; . /etc/huice/api.env; set +a      # 仅为取到 ADMIN_TOKEN，注意当前 shell 会持有它
python3 /opt/huice/app/infra/deploy/issue-session.py --hours 12
```

输出两行：cookie 值和可直接使用的 `Set-Cookie` 头。把 `Set-Cookie` 配到网关/演示入口（或在浏览器开发者工具里手工设置 cookie），之后带着它访问后台接口。签名密钥由 `ADMIN_TOKEN` 派生，所以**拿到 ADMIN_TOKEN 就等于能签发会话**：不要把输出写进日志、文档或群聊。

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

- `DEMO_ACCESS_MODE=restricted` 要求请求带 `huice_demo_session` cookie，而**签发该 cookie 的入口尚未实现**（计划见 `infra/README.md` §5.2 B）。因此在云端，`/api/v1/admin/*` 目前会返回 403，只有 `/api/v1/health` 可用。这不是部署故障。
- `OBJECT_STORE_MODE=obs` 的代码已实现，但**尚未在真实桶上验证**；在验证前保持 `local`。
- 未配置域名与证书：大陆地域用域名走 80/443 需要 ICP 备案，当前直接用 EIP 演示。
- `infra/deploy/deploy.ps1` 仍是占位文件，本阶段以本文档的手工步骤为准。

## 6. 回滚

```bash
systemctl stop huice-api          # 停服务
cp /opt/huice/app.bak/* /opt/huice/app/   # 恢复上一份代码（若事先备份）
systemctl restart huice-api
```

数据库只增不改：新变更写 `infra/sql/002_*.sql`，不 `DROP` 已有表、不改历史脚本。配置回退恢复 `/etc/huice/api.env` 上一版本即可。

## 7. 安全约束

- `/etc/huice/api.env` 含数据库与 Redis 密码、OBS 密钥，权限必须是 `640 root:huice`，不得复制到仓库目录、不得贴进聊天记录。
- `ADMIN_TOKEN` 只做服务端校验，不作为任何前端构建变量。
- 启动失败时日志只输出变量名；若看到具体值，按缺陷处理。
- nginx 只暴露 80（证书就绪后加 443），ECS 安全组不额外开放 8000。
