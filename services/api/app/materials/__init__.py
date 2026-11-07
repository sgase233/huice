"""材料要求（M3 的 ``data/policies/material_requirements.csv``）的导入与运行期展开。

CSV 是 M3 的产物，本包只**读**它，不写、不改：解析与校验在 :mod:`.catalog`，落库与
按政策取回在 :class:`app.integrations.storage.PolicyRepository`，命令行入口在
:mod:`.__main__`。
"""
