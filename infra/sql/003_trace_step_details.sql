-- 003：让 trace_step 能原样支撑 API-14（契约 TraceResponse / TraceStep）。
--
-- 背景（对应 docs/IMPLEMENTATION_AND_INTERFACE.md 2.8 与 4.3）：
--   * 001 的 trace_step 只有一个 summary，而契约要求 inputSummary 与 outputSummary
--     分开；把两者拼成一句再拆开是不可逆的，所以补两列。
--   * TraceStep 还有 policyId / policyVersion / errorCode / humanAction 四个可空字段，
--     001 没有对应的列。
--   * 001 里导入步骤写入的 stage 是 policy_import，契约 AgentStage 枚举没有这个值，
--     读取时会直接违反 schema；统一收口为 ingestion。
--
-- 本脚本可重复执行；stage 归一化在临时摘掉只追加触发器的窗口内完成，结束后立刻恢复。

BEGIN;

ALTER TABLE trace_step ADD COLUMN IF NOT EXISTS input_summary text;
ALTER TABLE trace_step ADD COLUMN IF NOT EXISTS output_summary text;
ALTER TABLE trace_step ADD COLUMN IF NOT EXISTS policy_id uuid;
ALTER TABLE trace_step ADD COLUMN IF NOT EXISTS policy_version text;
ALTER TABLE trace_step ADD COLUMN IF NOT EXISTS error_code text;
ALTER TABLE trace_step ADD COLUMN IF NOT EXISTS human_action text;

-- 旧的导入步骤用的是 policy_import，契约枚举里没有；先搬值再加约束。
DROP TRIGGER IF EXISTS trace_step_no_update_delete ON trace_step;

UPDATE trace_step
   SET input_summary = COALESCE(input_summary, summary),
       output_summary = COALESCE(output_summary, '')
 WHERE input_summary IS NULL;

UPDATE trace_step SET stage = 'ingestion' WHERE stage = 'policy_import';

ALTER TABLE trace_step DROP CONSTRAINT IF EXISTS trace_step_stage_check;
ALTER TABLE trace_step
    ADD CONSTRAINT trace_step_stage_check
    CHECK (stage IN (
        'ingestion', 'extraction', 'citation_validation', 'human_review',
        'profile', 'retrieval', 'evaluation', 'questioning', 'explanation'
    ));

CREATE OR REPLACE TRIGGER trace_step_no_update_delete
    BEFORE UPDATE OR DELETE ON trace_step
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();

INSERT INTO schema_migration (id, description)
VALUES ('003_trace_step_details', '轨迹拆分输入/输出摘要、政策关联与阶段枚举收口')
ON CONFLICT (id) DO NOTHING;

COMMIT;
