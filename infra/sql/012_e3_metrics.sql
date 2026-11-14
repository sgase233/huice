-- 012_e3_metrics.sql（E3 / D5 采集，B2）
--
-- 目的：让「审核事件」「运行生命周期」这两类事实从这一刻起可被查询，此前的一段
-- 历史按「未采集」对待，而不是当成 0。
--
-- 本迁移只建结构，不做任何回填：
--   * policy_review_event 的存量事件由 infra/deploy/backfill-review-baseline.py 写入
--     （B3，一次性脚本，基线事件 actor_kind='baseline'）；
--   * collection_baseline 的两个起点各自独立、各自幂等：review_events_from 由基线
--     脚本写，run_lifecycle_from 由 B2 上线时的初始化写。未上线的那个留 NULL，
--     对应指标整体返回 not_collected，不返回 0。
--
-- 三个必须守住的点：
--
--   1. policy_version.review_revision 用 ADD COLUMN ... NOT NULL DEFAULT 1。
--      PG 11+ 这是纯元数据操作，不写行、不触发行触发器——已发布版本一行都不动。
--      用 UPDATE 回填会撞 001 的 policy_version_guard_published（与 011 同一个坑）。
--
--   2. assessment_run.lock_owner_pid / lock_owner_started_at 只在 INSERT 时写、之后冻结。
--      判定「原连接是否断开」必须把 (pid, backend_start) 成对比：PID 会被复用，只比
--      pid 会把活着的新连接误判成死掉的旧连接，进而把仍在跑的运行标成 interrupted。
--
--   3. status 只增加 interrupted 一个值。契约里保留 queued 是为了枚举稳定，但当前
--      实现不产生 queued——契约描述由下一版契约统一更正（见 E3 方案 §5）。
--
-- 迁移连跑两遍必须可重复执行（db 夹具本身就是两遍）。

BEGIN;

-- 1. 审核事件 ---------------------------------------------------------------
--
-- 一次「审核完成」一条。review_revision 是该版本第几次被审核：首次发布 1，之后每次
-- 重新审核 +1。基线事件取 0——它们不是新一轮审核，只用于回放「当时还挂着多少条」。

CREATE TABLE IF NOT EXISTS policy_review_event (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    policy_version_id uuid NOT NULL,
    policy_id         uuid NOT NULL,
    review_revision   integer NOT NULL CHECK (review_revision >= 0),
    actor_kind        text NOT NULL CHECK (actor_kind IN ('user', 'baseline', 'legacy_unknown')),
    actor_user_id     uuid REFERENCES app_user (id) ON DELETE RESTRICT,
    occurred_at       timestamptz NOT NULL,
    legacy_source     text CHECK (legacy_source IN ('reviewed_at', 'unknown')),
    created_at        timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT policy_review_event_unique UNIQUE (policy_version_id, review_revision),
    CONSTRAINT policy_review_event_version_fk
        FOREIGN KEY (policy_version_id, policy_id)
        REFERENCES policy_version (id, policy_id) ON DELETE RESTRICT,
    CONSTRAINT policy_review_event_actor CHECK (
        (actor_kind = 'user'           AND actor_user_id IS NOT NULL AND legacy_source IS NULL)
     OR (actor_kind = 'baseline'       AND actor_user_id IS NULL     AND legacy_source IS NOT NULL)
     OR (actor_kind = 'legacy_unknown' AND actor_user_id IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS policy_review_event_occurred_idx
    ON policy_review_event (occurred_at);

-- 2. 两个覆盖起点 -----------------------------------------------------------
--
-- 两个起点来自不同批次（审核基线 B3、运行生命周期 B2），上线时间可以不同，
-- 所以不共用一个 T0。按「列」而不是按「行」幂等：谁先建行都行，各自只写自己那列。

CREATE TABLE IF NOT EXISTS collection_baseline (
    id                  text PRIMARY KEY CHECK (id = 'default'),
    review_events_from  timestamptz,
    review_source       text CHECK (review_source IN ('reviewed_at', 'unknown')),
    run_lifecycle_from  timestamptz,
    note                text,
    created_at          timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT collection_baseline_review_source
        CHECK ((review_events_from IS NULL) = (review_source IS NULL))
);

-- 3. 版本侧：审核轮次 -------------------------------------------------------

ALTER TABLE policy_version
    ADD COLUMN IF NOT EXISTS review_revision integer NOT NULL DEFAULT 1;

-- 4. 运行侧：失败分类、锁持有者、中断 -------------------------------

ALTER TABLE assessment_run ADD COLUMN IF NOT EXISTS error_category text;
ALTER TABLE assessment_run ADD COLUMN IF NOT EXISTS lock_owner_pid integer;
ALTER TABLE assessment_run ADD COLUMN IF NOT EXISTS lock_owner_started_at timestamptz;

ALTER TABLE assessment_run DROP CONSTRAINT IF EXISTS assessment_run_status_check;
ALTER TABLE assessment_run ADD CONSTRAINT assessment_run_status_check
    CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'interrupted'));

-- error_category 只保证取值域，不强制「failed 必有类别」：失败行此前根本不存在，
-- 硬约束会把将来的边界钉死；由代码与用例保证写全。
ALTER TABLE assessment_run DROP CONSTRAINT IF EXISTS assessment_run_error_category_check;
ALTER TABLE assessment_run ADD CONSTRAINT assessment_run_error_category_check
    CHECK (error_category IS NULL OR error_category IN
           ('policy_not_published', 'model_unavailable', 'dependency_unavailable', 'internal'));

-- 中断扫描只找 running 行：局部索引比全表索引小一个量级。
CREATE INDEX IF NOT EXISTS assessment_run_running_idx
    ON assessment_run (created_at) WHERE status = 'running';

-- 5. 守卫：放开 error_category，锁两列与身份信息继续冻结 --------------------

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
       OR NEW.supersedes_run_id IS DISTINCT FROM OLD.supersedes_run_id
       OR NEW.user_id IS DISTINCT FROM OLD.user_id
       OR NEW.lock_owner_pid IS DISTINCT FROM OLD.lock_owner_pid
       OR NEW.lock_owner_started_at IS DISTINCT FROM OLD.lock_owner_started_at THEN
        RAISE EXCEPTION
            '评估运行只允许更新 status/finished_at/error_category（run %）', OLD.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

INSERT INTO schema_migration (id, description) VALUES (
    '012_e3_metrics', 'D5 采集：审核事件、两个覆盖起点、运行生命周期与中断标记'
) ON CONFLICT (id) DO NOTHING;

COMMIT;
