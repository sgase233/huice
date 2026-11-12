"""政策版本校验发现的共享常量。

``policy_validation_finding`` 里存两类结论：

* **条件级**（``criterion_id`` 非空）：某条条件的引文、阈值方向、AST 等校验结果，
  每次保存条款或重新抽取都会整体重算；
* **来源级**（``criterion_id`` 为空）：描述来源本身的状态，例如「页面有附件但本轮
  没下载、没纳入抽取」。它不因某条条件被改好而消失，重算条件 findings 时也不该被
  一并清掉。

:data:`PRESERVED_SOURCE_FINDING_CODES` 列出整体替换 findings 时必须从库里原样保留
的码。只保留这些码，其它旧结论（包括过时的条件错误、旧的「合取为空」）照常清掉，
避免修复后仍挂着已经解决的提示。
"""

#: 页面有附件、但附件没有下载也没有纳入抽取。由候选的 ``attachment_urls`` 派生。
ATTACHMENT_NOT_EXTRACTED = "ATTACHMENT_NOT_EXTRACTED"

#: 重新保存条款（API-05/06）时需要保留的既有来源级码。
PRESERVED_SOURCE_FINDING_CODES = frozenset({ATTACHMENT_NOT_EXTRACTED})
