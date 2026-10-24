# M2 前端阶段一、二

本目录是 React + TypeScript + Vite 界面。五个页面用于**固定合成样本的静态演示**；当前企业画像不会调用资格判断 API，页面中的结论与清单不能用于实际申报。

## 页面信息层级与组件

`企业画像 → 机会列表 → 政策详情/证据 → 清单/报告` 是企业用户路径；`审核预览` 只用于团队查看未来后台布局。页面顶端持续显示演示标记，左侧导航在窄屏变为横向可滚动导航。状态由文字与颜色共同表达；`StatusBadge` 分开呈现四态、失效、关闭。`EvidenceRow` 呈现合成引用，`PreparationList` 呈现按税收/资金区分的清单。

界面样本在 `src/demo.ts`，依据 OpenAPI 字段组织，刷新后恢复默认状态。样本来源链接仅指向政府网站首页，明确标为占位，不是政策条款出处。`src/api/client.ts` 是后续真实接口的唯一 HTTP 出口；页面目前不依赖尚未实现的 API-02/03/08—14。后台导入与审核按钮禁用，不发送管理请求。

## 运行与验证

按团队约定使用 Node.js 22.12+ 的 22.x 与随附 npm。复制 `.env.example` 为不提交的 `.env.local`，只填公开 API 地址，不能放令牌。M1 提供服务后可检查 `/api/v1/health`、CORS 与错误信封；这不代表其余接口已实现。

```powershell
cd C:\Users\刘佳熙\Desktop\ICT\huice\apps\web
npm ci
npm run dev
npm run build
npm test
npm run test:e2e
```

前端地址 `http://127.0.0.1:5173`。浏览器测试使用本机 Microsoft Edge（Chromium 内核）；测试机需安装 Edge。`npm run build` 先检查 TypeScript 再构建静态产物。

## 后续联调边界

M1 的阶段一、二只实现 API-01/04/15/16。待 API-02/03/08—14 和授权门禁可用后，再接画像持久化、真实机会分析、报告下载及后台流程。当前 M2 对 `1.0.0-rc.2` 的类型生成一致性做复核，M1 审查日志中的 R1—R5 仍待修复/复测，契约尚未联合签收。
