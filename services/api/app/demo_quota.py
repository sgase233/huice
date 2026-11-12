"""演示会话的建样配额参数（#54 D1.5）。

单独成模块是为了断开循环依赖：``identity`` 构造它、``storage`` 在建企业的事务里消费它，
而 ``integrations/accounts.py`` 已经反向导入 ``storage``。放在任一处都会让那条边成环。

**为什么把参数而不是已占的名额传下去**：占额必须和「建企业」在同一个事务里完成。
先占、再在另一个事务里建企业，中间进程退出或退额本身失败，都会出现「名额没了、样本
没建成」（PR #61 复审 P2）。参数传进去，占额语句与 INSERT 同成同败。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class DemoQuota:
    """一次建样请求的配额参数：谁的名额、什么时候过期、上限多少。"""

    sid_hash: str
    expires_at: datetime
    limit: int
