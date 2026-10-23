"""外部依赖适配：对象存储、数据库仓储、缓存、模型客户端。

模型侧统一走 ``llm.py``：``fixture`` 为离线替身，``deepseek`` 调 DeepSeek 的
OpenAI 兼容接口。原华为云 MaaS 适配器已删除（队长 2026-10-23 决定不使用 MaaS，
改用 DeepSeek 替代）。
"""
