"""政策发现（E2 / #47）：抓取、候选暂存与运行编排。

三个子模块，彼此不反向依赖：

* :mod:`app.discovery.fetch` —— 外网抓取的**全部**限制（白名单、重定向复检、DNS
  解析结果、体积、超时、同站间隔）。它不碰数据库，可以被单元测试直接驱动。
* :mod:`app.discovery.runner` —— 一次发现的编排：运行锁、批次轮换、判重、抽取预算。
* :mod:`app.discovery.__main__` —— ``systemd timer`` 调用的 CLI 入口。

HTTP 层（DISC-01—08）在 :mod:`app.routes.discovery`，候选提交复用现有导入链路。
"""
