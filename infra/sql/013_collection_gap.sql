-- 013_collection_gap.sql（E3 / B3）
--
-- 目的：给指标端点一个可以回答「这段时间采集中断过吗」的证据表。
--
-- 为什么不在指标里直接猜：`collection_baseline` 的起点只说明「从这一刻起有记录」，
-- 证明不了之后没断过。断电、实例重建、库不可用都可能让采集停一段，而这些时段里
-- 指标值要么缺失、要么看起来像「真实的 0」。本表记录**已知的**中断区间；
-- 没有任何记录 ≠ 没有中断——读侧发现证据不可读时仍要降级（`partial`）。
--
-- 数据来源：
--   1) 主机侧事件日志 `/var/lib/huice/collection-lifecycle.jsonl`——由
--      `infra/deploy/record-collection-event.py` 写入，库挂了也能记；导入脚本
--      按 `event_id` 幂等转存到本表（`source='host-journal'`）。
--   2) 运维人工补记（`source='manual'`），例如计划内维护、实例迁移。
--
-- `gap_to IS NULL` 表示**未闭合**：只有 `stop` 没有 `start`（进程被杀、机器断电
-- 尚未恢复），按「持续到当前」解释。

BEGIN;

CREATE TABLE IF NOT EXISTS collection_gap (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- 证据来源：主机日志导入 / 人工补记
    source       text NOT NULL CHECK (source IN ('host-journal', 'manual')),
    -- 幂等键：同一事件重复导入不新增行。人工补记也要给稳定 id。
    event_id     text NOT NULL,
    host         text NOT NULL,
    kind         text NOT NULL CHECK (kind IN
                     ('stop', 'start', 'migrate', 'rollback', 'maintenance')),
    -- 影响范围：只断运行采集、只断审核事件、还是两者都断
    scope        text NOT NULL CHECK (scope IN ('run', 'review', 'all')),
    gap_from     timestamptz NOT NULL,
    -- NULL = 未闭合，持续到当前
    gap_to       timestamptz,
    reason       text,
    recorded_by  text,
    created_at   timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT collection_gap_event_unique UNIQUE (event_id),
    CONSTRAINT collection_gap_interval CHECK (gap_to IS NULL OR gap_to >= gap_from)
);

CREATE INDEX IF NOT EXISTS collection_gap_from_idx ON collection_gap (gap_from);
CREATE INDEX IF NOT EXISTS collection_gap_open_idx ON collection_gap (gap_from)
    WHERE gap_to IS NULL;

INSERT INTO schema_migration (id, description) VALUES (
    '013_collection_gap', 'B3 采集覆盖：中断区间证据表（主机日志导入与人工补记）'
) ON CONFLICT (id) DO NOTHING;

COMMIT;
