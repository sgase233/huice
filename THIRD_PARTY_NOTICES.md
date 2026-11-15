# 第三方软件与许可证说明

本文用于比赛材料与交接说明，列出「惠策协同」仓库当前依赖的主要第三方软件及其许可证。**版本以 `services/api/requirements.lock` 与 `apps/web/package-lock.json` 为准**；升级依赖后应同步更新本文件。

> 本文不是法律意见。对外分发二进制或镜像前，请按各许可证原文附上版权声明与许可证副本；有疑问时以包内 `LICENSE` / `NOTICE` 文件为准。

## 1. 项目自身的许可证

本项目采用 **MIT License**，全文见根目录 [`LICENSE`](LICENSE)。

- 著作权人：饶神舟、刘佳熙、王俪钧
- 年份：2026
- 适用范围：本仓库中的源代码、配置与文档（另有说明的除外）
- 政策原文、附件与第三方素材仍适用各自的版权/许可证，不在 MIT 授权范围内

MIT 允许使用、复制、修改、合并、发布、再许可与销售，但要求在副本或实质性部分中保留版权声明与许可声明。

## 2. 后端运行时依赖

直接依赖（`services/api/pyproject.toml`）：

| 包 | 版本 | 许可证 |
| --- | --- | --- |
| FastAPI | 0.161.1 | MIT |
| uvicorn | 0.54.0 | BSD-3-Clause |
| pydantic | 2.14.1 | MIT |
| python-multipart | 0.0.32 | Apache-2.0 |
| psycopg / psycopg-binary / psycopg-pool | 3.3.6 / 3.3.6 / 3.3.3 | LGPL-3.0（动态链接使用） |
| pypdf | 6.21.2 | BSD-3-Clause |
| python-dotenv | 1.2.4 | BSD-3-Clause |
| esdk-obs-python（华为云 OBS SDK） | 3.26.6 | Apache-2.0 |
| redis（redis-py 客户端） | 8.1.0 | MIT |

主要传递依赖：

| 包 | 许可证 |
| --- | --- |
| starlette、httpx、httpcore、h11、anyio | BSD-3-Clause / MIT |
| certifi | MPL-2.0 |
| requests | Apache-2.0 |
| urllib3、charset-normalizer、idna | MIT / MIT / BSD-3-Clause |
| pycryptodome、crcmod、six | BSD-2-Clause / MIT / MIT |
| PyYAML、jsonschema、referencing、rpds-py | MIT |
| Pygments | BSD-2-Clause |
| packaging | Apache-2.0 / BSD-2-Clause（以包内为准） |
| opentelemetry-api、tzdata | Apache-2.0 |
| typing-extensions | PSF-2.0 |
| click、colorama | BSD-3-Clause |
| watchfiles、websockets、httptools | MIT / BSD-3-Clause / MIT |
| pytest、pluggy、iniconfig（仅测试） | MIT |

说明：

- **psycopg 是 LGPL-3.0**：本项目以独立 Python 包形式动态使用，未修改其源码；分发时需保留许可证与版权声明，并提供对应源码获取方式。
- **certifi 是 MPL-2.0**：属于文件级 copyleft；分发时保留许可证，修改其文件需公开修改部分。
- **Apache-2.0 组件**（如 OBS SDK、requests、python-multipart）：分发时需附 Apache-2.0 许可证副本，并保留 NOTICE（如有）。
- 完整传递依赖及版本见 `services/api/requirements.lock`；如需逐包核对，可在安装后的虚拟环境中读取各包的 `METADATA`/`LICENSE`。

## 3. 前端依赖

运行时（会进入浏览器产物）：

| 包 | 版本 | 许可证 |
| --- | --- | --- |
| react | 19.3.1 | MIT |
| react-dom | 19.3.1 | MIT |
| scheduler | 0.28.0 | MIT |

构建/测试工具（不进入生产产物，但属于开发依赖）：

| 包 | 许可证 |
| --- | --- |
| vite、vitest、@vitejs/plugin-react、esbuild、rollup | MIT |
| typescript | Apache-2.0 |
| @playwright/test | Apache-2.0 |

完整列表见 `apps/web/package-lock.json`。

## 4. 运行时与基础设施

| 组件 | 许可证/条款 | 说明 |
| --- | --- | --- |
| Python 3.12 | PSF-2.0 | 运行时 |
| Node.js 24 | MIT | 仅构建前端 |
| PostgreSQL 16 + pgvector | PostgreSQL License / PostgreSQL License | 数据库；pgvector 为独立扩展 |
| Redis 7.4（如启用） | RSALv2 / SSPLv1 | 7.4 起不再是 OSI 开源许可；商用/再分发前需确认条款 |
| nginx | BSD-2-Clause | 反向代理与静态站点 |
| Ubuntu 22.04/24.04 | 各组件各自许可 | 基础镜像/系统 |
| 华为云 ECS/RDS/OBS | 华为云服务条款 | 云服务本身不适用开源许可证 |

如果另行使用 Terraform 或第三方部署工具，其许可证独立于本仓库（例如 Terraform 1.6+ 为 BSL-1.1，HuaweiCloud provider 以其仓库声明为准），不要混写进本项目的开源声明。

## 5. 内容与素材

- `data/policies/**` 中的政策正文、附件与链接指向政府公开文件，**版权归原发布机关**；仓库仅用于比赛演示与引文定位，不主张对其拥有版权。
- 企业名称、画像数据均为合成数据，不含真实企业资料。
- `apps/web/src/assets/home/` 下 3 张插图（`documents.png`、`office.png`、`team.png`）的来源与授权需由前端负责人（M2）确认并补充；在确认前不要对外声明为项目原创素材。
- 页面字体使用系统字体（Microsoft YaHei / PingFang SC / Noto Sans CJK），未打包第三方字体文件。

## 6. 维护方式

- 后端升级：重新生成 `requirements.lock` 后，按第 2 节核对直接依赖与新增传递依赖的许可证。
- 前端升级：`npm ci` 后按 `package-lock.json` 核对新增包；构建工具与运行时依赖分开记录。
- 项目级 `LICENSE` 确定后，在本文件第 1 节补上许可证名称与年份/著作权人，并在 README 增加许可证徽章或声明。

## 附录 A：后端锁定依赖全量清单

下表对应 `services/api/requirements.lock`（含传递依赖）。许可证以各包安装后的 `METADATA` / `LICENSE` 为准；表中「/」表示多许可中选择。

| 包 | 版本 | 许可证 |
| --- | --- | --- |
| annotated-doc | 0.0.5 | MIT |
| annotated-types | 0.8.0 | MIT |
| anyio | 4.15.1 | MIT |
| attrs | 26.1.0 | MIT |
| certifi | 2026.10.19 | MPL-2.0 |
| charset-normalizer | 3.5.2 | MIT |
| click | 8.5.0 | BSD-3-Clause |
| colorama | 0.4.6 | BSD-3-Clause |
| crcmod | 1.7 | MIT |
| esdk-obs-python | 3.26.6 | Apache-2.0 |
| fastapi | 0.161.1 | MIT |
| h11 | 0.16.0 | MIT |
| httpcore | 1.0.9 | BSD-3-Clause |
| httptools | 0.9.0 | MIT |
| httpx | 0.28.1 | BSD-3-Clause |
| idna | 3.20 | BSD-3-Clause |
| iniconfig | 2.3.1 | MIT |
| jsonschema | 4.26.0 | MIT |
| jsonschema-specifications | 2025.9.1 | MIT |
| opentelemetry-api | 1.45.1 | Apache-2.0 |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| pluggy | 1.7.0 | MIT |
| psycopg | 3.3.6 | LGPL-3.0 |
| psycopg-binary | 3.3.6 | LGPL-3.0 |
| psycopg-pool | 3.3.3 | LGPL-3.0 |
| pycryptodome | 4.1.0 | BSD-2-Clause |
| pydantic | 2.14.1 | MIT |
| pydantic_core | 2.50.1 | MIT |
| Pygments | 2.21.0 | BSD-2-Clause |
| pypdf | 6.21.2 | BSD-3-Clause |
| pytest | 9.1.1 | MIT |
| python-dotenv | 1.2.4 | BSD-3-Clause |
| python-multipart | 0.0.32 | Apache-2.0 |
| PyYAML | 6.0.3 | MIT |
| redis | 8.1.0 | MIT |
| referencing | 0.37.0 | MIT |
| requests | 2.34.2 | Apache-2.0 |
| rfc3339-validator | 0.1.4 | MIT |
| rpds-py | 2026.9.1 | MIT |
| six | 1.17.0 | MIT |
| starlette | 1.9.0 | BSD-3-Clause |
| typing-inspection | 0.4.4 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| tzdata | 2026.5 | Apache-2.0 |
| urllib3 | 2.8.0 | MIT |
| uvicorn | 0.54.0 | BSD-3-Clause |
| watchfiles | 1.3.0 | MIT |
| websockets | 17.3 | BSD-3-Clause |

## 附录 B：前端依赖范围

- **进入生产产物的运行时依赖**只有 `react`、`react-dom`、`scheduler`（均 MIT），见第 3 节。
- 构建/测试工具（Vite、TypeScript、Vitest、Playwright、esbuild、Rollup、Babel 等）只在开发/构建机使用，不会打包进 `dist/`；完整版本与许可证见 `apps/web/package-lock.json`。
- 如需逐包输出运行时清单：

```bash
cd apps/web
npm ls --omit=dev --all
```

## 附录 C：本地开发用容器镜像

`infra/local/docker-compose.yml` 使用：

| 镜像 | 说明 |
| --- | --- |
| `pgvector/pgvector:pg16` | PostgreSQL 16 + pgvector；PostgreSQL License |
| `redis:7.4-alpine` | Redis 7.4；RSALv2 / SSPLv1（非 OSI 开源许可，注意使用场景） |

## 附录 D：项目许可证元数据

- 根目录 `LICENSE`：MIT
- `services/api/pyproject.toml`：`license = { text = "MIT" }`，并列出三位著作权人
- `apps/web/package.json`：`"license": "MIT"`
- `README.md`：许可证章节（含第三方声明入口）

修改许可证或著作权人时，需同时更新上述四处与本文件第 1 节。
