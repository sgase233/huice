-- 004：失效事件补上事实期间，失效按 key + 期间 精确匹配。
--
-- 背景（对应 docs/IMPLEMENTATION_AND_INTERFACE.md 2.7 的 FR-07 口径）：
--   001 的 invalidation_event 只有 fact_key，没有期间列。写入时是靠
--   assessment_dependency 的 key + 期间 才命中依赖的，但事件本身不落期间，读取
--   （get_run 重算有效性）只能按 key 匹配。于是一个 run 同时依赖同一个键的两个
--   期间（例如年度 taxableIncome 与 2025 年度 taxableIncome）时，改动其中一条会把
--   另一条依赖也标成 stale，changedFactRefs 还会列出没被改的期间。
--
-- 本脚本可重复执行；只加列与约束，不改动已有行。004 之前写入的事件期间为空，
-- 读取端按「该键的任一期间」保守匹配（宁可多标 stale，不可漏标）。
-- 只追加触发器针对 UPDATE/DELETE 行操作，ALTER TABLE 不受影响。

BEGIN;

ALTER TABLE invalidation_event ADD COLUMN IF NOT EXISTS fact_period_start date;
ALTER TABLE invalidation_event ADD COLUMN IF NOT EXISTS fact_period_end   date;

-- 期间要么两列都有、要么都没有；有值时结束不得早于开始。
ALTER TABLE invalidation_event DROP CONSTRAINT IF EXISTS invalidation_event_fact_period_check;
ALTER TABLE invalidation_event
    ADD CONSTRAINT invalidation_event_fact_period_check
    CHECK (
        (fact_period_start IS NULL) = (fact_period_end IS NULL)
        AND (fact_period_start IS NULL OR fact_period_end >= fact_period_start)
    );

-- get_run 按 run_id 取事件并按时间排序，建一个对应索引。
CREATE INDEX IF NOT EXISTS invalidation_event_run_idx
    ON invalidation_event (run_id, created_at);

INSERT INTO schema_migration (id, description)
VALUES ('004_invalidation_event_period', '失效事件记录事实期间，按 key + 期间 精确失效')
ON CONFLICT (id) DO NOTHING;

COMMIT;
