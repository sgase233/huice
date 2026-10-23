# 接口契约

`openapi.yaml` 是 HTTP 字段的单一真源，当前为 v0.2 实现前草案，包含16个接口、请求/响应 schema 和合成示例，尚无可调用业务服务。人工可读说明见[功能实现逻辑与接口协作约定](../docs/IMPLEMENTATION_AND_INTERFACE.md)，补充了旧开发计划未明确的版本、事实期间、清单与后台读取约束。

M1 维护 OpenAPI，M2 审查前端可调用性，M3 审查业务含义。破坏性变更先更新契约与说明，由 M1/M2 确认并附迁移说明。实现 FastAPI 后还需比较 `/openapi.json`、实际返回与本文件；静态检查不等于后端契约验收。

从 `huice/` 运行：

```powershell
python contracts/generate_types.py          # 更新前端 wire types
python contracts/generate_types.py --check  # 只检查，不写文件
python -m unittest discover -s services/api/tests -p test_contract.py -v
```

`generate_types.py` 由 M1 维护，生成的 `apps/web/src/api/types.ts` 由 M2 审查，禁止手工改生成内容。生成器只处理本契约使用的 ref、union、object、array、enum 和 primitive；新增 schema 语法时同步扩展生成器及测试。TypeScript 不执行正则、期间或业务校验，后端必须单独实现。

静态工具使用 Python、PyYAML、jsonschema；本次实际版本在根目录协作约定的验证记录中。M1 完成后端环境时将其纳入测试依赖并锁定，不把当前机器版本冒称为已验证的部署依赖。
