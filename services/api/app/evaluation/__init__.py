"""评测输入/运行快照的导出与核对（``docs/product/EVALUATION_FREEZE.md`` §5.2；
该声明由 M3 起草，尚未合入 main，见 PR #36）。

这里只做「把库里的东西说清楚」，不产出任何评测指标。
"""

from .snapshot import (
    CONFIG_FINGERPRINT_KEYS,
    MANIFEST_VERSION,
    SnapshotError,
    collect_input_snapshot,
    config_fingerprint,
    diff_snapshots,
    expression_fingerprint,
    sha256_hex,
)

__all__ = [
    "CONFIG_FINGERPRINT_KEYS",
    "MANIFEST_VERSION",
    "SnapshotError",
    "collect_input_snapshot",
    "config_fingerprint",
    "diff_snapshots",
    "expression_fingerprint",
    "sha256_hex",
]
