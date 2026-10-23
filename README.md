# 惠策协同项目骨架

当前业务应用仍是注释骨架，尚不可运行。功能实现、参数传递、前后端约束和逐文件分工见[功能实现逻辑与接口协作约定](./docs/IMPLEMENTATION_AND_INTERFACE.md)。环境配置仍参考[原开发计划](./docs/brief/惠策协同_三人分工与开发计划_截至11月15日.md)第 4.1 节。

团队 Git 分支、提交和推送要求见[工作手册](./WORKING_GUIDE.md)：M1/M2/M3 各自建分支；每次提交和远程推送前均须取得明确同意。

`contracts/openapi.yaml` 已补为包含 16 个接口的 OpenAPI 3.1 草案（原14个接口加后台草稿列表/详情），`apps/web/src/api/types.ts` 由它生成；这些定义不表示服务已经实现。M1/M2 审查后再冻结契约。

- M1：`services/api/`、`infra/`、`contracts/` 与根配置。
- M2：`apps/web/`。
- M3：`data/`、`docs/product/`、`docs/ui/`、`docs/demo/`。

实现时先按第 4.1 节安装工具、复制 `.env.example`、`infra/local/.env.example` 和 `apps/web/.env.example`，再按接口契约提交最小可运行版本，补测试、部署和演示证据。不要提交凭据或真实企业材料。

多智能体内部交接与调度边界见 `services/api/AGENT_PROTOCOL.md`；M1 负责实现，M2 通过 API-14 展示脱敏轨迹，M3 复核政策依据和中文结果。

可独立执行的静态契约检查（从本目录运行，需要 PyYAML、jsonschema；不连接数据库或云端）：

```powershell
python contracts/generate_types.py --check
python -m unittest discover -s services/api/tests -p test_contract.py -v
```

M1 修改契约后执行 `python contracts/generate_types.py`，由 M2 审查生成的类型差异。依赖版本记录在 `contracts/README.md`，业务接口与浏览器验收仍待实现。
