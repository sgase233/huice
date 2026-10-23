# 本地数据服务准备（M1）

当前仅是环境交接骨架；`docker-compose.yml` 还没有服务定义，不能执行启动命令。环境基线、配置变量和验收要求见根目录开发计划第 4.1 节。

M1 实现时固定 PostgreSQL 16 + pgvector、Redis 7 的镜像版本与数据卷，端口只绑定 `127.0.0.1:5432`、`127.0.0.1:6379`。先复制 `.env.example` 为 `.env`，将 `POSTGRES_PASSWORD` 设为开发专用密码，并与 `huice/.env` 的 `DATABASE_URL` 保持一致。此处补齐逐条 PowerShell 命令：启动/停止服务、创建开发库、启用并验证 `vector`、执行迁移、检查 Redis、清理本地测试数据。密码放在被 Git 排除的本地环境文件，不能写进 Compose 或此文档。

验收输出需包含数据库版本、`vector` 扩展状态、Redis `PING` 结果、初始化脚本版本与日期；检查记录写入 `docs/devlog/M1.md`，不贴完整连接串。
