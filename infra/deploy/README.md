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

## 0. 前置条件、版本矩阵与执行顺序

**这份文件的读者**：拿到一台空 ECS 与已开通的云资源、要从零装到「健康检查 200」的人。除标注「Windows 侧」的步骤外，命令都在 ECS 的 bash 里执行。

### 0.1 云资源与安全组

| 资源 | 当前取值（2026-11-04） | 动手前要确认 |
| --- | --- | --- |
| ECS | `huice-api-01`，2 vCPU / 4 GiB，Ubuntu 24.04.4 LTS（自带 Python 3.12.3），公网 EIP `139.9.33.46`，内网 `192.168.0.86` | 安全组入方向：TCP 22（只对你的来源 IP）、80、443；**不开放 8000** |
| RDS | PostgreSQL 16.13 + `pgvector` 0.8.0，内网 `192.168.0.80:5432`，库 `huice` | 与 ECS 同 VPC；从 ECS 能连到 5432 |
| Redis | DCS 7.2.11.5 主备，内网 `192.168.0.38:6379` | 同上；密码只写进 `/etc/huice/api.env` |
| OBS | 桶 `huice-policies`（私有），`cn-south-1` | 只在 `OBJECT_STORE_MODE=obs` 时需要桶名/endpoint/AK/SK |
| DeepSeek | 公开 API `https://api.deepseek.com`，模型 `deepseek-chat` | 只在 `MODEL_MODE=deepseek` 时需要 Key |
| 域名／证书 | 暂无，直接用 EIP | 大陆地域用域名走 80/443 需 ICP 备案；未备案按 §3.2 走自签 443 |

### 0.2 软件版本矩阵

| 组件 | 版本 | 依据 |
| --- | --- | --- |
| 操作系统 | Ubuntu 24.04.4 LTS | 华为云镜像，2026-10-23 在 ECS 上 `lsb_release -a` 实测 |
| Python | 3.12.3（系统自带）；虚拟环境在 `/opt/huice/venv` | 同上，`python3 --version` |
| 后端依赖 | `services/api/requirements.lock`，**48 项**（`pip freeze`；含 FastAPI 0.161.1、Pydantic 2.14.1、psycopg 3.3.6、redis 8.1.0、esdk-obs-python 3.26.6） | 锁文件本身 |
| PostgreSQL | 16.13（含 `vector` 0.8.0） | 2026-10-24 在 ECS 上连库实测 |
| Redis | 7.2.11.5（DCS 主备） | 2026-10-23 实测 `PING` → `PONG` |
| nginx | 系统包，由 `provision.sh` 安装 | 版本不固定，以 `nginx -v` 为准 |
| systemd | 系统自带 | unit 见 `huice-api.service` |

### 0.3 从零到「健康检查 200」的顺序

顺序反了不会「降级运行」，而是出现「启动成功但写入 500」「外网 404 且响应头泄露版本」这类故障。按下面八步走：

| 步 | 做什么 | 在哪 | 顺序反了的表现 |
| --- | --- | --- | --- |
| 1 | 传代码到 `/opt/huice/app`（§1） | Windows 侧 + ECS | 后面所有脚本都找不到 |
| 2 | `provision.sh`（§2）：装包、建 `huice` 用户与 venv、装依赖、装 unit 与 80 配置 | ECS | — |
| 3 | 填 `/etc/huice/api.env`（§3） | ECS | 第 2 步结束时模板里的必填项还是空值，服务会以「缺少变量名」启动失败，属预期（脚本那一步就算失败也继续执行下一步） |
| 4 | 执行迁移 001—007（§2.1：`bash infra/deploy/migrate.sh`），确认 `schema_migration` 七行 | ECS | 先对外服务再迁移：写事实 500、抽取写幂等记录因缺列 500（§2.1 末段） |
| 5 | `systemctl restart huice-api`，再 `nginx -t && systemctl reload nginx` | ECS | 只 `enable` 不 `reload`：外网 404 且 `Server` 头暴露 nginx 版本（见 §8「外网 404」一行） |
| 6 | 健康检查 200（§4 第 **1—4** 项）：本机、经 nginx、公网三层 | ECS + 自己电脑 | 200 只证明应用→RDS 通，不证明 443 与前端可用 |
| 7 | 自签 HTTPS（§3.2）；要前端页面同源就加 `--web-root`（§3.3）。**完成后**按 §4 的三批安排补跑第 **8—11** 项（第 5—7 项在第 6 步之后就能跑，不必等这里） | ECS | 不做：`restricted` 下带 `Secure` 的 cookie 在 `http://` 下存不住，页面一直 403；若先跑第 8—9 项，会因为 443 还不存在而误判成部署失败 |
| 8 | 签发会话 cookie（§3.1） | ECS | `/admin/*` 会卡在鉴权第一关：没有 Bearer 或 Bearer 不对是 **401 `UNAUTHENTICATED`**；Bearer 正确但缺/错 cookie 才是 **403 `DEMO_SESSION_REQUIRED`**（与 §4 第 5—7 项同一口径），`/health` 仍是 200 |

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

脚本必须是 **LF**：Windows 侧 `core.autocrlf` 会把工作区写成 CRLF，打包上云后 `#!/usr/bin/env bash\r` 直接执行失败（`bad interpreter` 或满屏 `$'\r': command not found`）。本目录的 `.gitattributes` 已把 `*.sh` 固定为 `eol=lf`，解包后确认一次：

```bash
head -c 20 /opt/huice/app/infra/deploy/provision.sh | od -c | head -1   # 看到 \n，不能是 \r \n
```

本地工作区若是更早结账的 CRLF 副本，删掉再 `git checkout -- infra/deploy/*.sh` 让它按属性重写（直接 `git checkout` 对未改动的文件会因 stat 相同而跳过）。

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

代码直接读写 `invalidation_event.fact_period_start/fact_period_end`（`004_invalidation_event_period.sql` 引入），企业事实的键/单位要与契约 `1.0.0-rc.4` 起的 **18 键**字典对齐（`005` 由 10 键扩到 16 键并引入 `ratio`，`006` 再由 16 键扩到 18 键、新增 `specificMarketSince`/`independentLegalEntity`），`1.0.0-rc.7` 起幂等记录的读写又多了一列 `idempotency_record.usage_semantics`（`007_idempotency_usage_semantics.sql` 引入），**服务启动前云 RDS 必须已执行 001—007 全部迁移**。

**取值交给 `migrate.sh`，不要 `source` 整个文件，也不要自己解析。** `systemd` 的 `EnvironmentFile` 不是 shell——不做变量展开、不执行命令，只处理引号、转义与续行。`set -a; . /etc/huice/api.env; set +a` 会让 shell 按 shell 规则解析**整个文件**：口令或密钥里出现 `$`、反引号、`!`、未配对引号时，读到的值可能和 systemd 实际传给服务进程的不一样（迁移就打到错的库），甚至把文件内容当命令执行。

同目录的 `migrate.sh` 不复制 systemd 的解析规则，而是直接用 systemd 自己的结果：

1. **服务在跑**（`systemctl show -p MainPID --value huice-api` 给出非 0 的 PID）：读 `/proc/<PID>/environ`——那是 systemd 真正传进进程的那一份，不存在「两边理解不同」。
2. **服务没跑**：`systemd-run --pipe --wait -p EnvironmentFile=/etc/huice/api.env` 让 systemd 自己解析一次再把值交回来，语法、引号、续行全由 systemd 决定。
3. **两条都不行**：只打印变量名与原因，非零退出。不猜，也不退回默认连接参数。

两种读法到底差在哪，2026-11-05 在 ECS 上用假值实测了一次（同一份文件，systemd 与 `source` 各解析一次）：

| 文件里的写法 | systemd 读到 | `set -a; . 文件` 读到 |
| --- | --- | --- |
| `HUICE_T_EXPAND=$HOME` | `$HOME`（不展开） | `/root` |
| `` HUICE_T_TICK=`id -u` `` | `` `id -u` ``（字面） | `0`（**真的执行了**） |
| `HUICE_T_SPACE=a b` | `a b` | 空，且报 `b: command not found` |
| `HUICE_T_CONT=l1 \`（下一行 `l2`） | `l1 l2` | 空，且报 `l2: command not found` |

也就是说 `source` 既可能读错值（`$`、反引号、空格全按 shell 规则处理），也会把文件内容当命令执行；口令里带这些字符时，两种读法拿到的不是同一个连接串。同一份 `api.env` 上，本脚本的两条取值路径——服务在跑时读 `/proc/<MainPID>/environ`、服务没跑时用 `systemd-run -p EnvironmentFile=`——用假值实测拿到**同一个** `DATABASE_URL`（只比 SHA-256，不打印值）。这条对照比的是**读法**（`source` vs systemd 的 `EnvironmentFile` 语义），不等于脚本第 2 条路径自身没问题——后者在 2026-11-05/06 两次真实部署里取到过空值，根因与修复见下段。`EnvironmentFile` 指向不存在的文件时 `systemd-run` 以退出码 1 失败（实测），脚本因此停下，不会退回默认连接参数。

**服务还没启动过时，第 2 条是唯一路径**——首次部署正好落在这里。2026-11-05 与 2026-11-06 两次真实部署都在这一步取 `DATABASE_URL` 得到空值，2026-11-06 定位出根因：脚本把 `${DATABASE_URL-}` 原样交给 `systemd-run`，而 **systemd 在把命令交给 `ExecStart` 之前会先展开 `${...}`**——`DATABASE_URL-` 带减号、不是合法变量名，被当成未知变量换成空串，`/bin/sh` 实际收到的是 `printf '%s' ""`，退出码 0、输出为空，看着就是「这个变量没配」。修复是把 `$` 写成 `$$`（systemd 只还回一个字面 `$`，留给 `/bin/sh` 自己算），云端实测未转义 0 字节、转义后 54 字节。**修复在本分支（PR #44），尚未部署**：云端 `/opt/huice/app/infra/deploy/migrate.sh` 仍是未转义的那一版，按 §6.1 换上本分支之前，停机跑迁移仍会停在「`DATABASE_URL` 为空」。取值失败时脚本会把能定位的信息都打出来（只打变量名与路径，绝不打值）：

- `systemd-run` 的**退出码与 stderr 前 10 行**：「临时单元根本没起来」和「起来了、但这个变量是空的」是两类问题，先分开再查；
- `systemctl show -p EnvironmentFiles --value huice-api` 的结果：unit 实际读的到底是不是 `/etc/huice/api.env`。对不上就明说脚本读的是另一个文件；unit 改用 `Environment=` 直接声明变量（没有 `EnvironmentFiles`）时也只报这件事，不打印内容；
- 路径对得上、值仍为空：提示查文件格式，也就是下面这段。

`api.env` 的格式是硬要求——**systemd 不按 shell 解析**：每行只写 `KEY=value`，不要写 `export`，等号两侧不要空格，不要 CRLF/BOM。systemd 对不认识的行**只记一条 journal 警告、然后把那行当不存在**，脚本拿到的就是空值；所以「文件里明明有这行」和「systemd 读到了」是两件事。自查（只输出路径、变量名、行号，不输出值）：

```bash
systemctl show -p EnvironmentFiles huice-api
systemd-run --quiet --pipe --wait --collect -p EnvironmentFile=/etc/huice/api.env \
    /bin/sh -c 'env | cut -d= -f1 | sort'                    # systemd 认得的变量名
grep -nE '^export |[[:space:]]=|=[[:space:]]' /etc/huice/api.env | cut -d= -f1   # 可疑行号
grep -c $'\r' /etc/huice/api.env                             # 非 0 即含 CRLF
```

首次部署的留证里要含这三样：`systemd-run` 的退出码、`systemctl show -p EnvironmentFiles huice-api` 的输出、以及取值成功后脚本打印的 `schema_migration` 验收结果。**第 2 条路径已于 2026-11-06 在真实 systemd（255.4）上定位并实测**：未转义写法返回 0 字节，改成 `$$` 后取到 54 字节（只记长度，不记值）；假件 `test_migrate_script.py` 也按同一条规则判——去掉 `$$` 后仍出现 `${` 就判失败。修复随本分支（PR #44）上线；在那之前，ECS 上仍要先 `systemctl start` 再跑迁移。

```bash
bash /opt/huice/app/infra/deploy/migrate.sh
```

要 **root** 执行：读 `/proc/<PID>/environ` 与调用 `systemd-run` 都需要（`huice` 用户读不到别人的进程环境）。

脚本先读一次 `schema_migration`（表不存在就按空库处理），再按 001—007 的顺序**只执行还没登记的那些**（每步 `-v ON_ERROR_STOP=1`）；已登记的打一行「已登记，跳过」不动它，库里若有清单之外的 id（代码比库旧）只点名、不影响本次执行。**任一环节失败立即停下**：读不到 `DATABASE_URL`、读到空值、某一步迁移报错时，后面的迁移与验收查询**一次都不会执行**——没迁移完的库上跑验收查询只会给出误判。全部成功后才做验收查询，逐个核对七个 id 都在 `schema_migration` 里，缺哪个报哪个并以非零退出。

**为什么不是「从 001 全量重放」**：迁移文件各自幂等，但**幂等不等于能跳过中间状态**。库已到 `008` 时重放 `005`，事实字典约束会被收回到 16 键，撞上库里已有的 `specificMarketSince` 行而报错（2026-11-06 云端实测）。所以「有新序号就重跑脚本」在旧版里根本推不动，新版按登记表判断。

**「立即停下」保护的是后面的步骤，不是整批的原子性。** 迁移是一条一条提交的：第 N 条失败（或 `infra/sql/` 里缺了某个 `.sql`）时，前面几条**已经写进库**，库就停在这个部分迁移的状态——先跑 001—006、再因 007 缺失停下，和「在任何迁移之前停下」是两回事。已登记的那些下次会被跳过，没登记的继续跑，所以修好原因直接重跑 `migrate.sh` 就会从断点补上；验收查询只在全部执行完之后才跑，所以部分状态不会被报成「全部应用」。

**登记表本身就是验收项**：某份 `.sql` 跑通了却漏了写 `INSERT INTO schema_migration`，`psql` 退出码仍是 0、库里却没有它。这种文件只有收尾那次验收查询能发现（它会报「缺 00X」并非零退出），`test_migrate_script.py` 用一个「跑成功但不登记」的假迁移覆盖了这条路径。

脚本里的清单与 `infra/sql/` 下的文件由 `services/api/tests/test_migrate_script.py` 的一条守卫用例比对：新增迁移只加 SQL 文件、忘了进清单，用例就转红。验收查询也按清单逐项核对，所以清单漏一条时它不会替那条「登记上了」——漏掉的是执行本身。

`DATABASE_URL` 会作为 `psql` 的参数出现在进程列表里（`psql` 只从命令行或 `.pgpass` 取 URI，不认环境变量）。迁移窗口内别与他人共用登录会话，也别同时开着 `watch ps` 之类会刷屏的东西。ECS 镜像自带 `psql`（2026-10-24 实测），换成没有客户端的镜像就先 `apt-get install -y postgresql-client`。

服务起来之后想确认「进程里那份值」与文件一致，可以只比哈希、不打印口令：`tr '\0' '\n' < /proc/$(systemctl show -p MainPID --value huice-api)/environ | grep '^DATABASE_URL=' | sha256sum`。

漏掉 `006` 不会在启动时报错，而是在**写入地方通知企业事实时**才炸：`enterprise_fact_key_check` 与 `enterprise_fact_key_unit` 仍是 16 键版本，`specificMarketSince`/`independentLegalEntity` 会被约束直接拒绝（500），而 SRDI-001、RDSUB-001/002 正好要用这两键。

漏掉 `007` 同样不在启动时报错：`/api/v1/health` 200、其余接口正常，只有**抽取并保存政策草稿**那一条写幂等记录时因缺列 500（`idempotency_record.usage_semantics` 不存在）。所以验收查询必须跑完整个清单，只看「服务起来了」证明不了迁移齐了。

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
| `CORS_ORIGINS` | 前端真实源；前端未部署前用 `http://139.9.33.46`，做了 §3.3 的同源站点后改用 `https://139.9.33.46`（同源请求不再走 CORS，填了也无害） |
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
systemd-run --quiet --pipe --wait --collect \
  -p EnvironmentFile=/etc/huice/api.env \
  -p WorkingDirectory=/opt/huice/app \
  /opt/huice/venv/bin/python /opt/huice/app/infra/deploy/issue-session.py --hours 12
echo "退出码: $?"
```

密钥由 systemd 自己按 `EnvironmentFile` 的规则装进这个**临时单元**的进程环境，签发脚本从环境里取用：不经过 shell 解析（理由同 §2.1），不出现在任何命令行参数里，也不留在当前 shell。`--pipe` 把脚本输出接回来，`--collect` 让临时单元跑完即回收；`systemd-run` 需要 root。

**临时单元不继承调用 shell 的工作目录**：`systemd-run` 创建的单元默认从 `/` 启动，前面先 `cd /opt/huice/app` 也不起作用（那是 shell 自己的 cwd）。因此：

- 解释器与脚本一律写**绝对路径** `/opt/huice/venv/bin/python /opt/huice/app/infra/deploy/issue-session.py`。写成相对路径时 Python 会把相对路径拼到工作目录 `/` 上，去找 `//infra/deploy/issue-session.py`，stderr 报 `can't open file '//infra/deploy/issue-session.py': [Errno 2] No such file or directory` 并以退出码 2 结束——签发脚本一行都没跑，所以不会有 `value:` 那两行（2026-11-05 在 ECS 上实测：绝对路径写法的退出码 0、输出两行；相对路径写法退出码 2、无输出）。
- 显式加 `-p WorkingDirectory=/opt/huice/app`，与 `huice-api.service` 的工作目录一致。签发脚本本身用 `__file__` 推出仓库根、不依赖 cwd，但服务读的 `LOCAL_OBJECT_DIR` 默认值是相对路径 `.local-objects`；把工作目录钉死，临时单元与应用进程看到的是同一个根。
- 跑完核对 `echo $?` 为 0 且输出两行（`value:` 与 `Set-Cookie:`）。**退出码 2 有三种来源**：Python 找不到脚本、`ModuleNotFoundError`（用了系统 `python3`）、`DEMO_SESSION_SECRET` 缺失或过短——只看退出码分不清，要读 stderr：脚本自己打印的提示只报变量名、不报值，`can't open file` 则是路径写错。

**必须用虚拟环境的解释器**：脚本会导入 `app.security`，后者在模块级导入 FastAPI，而依赖只装在 `/opt/huice/venv`。用系统 `python3` 运行会直接抛 `ModuleNotFoundError`（脚本会打印提示并以退出码 2 结束，而不是抛堆栈）。

两点注意：**不要 `set -a; . /etc/huice/api.env`**——shell 会按 shell 规则解析整个文件，而 `systemd` 的 `EnvironmentFile` 不是 shell（不做变量展开、不执行命令），密钥里含 `$`、反引号或引号时两边读到的值可能不同，甚至把文件内容当命令执行；改用上面的 `systemd-run` 后这条风险不再存在（两种读法的实测对照表见 §2.1）；但读回**空值**时仍要按 §2.1 的自查查 `api.env` 的写法与 unit 的 `EnvironmentFiles`——临时单元读的和 `migrate.sh` 是同一份文件、同一套规则。签发完成后按 §3.2 的例外说明限定有效期、用完清掉并按需轮换 `DEMO_SESSION_SECRET`。

输出两行：cookie 值和可直接使用的 `Set-Cookie` 头。把 `Set-Cookie` 配到网关/演示入口（或在浏览器开发者工具里手工设置 cookie，见 §3.2 的例外说明），之后带着它访问后台接口。

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
- 写会话 cookie 的**默认与正式方案只有一条**：把 §3.1 输出的 `Set-Cookie` 配到演示网关/服务端，由它下发 `HttpOnly; Secure; SameSite=Lax`。人工注入是**受限测试例外**，不是等价方案，理由与边界如下：
  - 手工路径（F12 → Application → Cookies，或 Console 的 `document.cookie`）拿不到 `HttpOnly`：演示环境用的新版 Chromium 里那两栏是灰的，用 `document.cookie` 写入更是明文可读——**同源的任何页面脚本都能取走这个会话**。
  - 因此：只用合成数据、只在评审演示窗口内使用、有效期压到最短（`issue-session.py --hours` 取 1—2 小时）、用完立刻清掉 cookie 并在服务端轮换 `DEMO_SESSION_SECRET` 让已签发的会话全部失效。
  - 不能把它当正式门禁：不应写进验收脚本、不应让外部演示账号长期持有、也不应在文档或聊天里留值（§3.1 的输出只进受控入口）。
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

分三批跑，对应 §0.3 的步骤，不要求在第 6 步一次过完：

1. **第 1—4 项**在第 6 步就能跑（服务起得来、`/health` 200）。
2. **第 5—6 项**在第 5 步重启之后就能跑（无凭证 → 401、只带 Bearer → 403，不依赖会话 cookie）。
3. **第 7 项**要等 §3.1 签发了会话 cookie（§0.3 第 8 步），否则它只会重复第 6 项的 403。
4. **第 8—9 项**要等 §3.2 / §3.3（§0.3 第 7 步）完成后。**只部署 API、不做 443 同源站点时记「不适用」**。
5. **第 10 项**随时可跑；**第 11 项**看三条前提，不满足记「不适用/待验」。

除标注「不适用」的项外，各项不过就先修，不要继续往下走；第 11 项另有三条前提（见下表），不满足就记「不适用/待验」，不算通关条件。命令编号与下表一致：

```bash
# 1) 应用只绑回环
ss -lntp | grep 8000

# 2)—4) 健康检查：本机、经 nginx、公网（4 在自己电脑上跑）
curl -i http://127.0.0.1:8000/api/v1/health
curl -i http://127.0.0.1/api/v1/health
curl -i http://139.9.33.46/api/v1/health

# 5)—7) 鉴权分层。令牌只从 systemd 解析出的环境里取：不 source 整个 api.env（shell 会按
#       展开/执行规则解析整个文件，而 systemd 的 EnvironmentFile 不是 shell；口令里的
#       $ ` " 等字符两边理解不同，source 既可能读错值，也可能把内容当命令执行）。
#       临时单元读完即回收，令牌不进 argv、不留在当前 shell
ADMIN_TOKEN=$(systemd-run --quiet --pipe --wait --collect \
    -p EnvironmentFile=/etc/huice/api.env /bin/sh -c 'printf %s "$ADMIN_TOKEN"')
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/api/v1/admin/policies
# 6) 只带 Bearer：令牌与 cookie 都经 stdin 交给 curl（--config -），不出现在进程参数里，
#    同机其他用户 ps 看不到
printf 'header = "Authorization: Bearer %s"\n' "$ADMIN_TOKEN" |
    curl -s -o /dev/null -w '%{http_code}\n' --config - http://127.0.0.1:8000/api/v1/admin/policies

# 7) Bearer + 会话 cookie：cookie 值取自 §3.1 输出的第一行（`value:` 后面那段），
#    用隐藏输入读，避免落在 shell 历史与 argv 里；12 小时有效，验完清掉
read -r -s -p 'huice_demo_session 的值（不回显）：' SESSION; echo
printf 'header = "Authorization: Bearer %s"\nheader = "Cookie: huice_demo_session=%s"\n' \
    "$ADMIN_TOKEN" "$SESSION" |
    curl -s -o /dev/null -w '%{http_code}\n' --config - http://127.0.0.1:8000/api/v1/admin/policies
unset ADMIN_TOKEN SESSION

# 8)—9) 443 与同源站点（做过 §3.2 / §3.3 才有意义）
curl -k -i https://127.0.0.1/api/v1/health
curl -k -s -o /dev/null -w '%{http_code}\n' https://127.0.0.1/

# 10) 日志不泄露（输出应为 0）
journalctl -u huice-api -n 200 --no-pager | grep -Eci -e 'sk-' -e 'password' -e 'postgresql://'

# 11) 模型调用记录：需同时满足①部署的是含 model_call 的版本（PR #34 起）、
#     ②MODEL_MODE=deepseek、③本次部署后真的跑过一次抽取或判断。
#     三条缺一条就记「不适用/待验」，别把它当成部署通关条件
journalctl -u huice-api --since today | grep model_call
```

| # | 检查 | 通过标准 |
| --- | --- | --- |
| 1 | 应用只绑回环 | 只有 `127.0.0.1:8000`，没有 `0.0.0.0:8000` 或 `*:8000` |
| 2 | 本机健康检查 | 200，带 `X-Trace-Id`，`version` 与 `app/config.py` 的 `APP_VERSION` 一致 |
| 3 | 经 nginx（80） | 同第 2 项 |
| 4 | 公网 | 200；超时先查安全组 80 与 nginx 是否 reload（见 §8「外网 404」一行） |
| 5 | 鉴权：无凭证 | **401** `UNAUTHENTICATED` |
| 6 | 鉴权：只带 Bearer | **403** `DEMO_SESSION_REQUIRED`（`restricted` 模式下缺会话） |
| 7 | 鉴权：Bearer + 会话 | 200 |
| 8 | 443 | 200 |
| 9 | 同源站点 | 200，返回 `index.html` |
| 10 | 日志不泄露 | 0（命中按缺陷处理） |
| 11 | 模型调用有记录（有前提，见右） | 前提：部署了含 `model_call` 的版本（PR #34 起，日志级别由应用自己在 `create_app` 里配置）、`MODEL_MODE=deepseek`、部署后跑过一次真实的抽取或判断。字段口径见 `infra/README.md` §4.3；任一前提不满足记「不适用/待验」 |

健康检查内部会对 RDS 执行 `SELECT 1`，**返回 200 即证明应用→RDS 的内网链路通**；返回 503 `DEPENDENCY_UNAVAILABLE` 说明数据库连不上，先看 `journalctl -u huice-api`（日志不含连接串）。第 4 项 200 而第 8 项不通，问题在 443 那份配置或安全组，不在应用。

## 5. 已知限制（M1）

- `DEMO_ACCESS_MODE=restricted` 要求请求带 `huice_demo_session` cookie。**签发脚本已实现**（`infra/deploy/issue-session.py`，用法见 §3.1）；云端真实链路已于 2026-11-02 验证（健康检查 200、401/403 分层、写路径冒烟通过）。仍待自动化的是**网关侧的 cookie 下发**——目前要按 §3.2 由受控入口下发（临时可在浏览器里写入，见 §3.2 最后几条（这条路线拿不到 `HttpOnly`，只用于演示））；`Secure` 所需的 TLS 由 §3.2 的自签 443 提供。前端与 API 必须同源（§3.2 的 `SameSite=Lax` 限制；同源站点的装法见 §3.3）。在注入 cookie 之前，云端 `/api/v1/admin/*` 会返回 403 `DEMO_SESSION_REQUIRED`，只有 `/api/v1/health` 可用——这不是部署故障。
- `OBJECT_STORE_MODE=obs` 已于 2026-10-27 在真实桶上联调通过（`-m obs` 5 项，见 `infra/README.md` §4.3）；首轮部署仍先留在 `local`，填好 `OBS_BUCKET`/`OBS_ENDPOINT`/`OBS_AK`/`OBS_SK` 后再切 `obs`，切换后按 §4 复测一次导入。
- 未配置域名与正式证书：大陆地域用域名走 80/443 需要 ICP 备案，当前直接用 EIP 演示，评审期临时走 §3.2 的自签 443；备案后换正式证书并去掉浏览器的自签警告。
- `infra/deploy/deploy.ps1` 仍是占位文件，本阶段以本文档的手工步骤为准。

## 6. 更新与回滚（按提交）

### 6.1 更新已有部署：从当前状态换到提交 `X`

§1 讲的是「第一次把代码放上去」，这一节讲**机器上已经在跑、要换到另一个提交**。两条硬要求：**整目录替换**（解包不会删掉新版本里已删除的文件），**换完核对装上去的是哪份代码**（否则「跑的是哪版」只能靠记忆）。

**本机打出该提交的包**（`git archive` 只含已提交内容，天然排除 `.env`、`.venv`、`.local-objects`）：

```bash
cd "/d/Study/比赛/华为ICT大赛/huice"
X=$(git rev-parse --short HEAD)            # 换成要部署的提交
git -c core.autocrlf=false archive --format=tar.gz --prefix=huice/ -o "/tmp/huice-$X.tar.gz" "$X"
scp "/tmp/huice-$X.tar.gz" root@139.9.33.46:/tmp/
```

**`-c core.autocrlf=false` 不能省**（Windows 上尤其）：`core.autocrlf=true` 时 `git archive` 会把文本文件转成 CRLF 打进包里，`.sh` 直接坏掉（变量名后面多一个 `\r`，读到的值是空串），`.py` 虽能跑但与提交不再逐字节相同，`sha256sum` 核对全部对不上。2026-11-06 第一次按本节部署就栽在这里：`migrate.sh` 报 `DATABASE_URL 为空`，根因是它自己的 CRLF。

**ECS 上：停服务 → 备份 → 换整棵目录 → 按需装依赖 → 迁移 → 起服务 → 验判断接口**

```bash
set -e
X=<刚部署的提交>                            # 与上面一致
systemctl stop huice-api                    # 先停：别让运行中的进程看着脚底下的目录被移走
tar -czf "/root/app-before-$X-$(date +%Y%m%d).tar.gz" -C /opt/huice app   # 备份整棵 app（含 .local-objects）
[ ! -e "/opt/huice/app.old-$X" ] || { echo "app.old-$X 已存在（上一次更新或回滚留下的），先人工确认它是什么再删，别直接覆盖"; exit 1; }
mv /opt/huice/app "/opt/huice/app.old-$X"
mkdir -p /opt/huice/app
tar -xzf "/tmp/huice-$X.tar.gz" -C /opt/huice/app --strip-components=1

# 对象目录就在 app 里面：整目录替换会把它连同旧代码一起挪进 app.old-*，新目录里没有。
# 必须搬回来再起服务，否则已导入的附件全部找不到；权限也要一并还原，
# unit 是 ProtectSystem=strict、只对这个路径放行写入，属主/权限不对则新对象写不进去。
if [ -d "/opt/huice/app.old-$X/.local-objects" ]; then
    mv "/opt/huice/app.old-$X/.local-objects" /opt/huice/app/.local-objects
fi
chown -R huice:huice /opt/huice/app
chmod 750 /opt/huice/app/.local-objects 2>/dev/null || true   # 没有对象目录时（obs 模式）跳过
# 搬走之后 app.old-$X 里就没有这个子目录了：回滚必须把它带回去，由 §6.2 的
# rollback.sh 无条件搬回并逐个校验（同名对象内容不同就停下），别在这里或那边手写分支。

# 依赖只在 requirements.lock 变过时才装：比哈希，别靠印象
sha256sum /opt/huice/app/services/api/requirements.lock "/opt/huice/app.old-$X/services/api/requirements.lock"

# 换行符自查：应当没有任何输出（CRLF 的 .sh 会坏，见上）
grep -rlU $'\r' /opt/huice/app --include=*.sh | head

# 迁移在服务停止时跑，且只在 infra/sql/ 出现了库里没有的序号时才需要跑：
# 新代码要读新列，让老进程对着半新半旧的库接请求只会 500。migrate.sh 自己会跳过已登记的，
# 库里没有新序号时它一条都不执行（见 §2.1）。
ls /opt/huice/app/infra/sql/ | tail -3
bash /opt/huice/app/infra/deploy/migrate.sh

systemctl start huice-api
systemctl is-active huice-api
curl -i http://127.0.0.1:8000/api/v1/health
journalctl -u huice-api -n 30 --no-pager
```

**换完再验判断接口**（健康检查只证明进程活着、连得上库）：起一条真实判断再读回轨迹，确认新代码读写的是新列，而不是靠「服务起来了」推断。

```bash
# 1) 取一个真实的企业与政策版本 id（GET /enterprises、GET /policies 都要 §4 的鉴权）
# 2) 起一条判断（API-11）
curl -sS -o /tmp/run.json -w '%{http_code}\n' -X POST http://127.0.0.1:8000/api/v1/assessment-runs \
  --config - <<'EOF'
header = "Authorization: Bearer <ADMIN_TOKEN>"
header = "Cookie: huice_demo_session=<§3.1 输出的 value>"
header = "Content-Type: application/json"
data = "{\"enterpriseId\":\"<企业UUID>\",\"policyIds\":[\"<政策版本UUID>\"],\"asOf\":\"2026-11-06T12:00:00+08:00\",\"period\":{\"startDate\":\"2026-01-01\",\"endDate\":\"2026-12-31\"}}"
EOF
# 3) 读回轨迹（API-14，admin）：走 invalidation_event 的新列与判断记录
RUN=$(python3 -c 'import json;print(json.load(open("/tmp/run.json"))["id"])')
curl -sS -o /tmp/trace.json -w '%{http_code}\n' \
  -H 'Authorization: Bearer <ADMIN_TOKEN>' \
  "http://127.0.0.1:8000/api/v1/assessment-runs/$RUN/trace"
```

两次都应是 200 且响应体字段齐全；任一处 500 说明库结构或代码有一边没换到位（迁移没跑完、或目录换了一半），别往「前端问题」上推。

对象目录还要单独验两条，光看目录存在不算（属主对了、权限也可能不对；反过来说，权限对了也可能搬错目录）：

```bash
# 既有对象可读：拿一个已经存在的对象，用服务身份读一次
sudo -u huice test -r "/opt/huice/app/.local-objects/<已有对象路径>" && echo "既有对象可读"
# 新对象可写：同一个身份新建再删掉（unit 的 ProtectSystem=strict 只放行这一个路径）
sudo -u huice sh -c 'umask 077 && printf probe > /opt/huice/app/.local-objects/.probe' && echo "新对象可写"
sudo -u huice rm -f /opt/huice/app/.local-objects/.probe
```

两条都过了才算换完；`ls -ld /opt/huice/app/.local-objects` 显示 `huice:huice`、`750`，与 `provision.sh` 建出来的那份一致。

**取值路径与增量迁移都各测过，但「空库首次部署走完整条链」没有实测过**：`systemd-run` 取 `DATABASE_URL` 在真实 systemd 上单项验过（未转义 0 字节、转义后 54 字节），`migrate.sh` 的「跳过已登记、只跑待应用」用假库覆盖过各种起点；可这两件事从没在同一次空库部署里串起来走一遍（停服务取值 → 增量迁移 → 起服务 → 验判断接口）。11/15 的「从空环境复现部署」才是那条链的第一次实测，在那之前别把单项实测写成「部署已通」。

下面三条都是 2026-11-06 实测踩出来的：

- **服务停止时 `migrate.sh` 曾取不到 `DATABASE_URL`。** 它服务在跑时读 `/proc/<MainPID>/environ`（第 1 条路径），服务停止时改用 `systemd-run -p EnvironmentFile=`（第 2 条路径）；后者当天在 ECS 上返回空值，脚本随即以「`DATABASE_URL` 为空」停下。根因：systemd 在 `ExecStart` 之前会展开 `${...}`，脚本原样的 `${DATABASE_URL-}` 名字带减号、被换成空串。`read_env_var` 里已改成 `$${DATABASE_URL-}`（细节见 §2.1），**修复在本分支（PR #44），尚未部署到云端**——云端当前的 `migrate.sh` 仍是旧版，所以在这次修复上线之前，那台机器上仍然要 `systemctl start` 之后再跑迁移；按本节装上新代码之后，停机迁移才是可取的那条路径。
- **不要在已推进的库上重放旧迁移。** 「可重复执行」指的是**幂等**，不是**可以跳过中间状态**：云端库已到 `008` 时从 `001` 重放，`005` 会把事实字典约束收回 16 键，被库里已有的 `specificMarketSince` 行顶掉，`ERROR: check constraint "enterprise_fact_key_check" ... is violated by some row`。它失败在 `BEGIN` 里、事务回滚，结构没被改动，但这一趟白跑。本分支的 `migrate.sh` 改为先查 `schema_migration`、只跑未登记的那些（已登记的打印「已登记，跳过」，库里多出来的 id 只点名不影响本次执行），这一步不需要「先看 `ls` 再决定跑不跑」；旧版脚本仍然必须手工判断。
- **`psql` 直查要用服务的环境**：`PID=$(systemctl show -p MainPID --value huice-api)`、`DB=$(tr '\0' '\n' < /proc/$PID/environ | sed -n 's/^DATABASE_URL=//p')`，查完 `unset DB`。连接串会短暂出现在 `psql` 的参数里（`psql` 不认环境变量），别开着 `watch ps`。

**核对装上去的是哪份代码**：`app/evaluation` 的 `fingerprint` 子命令（PR #42 合入后可用）在**两边各算一次**，值相同才算对上；在那之前只能逐文件 `sha256sum` 比对，且注意别拿分支上的文件去比云端 main 的文件。

它的证明范围只有 `CODE_SCOPE` 那三项——`services/api/app`、`contracts/openapi.yaml`、`services/api/requirements.lock`（`infra/sql` 刻意不在内，库结构由 `schema_migration` 的验收结果证明，见 §2.1）。所以指纹相同只说明**这三处代码字节一致**：它不证明部署树的其余部分（`infra/deploy` 的脚本、`apps/web` 构建产物）、不证明迁移跑过、不证明依赖装对、更不证明进程里跑的就是盘上这份（换完没重启同样对得上）。这四件事各自另有证据，别用一条指纹代替。

```bash
cd /opt/huice/app/services/api && /opt/huice/venv/bin/python -m app.evaluation fingerprint
# 本机同提交复算：另开一个 worktree checkout 到该提交后再算（别在工作区里 stash）
cd services/api && python -m app.evaluation fingerprint
```

**已经手工打过临时补丁的机器不能「只换改过的文件」**：`/opt/huice/app` 一旦被单文件覆盖过（2026-11-06 的抽取提示词热补丁就是），它就不再等于任何提交，只有整目录替换才能回到「某提交的原样」。替换完把差异处理记进 `infra/README.md` §7，别只写在聊天里。

2026-11-06 已按本节把云端从「rc.6 + 热补丁」换成 `112d0a7`（rc.7），热补丁退役：备份在 `/root/app-hotpatched-20261106.tar.gz` 与 `/opt/huice/app.hotpatched-20261106`，确认稳定后可删。提示词本身的修正走 PR #45 回仓库，合并后云端只需换成 main 的提交，内容不变。

**`fingerprint` 也算工作区字节**：同一提交在 Windows（CRLF 检出）与 Linux（LF 部署）算出的指纹不同。核对时要么两边都按提交内容算（`git -c core.autocrlf=false` 导出后再算），要么就接受「换行符不同」这一条差异，别直接判成「部署的代码不一样」。

### 6.2 回滚到上一个提交

回滚有两件事必须同时成立，缺一条都是「服务起来了、附件却没了」：代码树换回**更新前**那份，持久对象目录跟着一起走。§6.1 已经把 `.local-objects` **搬**进了新目录，所以旧树里通常没有这个子目录；而 `cp -a` 的 `-n` 只保证不覆盖、遇到同名文件会静默跳过，`comm` 有差异时退出码也仍是 0——写在手册里的「有输出就停下」不会自己发生。所以这一段不再手写命令，改由 `infra/deploy/rollback.sh` 执行：判据由命令自己返回非零，行为由 `services/api/tests/test_rollback_script.py` 在临时目录里真跑 shell 覆盖（正常更新→更新窗内新增对象→回滚、同名对象内容不同、对象放不进去、`find` 读不全、obs 模式与 local 模式的对象目录判定、备份包还原，以及停服后 `mv`/`chown`/`systemctl` 每一步失败时的恢复与退出码）。

`X` 用 §6.1 那次**实际部署**的提交号（也就是坏掉的那个），不是「上一个提交」：`app.old-$X` 这个目录名是 §6.1 起的，里面装的是更新前的代码，回滚时别再换一套命名。

```bash
cd /opt/huice
bash /opt/huice/app/infra/deploy/rollback.sh <X>                  # X 与 §6.1 一致
```

它做的事，顺序就是安全边界：

1. **先判对象存储模式再停服务**：`--object-store` 默认 `local`，此时 `app/.local-objects` 必须存在；显式给 `obs` 时它必须不存在。对不上就退出 2，服务都没停——「目录没了」既可能是 obs，也可能是部署事故，脚本不替人猜；
2. 停服务，然后把旧树摆到 `app.stage-$X`——**`app` 在换目录之前一动不动**；
3. 把 `app/.local-objects` 里的每个对象并进 stage：缺的复制、同名且**内容相同**的跳过、同名而内容不同的**一律停下**（不覆盖，交给人决定留哪份）；清单用 `find` 落地成文件再读，`find` 读不全（权限、IO 错误）直接算失败，不会被当成「0 个对象」通过；
4. 逐文件比 sha256 校验：stage 里必须同名同内容，缺一个就非零退出；
5. 以上都过了才换目录（`app` → `app.bad-$X`，stage → `app`），最后起服务。停服之后的每一步都有明确处置：换目录前失败就把 stage 收回、**服务按原代码重新拉起**；第一次 `mv` 失败什么都没换；第二次 `mv`（stage → app）失败时把 `app` 与旧树都放回去再起服务，恢复动作本身失败会照实打印，不会显示成「已恢复」。

退出码：**0** 成功；**2** 前置条件不满足（没停服务），或停服后在换目录前失败（stage 已收回、服务按原代码重新拉起），或第二次 `mv` 失败后已把 `app` 与旧树放回（**看 stderr 分清是哪种**：前置条件那种连服务都没停）；**3** 同名对象内容不同、对象放不进去或对象清单读不全（目录没换，服务按原代码重新拉起）；**4** 合并后校验不过（同样没换，兜底用，正常路径不该出现）；**5** 已换完但服务没起来（看 `journalctl -u huice-api`，被换下来的那份在 `app.bad-$X`）。

退出码 3/4 时**不要手工接着往下做**：现场仍是原来那棵，按提示把同名对象处理掉再重跑一次即可。退出码 2 里「换目录前失败」那两种也是同样处理——stage 已收回、旧树没动，看 stderr 里那句提示修掉原因再重跑。

**从备份包还原**（`app.old-$X` 已被删或不可用）：包是**更新前**打的、不含更新窗内新导入的对象，脚本先把当前对象并进从包里还原出的树、再校验，所以这批对象不会丢：

```bash
cd /opt/huice
bash /opt/huice/app/infra/deploy/rollback.sh <X> --from-backup /root/app-before-<X>-<日期>.tar.gz
```

**obs 模式**（`OBJECT_STORE_MODE=obs`，app 里本来就没有 `.local-objects`）：**必须显式加 `--object-store obs`**，脚本才跳过对象那一步、照常换目录并打印一行说明——不会因为目录不存在而中断在「app 已挪走」的半路。不加这个参数时脚本按 `local` 判：目录不存在直接退出 2（服务都不停），因为「目录没了」也可能是部署事故。先核对 `/etc/huice/api.env` 里的 `OBJECT_STORE_MODE`，再决定传哪个值，别拿「反正没有目录」去猜；声明 `obs` 而目录还在时同样退出 2，交给人确认。

**脚本之外还要人做的两件事**：回滚后按 §6.1 末尾那两条验一次对象可读可写（换目录会动属主）；`app.bad-$X` **先别删**，确认对象都在、服务正常之后再清。

**演练要连对象一起走**：更新 → 在更新窗内导入一个新附件（库里多一条对象记录、`.local-objects` 里多一个文件）→ 回滚 → 用回滚后的服务把那个附件读出来。只做到「服务起来了」不算，读得到才算。`test_rollback_script.py` 覆盖的是同一组状态在临时目录里的行为，真实 ECS 上的演练仍待做。

**迁移不回退**：库结构只增不改，回滚代码时**不要**删列删表——旧代码在新库结构上照常工作（新列有默认值或可空）。真要回退结构，按 `infra/README.md` §8 的逆序 `DROP` 并同步删 `schema_migration` 对应行。

数据库只增不改：新变更按 `infra/sql/` 里**当前**最大序号顺延（以 `ls infra/sql/` 为准确认，别照这份手册的某一版抄），不 `DROP` 已有表、不改历史脚本。配置回退恢复 `/etc/huice/api.env` 上一版本即可。

## 7. 安全约束

- `/etc/huice/api.env` 含数据库与 Redis 密码、OBS 密钥，权限必须是 `640 root:huice`，不得复制到仓库目录、不得贴进聊天记录。
- `ADMIN_TOKEN` 只做服务端校验，不作为任何前端构建变量。
- 启动失败时日志只输出变量名；若看到具体值，按缺陷处理。
- nginx 只暴露 80 与评审期自签的 443（§3.2），ECS 安全组不额外开放 8000。

## 8. 常见故障

这一节全是实际踩过的，现象按看到的样子写，不按根因分类。

| 现象 | 原因 | 处置 |
| --- | --- | --- |
| ECS 上 `git clone` / `git pull` 卡住后报 `GnuTLS recv error (-110): The TLS connection was non-properly terminated` 或长时间超时 | ECS 直连 GitHub 被网络拦下，不是凭证问题 | 在 Windows 侧 `git bundle create huice.bundle --all`，`scp` 上去，ECS 上 `git clone /tmp/huice.bundle /opt/huice/app`；后续更新同理用 bundle 做 `fetch`，不要反复重试直连 |
| `DATABASE_URL` 填好、`psql` 能连，但 `/health` 一直 **503 `DEPENDENCY_UNAVAILABLE`** | 口令里有 `@`、`:`、`/` 等字符，连接串被截断成「主机名 + 剩余口令」 | 按本节末的「口令转义」生成转义后的口令，把结果填进连接串；**别手工改**，改完 `systemctl restart huice-api`。转义结果本身也是凭据，只写进 `/etc/huice/api.env`（该文件应为 `0640 root:huice`），不要贴进文档、聊天或工单 |
| 服务本机 `curl 127.0.0.1:8000` 是 200，外网访问 `http://<EIP>` 是 **404**，响应头 `Server: nginx/x.y.z` 带版本号 | `provision.sh` 装完 `conf.d` 后只 `enable nginx`；nginx 常已被 apt 拉起，`enable` 不会让已运行的进程读新配置，默认站点还在服务 | `nginx -t && systemctl reload nginx`；再确认 `/etc/nginx/sites-enabled/default` 已删（脚本会删，手改过的机器要自己核） |
| 登录后接口仍 403 `DEMO_SESSION_REQUIRED`，或 cookie 写进去就没了 | 会话 cookie 带 `Secure`，而页面在 `http://<EIP>`（非 localhost）下打开，浏览器不保存；或前端与 API 不同源，`SameSite=Lax` 不发送（§3.2 末段） | 走 §3.2 的 443 同源；临时演示按 §3.2 手工设置，注意 DevTools 新建 cookie 的 `HttpOnly` / `SameSite` 在新版 Chromium 里勾不动，退而用 `document.cookie` 写入就没有 `HttpOnly`——这是 §3.2 写的受限测试例外（合成数据、短有效期、验完清掉并轮换密钥），不是等价方案 |
| `nginx -t` 通过，但 443 上的行为时好时坏（有时是旧配置、有时两份都对不上） | `/etc/nginx/conf.d/` 里同时存在两份 `listen 443` + `server_name _` 的配置：nginx 只打一条 `conflicting server name "_"` 警告，不报错，实际只生效一份 | `grep -rl "listen 443" /etc/nginx/conf.d/` 只应有一份（`huice-api-tls.conf`）；`enable-tls.sh` 靠「同一个文件名」避免并存，手工加过配置的机器要自己删掉多出来的那份 |

### 口令转义（连接串里的特殊字符）

口令**不进命令行**：`python3 -c "...quote('原始口令')..."` 会把口令留在 shell 历史、进程列表（同机用户可见）和终端记录里。改用隐藏输入 + 标准输入，口令只存在于当前 shell 变量与管道的受控内存中：

```bash
# 1) 隐藏输入（不回显）；提示符里的内容不进 shell 历史
read -r -s -p '原始口令（不回显）：' HUICE_PW; echo

# 2) 口令经 stdin 交给 python，不出现在 argv 里
encoded=$(printf '%s' "$HUICE_PW" | python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.stdin.read(), safe=""))')

# 3) 立刻清掉原始口令，只把转义结果打印出来
unset HUICE_PW; printf '%s\n' "$encoded"; unset encoded
```

把打印出来的串填进 `/etc/huice/api.env` 的 `DATABASE_URL`，填完 `systemctl restart huice-api`。转义结果是**等价的凭据**：别贴进文档、聊天、工单与截图，`api.env` 的权限保持 `0640 root:huice`。含 `@ : / % ? # & = +` 与空格的密码都用这一条，别只按字符猜哪几个要转义。
