# M2 第三阶段验收记录（进行中）

核验日期：2026-10-30（北京时间）。基线：`main` 的 `d1b6a31`，本地工作分支 `m2/frontend-phase3`；当前改动尚未提交。此表记录实际验证，不代替成员交叉审查。

| 验收项 | 证据 | 状态和边界 |
| --- | --- | --- |
| PR #10 六个事实键 | `src/demo.ts` 标签/单位；`enterprise.test.ts` 和 `real-flow.spec.ts` | 布尔、负增量、比例及数值边界已验证；M3 仍需复核业务文案 |
| 双模式隔离 | `real-flow.spec.ts` 的分页、筛选与切换用例；固定样例测试 | 默认真实接口，`?mode=demo` 为固定样例；失败不自动回退 |
| 画像、列表、运行及详情 | `live-flow.spec.ts`；本机 FastAPI + 隔离 PostgreSQL | 合成夹具通过真实浏览器、HTTP、数据库及跨域 ETag；非正式政策 |
| 行业排除、缺失事实 | 真实库 run `423093fb-ac37-4d55-a614-03329127fd83`、`19cded27-3dcf-4a29-a73c-b75eda4bb772` | 分别 `not_satisfied` 和 `insufficient_evidence`；运行 ID 仅存在于当前临时库 |
| 事实变更与失效 | `live-flow.spec.ts` PATCH 后读回原 run | 旧判断保留并显示 `stale`；新事实未冒充旧快照 |
| 四态、关闭、冲突双引文、错误和版本不一致 | `real-flow.spec.ts`、`client.test.ts` | 浏览器接口模拟验证，不代表实际政策内容已复核 |
| 计算过程与多尺寸 | `real-flow.spec.ts` | 模拟响应的详情在 375/768/1440 宽无横向溢出；375px 截图已目视检查；截图在被忽略的 `test-results/`，后续测试会清理 |
| 真实政策依据 | `data/policies/criteria_review.csv`、M3 PR #9 | **未完成**：main 条件表只有表头，PR #9 未合并，尚无可供本次验收的双人复核政策及案例；合成夹具不能替代 |
| 比例引用核验 | `services/api/app/domain/citations.py` | **待 M1/M3**：比例原文数值和方向未全面自动核验，不能声称已完成 |
| 契约联合签收、M3 文案复核 | 工作手册与本次记录 | **未完成**：未取得 M1/M2 契约签收或 M3 审阅证据 |

2026-10-30 最后一次验证：Node `22.15.0` 下构建通过、Vitest **24/24**、Edge Playwright **18/18**（其中 1 项使用临时真实后端，其余为固定样例或模拟接口）；`python contracts/generate_types.py --check` 通过。默认系统 Node 为 24.14.0。测试环境使用 fixture 模型，不代表真实模型调用。

完成正式验收需要 M3 提供已双人复核的政策条款和合成企业预期，M1 通过正式导入及发布流程提供可调用版本；M2 再核验官方出处、条件结果和中文文案，记录测试 run、截图、问题及复测。不要把该临时数据库中的测试发布当作正式审核。

补充验证：随后在系统 Node 24.14.0 下，政策列表未知状态拒绝和联调地址保护加入后，构建通过、Vitest 25/25、包含隔离后端的 Edge 18/18。浏览器联调用例限制 API 源为本机 8000；该限制不证明服务连接的是隔离数据库。

补充验证：系统 Node 24.14.0 下最新构建通过、Vitest 25/25、启用隔离后端的 Edge 19/19。新增模拟网络中断和 401 测试，核对分析请求不自动重试及未生成结论；浏览器历史导航失效刷新由真实隔离后端用例覆盖。

后续契约依赖：PR #9 的 M1 评论描述未推送 rc.4，拟增加 `specificMarketSince`（date）及 `independentLegalEntity`（boolean）。当前分支仍为 rc.3，须待 M1 提供可核查契约后重新生成类型、同步映射和测试；现有布尔控件按单位处理。不能将评论视为已合入的接口。

## 2026-11-01 同步与 rc.5 验收进度

当前工作分支已快进至 `fce0e05`（PR #12 合并），M3 PR #13 的数据也已随主分支更新；上文关于 PR #9 未合并、main 仍为 rc.3 或条件表为空的描述为 10 月 30 日历史状态。生成类型检查通过，新增 `specificMarketSince` 日期和 `independentLegalEntity` 布尔事实的前端映射与测试，日期期间与画像期间保持一致；`manual_review` 结果的证据不足和后端理由在浏览器模拟中验证。

本次前端构建通过、Vitest 28/28、Edge 19 通过及 1 项真实后端用例跳过；后端全量 583 passed、48 skipped、76 subtests passed，含政策数据闸门和 OpenAPI 对齐的 29 项通过。测试日志有依赖弃用等警告。Docker 引擎目前未就绪，尚未执行隔离库 006 迁移或 rc.5 真实浏览器联调，旧 rc.3 的真实联调不能代替。`data/evaluation/labels.csv` 仍只有表头，M3 预期案例及双人标注验收仍缺证据；契约联合签收和 M3 文案复核也不能从 CI 或合并推定完成。

原 M2 工作备份在工作区上级的 `m2-phase3-backup-20261101-155404.zip`，stash `M2 phase3 before rc5 sync 2026-11-01` 保留。未提交或推送；不要丢弃备份或 stash 直到确认原工作完整。

### 2026-11-01：Docker 恢复与 rc.5 真实链路

Docker Desktop 因 `dockerInference` 和 `engine.sock` 运行时重解析点不可访问而崩溃。关闭报错进程后保留并重命名运行目录，停止 Docker 专用 WSL 发行版并重启，Docker Engine 29.3.1 恢复；原运行目录保存在 `%LOCALAPPDATA%\Docker\run-codex-backup-20261101`、`run-codex-backup-20261101-b` 和 `%LOCALAPPDATA%\docker-secrets-engine-codex-backup-20261101`，未恢复出厂设置或删除容器数据。

新建仅本机可访问的临时 PostgreSQL，001–006 迁移成功；后端数据库测试 **37 passed**。fixture FastAPI 基于 `fce0e05`，完整浏览器测试 **20/20**；随后扩展真实后端用例，验证细分市场日期和独立法人布尔事实实际写入、人工核查条件得到证据不足并展示理由，定向真实用例 **1/1**。政策由测试夹具直接发布，是合成占位，不代表正式政策审查。

发现跨成员问题：GET `/api/v1/health` 返回版本 `1.0.0-rc.3`，而当前 `contracts/openapi.yaml` 为 rc.5；原因是 `services/api/app/config.py` 的 `APP_VERSION` 仍为 rc.3，需 M1 修复并补回归。`data/evaluation/labels.csv` 仍只有表头，M3 双人标注案例、正式原文与文案的验收仍待确认；不能宣称最终验收通过。
