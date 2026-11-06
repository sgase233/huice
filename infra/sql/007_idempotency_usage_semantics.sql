-- 007：给幂等记录标记用量语义的来源（契约 1.0.0-rc.7）
--
-- 背景：rc.6 的抽取把上游 `usage` 的两个计数**各自**用 `or 0` 兜底，且不写任何标记
-- （`integrations/llm.py` 的 `usage.get(...) or 0`）。一条 rc.6 记录里的 `120/0`
-- 分不清「上游只给了 prompt」还是「completion 真的是 0」，`0/45` 反之，`0/0` 可能
-- 两个都缺——只看数值判不出这条记录的用量是否完整。
--
-- rc.7 起未知一定带 `tokensUnknown: true`，于是**记录本身**就有语义；但要信它，得
-- 先知道这条记录出自哪一代服务端。这里不碰 `response` 本体（它必须与线路形状保持
-- 一致，回退到 rc.6 时同键重放才解析得动），只加一列来源标记：
--
--   'legacy'   —— 本迁移之前写入的记录，用量可能被兜底；
--   'explicit' —— rc.7 起写入，未知一定带标记，缺省即已知。
--
-- 历史行由 DEFAULT 直接落 'legacy'，无需回填；回退到 rc.6 期间写入的行同样落
-- 'legacy'（旧代码不写这一列），语义也对。可重复执行。

BEGIN;

ALTER TABLE idempotency_record
    ADD COLUMN IF NOT EXISTS usage_semantics text NOT NULL DEFAULT 'legacy';

ALTER TABLE idempotency_record
    DROP CONSTRAINT IF EXISTS idempotency_record_usage_semantics_check;
ALTER TABLE idempotency_record
    ADD CONSTRAINT idempotency_record_usage_semantics_check
    CHECK (usage_semantics IN ('legacy', 'explicit'));

INSERT INTO schema_migration (id, description) VALUES (
    '007_idempotency_usage_semantics',
    '幂等记录标记用量语义来源（legacy/explicit），重放时据以判断历史用量是否可信'
) ON CONFLICT (id) DO NOTHING;

COMMIT;
