#!/usr/bin/env python3
"""建立审核事件基线（E3 / B3，迁移 `012` 的 `policy_review_event`）。

D5 要统计「人工审核完成」。历史版本只有 `policy_version.reviewed_at`，没有事件行；
基线脚本把**已发布版本**一次性补成 `actor_kind='baseline'`、`review_revision=0` 的事件：

- `reviewed_at` 非空 → `occurred_at = reviewed_at`、`legacy_source='reviewed_at'`；
- `reviewed_at` 为空 → `occurred_at = T_review`（本次执行时刻）、`legacy_source='unknown'`
  （防御分支：现行约束 `policy_version_reviewed` 保证已发布行必有 `reviewed_at`，
  这条只在日后放宽约束时才可能走到）。

基线事件**不计 `reviewsCompleted`**，只用于 `pendingAtEndOfDay` 的历史回放——它证明不了
「这个版本哪天被哪个人审完」（M3 的 D-9 口径）。

两个覆盖起点按**列**幂等（`METRICS_SPEC` §2.3）：

- `review_events_from` / `review_source` 由本脚本写；
- `run_lifecycle_from` 由 B2 的服务启动时写；
- 谁先跑都行，各自只写自己那一列。`review_events_from` 已有值时本脚本直接退出 0。

用法（ECS，venv 解释器）::

    systemd-run --pipe --wait --collect -p EnvironmentFile=/etc/huice/api.env \\
      -p WorkingDirectory=/opt/huice/app \\
      /opt/huice/venv/bin/python /opt/huice/app/infra/deploy/backfill-review-baseline.py
    # 确认输出后再加 --apply

退出码：0 成功（含「已有基线、跳过」）；2 数据或环境不合法；3 写库失败。
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone

SCRIPT_DIR = None


def _run(apply: bool) -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("缺少环境变量 DATABASE_URL（不打印取值）。", file=sys.stderr)
        return 2
    try:
        import psycopg
    except ModuleNotFoundError as exc:
        print(f"缺少依赖 {exc.name}：请用项目虚拟环境的解释器运行。", file=sys.stderr)
        return 2

    logging.getLogger("psycopg.pool").setLevel(logging.CRITICAL)
    now = datetime.now(timezone.utc)
    written = 0
    try:
        with psycopg.connect(dsn, autocommit=False) as conn:
            # 与「导入/发布」互斥：基线只补已发布版本，期间不能有版本被发布或改写。
            conn.execute("LOCK TABLE policy_version IN SHARE ROW EXCLUSIVE MODE")

            row = conn.execute(
                "SELECT review_events_from FROM collection_baseline WHERE id = 'default'"
            ).fetchone()
            if row is not None and row[0] is not None:
                print(
                    f"已有审核事件基线（review_events_from={row[0].isoformat()}），跳过。"
                )
                conn.rollback()
                return 0

            versions = conn.execute(
                "SELECT id, policy_id, reviewed_at FROM policy_version "
                "WHERE review_status = 'published' ORDER BY id"
            ).fetchall()
            print(f"已发布版本 {len(versions)} 个，将补 {len(versions)} 条基线事件。")
            if not versions:
                # 没有已发布版本也要把起点写上：从这一刻起基线是完整的（空集）。
                pass

            unknown = 0
            if apply:
                for version_id, policy_id, reviewed_at in versions:
                    occurred = reviewed_at or now
                    legacy = "reviewed_at" if reviewed_at is not None else "unknown"
                    if reviewed_at is None:
                        unknown += 1
                    cursor = conn.execute(
                        "INSERT INTO policy_review_event "
                        "(policy_version_id, policy_id, review_revision, actor_kind, "
                        " actor_user_id, occurred_at, legacy_source) "
                        "VALUES (%s, %s, 0, 'baseline', NULL, %s, %s) "
                        "ON CONFLICT (policy_version_id, review_revision) DO NOTHING",
                        (version_id, policy_id, occurred, legacy),
                    )
                    written += cursor.rowcount

                source = "unknown" if unknown else "reviewed_at"
                conn.execute(
                    "INSERT INTO collection_baseline (id) VALUES ('default') "
                    "ON CONFLICT (id) DO NOTHING"
                )
                conn.execute(
                    "UPDATE collection_baseline SET review_events_from = %s, review_source = %s "
                    "WHERE id = 'default' AND review_events_from IS NULL",
                    (now, source),
                )
                conn.commit()
            else:
                conn.rollback()
    except Exception as exc:  # 不回显连接串
        print(f"数据库操作失败（{type(exc).__name__}）。", file=sys.stderr)
        return 3

    if apply:
        print(f"已写入 {written} 条基线事件；review_events_from={now.isoformat()}。")
    else:
        print("干跑完成，未写库。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="建立审核事件基线（B3）")
    parser.add_argument("--apply", action="store_true", help="真正写库；不加只打印计划")
    args = parser.parse_args(argv)
    return _run(args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
