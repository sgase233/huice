-- 002：把评估运行与幂等所需的结构补齐，并修正与契约不一致的事实来源枚举。
--
-- 背景（对应 docs/IMPLEMENTATION_AND_INTERFACE.md）：
--   * 契约 FactSource 只有 user_input / synthetic_material，001 里写成了 verified_document，
--     两者必须一致，否则合成演示材料无法入库。
--   * 契约 AssessmentRun 必带 period 与 supersedesRunId，001 的 assessment_run 没有这两列。
--   * API-05 要求按 Idempotency-Key 防重复抽取，需要一张只追加的幂等记录表。
--   * 判断结果（条件结果/缺失事实/补问/解释）要跟着 run 一起成为历史，assessments
--     需要一列快照；trace_step 的状态枚举也要与契约 AgentStatus 一致。
--
-- 本脚本可重复执行：ALTER 用 IF EXISTS / IF NOT EXISTS，约束用先删后建。

BEGIN;

-- 1. 事实来源枚举与契约对齐 -------------------------------------------------

ALTER TABLE enterprise_fact DROP CONSTRAINT IF EXISTS enterprise_fact_source_check;
ALTER TABLE enterprise_fact
    ADD CONSTRAINT enterprise_fact_source_check
    CHECK (source IN ('user_input', 'synthetic_material'));

-- 2. 评估运行的期间与重算关联 ----------------------------------------------

ALTER TABLE assessment_run ADD COLUMN IF NOT EXISTS period_start date;
ALTER TABLE assessment_run ADD COLUMN IF NOT EXISTS period_end date;
ALTER TABLE assessment_run
    ADD COLUMN IF NOT EXISTS supersedes_run_id uuid REFERENCES assessment_run (id) ON DELETE SET NULL;

ALTER TABLE assessment_run DROP CONSTRAINT IF EXISTS assessment_run_period;
ALTER TABLE assessment_run
    ADD CONSTRAINT assessment_run_period
    CHECK (period_start IS NULL OR period_end IS NULL OR period_end >= period_start);

-- 期间与重算关联也是运行快照的一部分，写入后不得修改
CREATE OR REPLACE FUNCTION guard_assessment_run_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.id <> OLD.id
       OR NEW.enterprise_id <> OLD.enterprise_id
       OR NEW.as_of <> OLD.as_of
       OR NEW.fact_version <> OLD.fact_version
       OR NEW.model_or_rule_version <> OLD.model_or_rule_version
       OR NEW.created_at <> OLD.created_at
       OR NEW.period_start IS DISTINCT FROM OLD.period_start
       OR NEW.period_end IS DISTINCT FROM OLD.period_end
       OR NEW.supersedes_run_id IS DISTINCT FROM OLD.supersedes_run_id THEN
        RAISE EXCEPTION '评估运行只允许更新 status 与 finished_at（run %）', OLD.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

-- 3. 抽取幂等记录（只追加） -------------------------------------------------

CREATE TABLE IF NOT EXISTS idempotency_record (
    scope               text NOT NULL CHECK (length(btrim(scope)) > 0),
    idempotency_key     uuid NOT NULL,
    request_fingerprint char(64) NOT NULL CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
    response            jsonb NOT NULL,
    revision            uuid NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (scope, idempotency_key)
);

CREATE OR REPLACE TRIGGER idempotency_record_no_update_delete
    BEFORE UPDATE OR DELETE ON idempotency_record
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();

-- 4. 判断结果快照与轨迹状态枚举对齐契约 -------------------------------------

-- 一次判断的完整结果（条件结果、缺失事实、补问、准备清单、解释）是历史的一部分，
-- 与 citation 一起随 run 落库；读取时只重算 validity/staleReasons/validUntil。
ALTER TABLE assessment
    ADD COLUMN IF NOT EXISTS snapshot jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE assessment DROP CONSTRAINT IF EXISTS assessment_snapshot_object;
ALTER TABLE assessment
    ADD CONSTRAINT assessment_snapshot_object CHECK (jsonb_typeof(snapshot) = 'object');

-- 001 里 trace_step.status 只有 succeeded/failed/skipped，契约 AgentStatus 是
-- ok/needs_review/retryable_error/fatal_error。先把历史值搬到新枚举，再换约束；
-- 只追加表本来禁止 UPDATE，这里临时摘掉该触发器，改完立刻恢复。
DROP TRIGGER IF EXISTS trace_step_no_update_delete ON trace_step;
UPDATE trace_step
   SET status = CASE status
                    WHEN 'succeeded' THEN 'ok'
                    WHEN 'failed' THEN 'fatal_error'
                    WHEN 'skipped' THEN 'ok'
                    ELSE status
                END
 WHERE status IN ('succeeded', 'failed', 'skipped');
ALTER TABLE trace_step DROP CONSTRAINT IF EXISTS trace_step_status_check;
ALTER TABLE trace_step
    ADD CONSTRAINT trace_step_status_check
    CHECK (status IN ('ok', 'needs_review', 'retryable_error', 'fatal_error'));
CREATE OR REPLACE TRIGGER trace_step_no_update_delete
    BEFORE UPDATE OR DELETE ON trace_step
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();

INSERT INTO schema_migration (id, description)
VALUES ('002_evaluation_and_idempotency', '运行期间与重算关联、事实来源枚举对齐、抽取幂等记录')
ON CONFLICT (id) DO NOTHING;

COMMIT;
