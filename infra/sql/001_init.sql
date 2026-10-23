-- Purpose: 惠策协同初始库表（政策导入 / 企业画像 / 评估运行 / 追踪）。
-- Call: psql -v ON_ERROR_STOP=1 -f 001_init.sql（可重复执行）。
-- Output constraint: 不含任何账号、密码或真实企业数据；整份脚本在一个事务内完成。

BEGIN;

CREATE EXTENSION IF NOT EXISTS vector;

-- ---------------------------------------------------------------------------
-- 迁移记录
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_migration (
    id          text PRIMARY KEY,
    description text NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- 政策：policy 只是聚合根，版本与内容都在 policy_version
-- review_status 只保存 pending_parse/pending_review/published；
-- 「已截止」是读取时按有效期与申报窗口派生，不落库。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS policy (
    id         uuid PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS policy_version (
    id                  uuid PRIMARY KEY,
    policy_id           uuid NOT NULL REFERENCES policy (id) ON DELETE RESTRICT,
    version             text NOT NULL CHECK (length(btrim(version)) > 0),
    title               text NOT NULL CHECK (length(btrim(title)) > 0),
    policy_type         text NOT NULL CHECK (policy_type IN ('tax', 'grant')),
    issuer              text NOT NULL CHECK (length(btrim(issuer)) > 0),
    region              text NOT NULL CHECK (length(btrim(region)) > 0),
    document_number     text,
    applicable_entities jsonb NOT NULL CHECK (
                            jsonb_typeof(applicable_entities) = 'array'
                            AND jsonb_array_length(applicable_entities) > 0
                        ),
    published_at        date NOT NULL,
    effective_from      date NOT NULL,
    effective_to        date,
    application_window  jsonb,
    source_url          text NOT NULL CHECK (source_url ~ '^https://'),
    official_entry_url  text NOT NULL CHECK (official_entry_url ~ '^https://'),
    review_status       text NOT NULL DEFAULT 'pending_parse'
                        CHECK (review_status IN ('pending_parse', 'pending_review', 'published')),
    revision            uuid NOT NULL,
    imported_at         timestamptz NOT NULL DEFAULT now(),
    reviewed_at         timestamptz,
    updated_at          timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT policy_version_unique      UNIQUE (policy_id, version),
    CONSTRAINT policy_version_revision    UNIQUE (revision),
    CONSTRAINT policy_version_composite   UNIQUE (id, policy_id),
    CONSTRAINT policy_version_dates       CHECK (effective_from >= published_at),
    CONSTRAINT policy_version_end_date    CHECK (effective_to IS NULL OR effective_to >= effective_from),
    CONSTRAINT policy_version_window      CHECK (
        application_window IS NULL
        OR (
            jsonb_typeof(application_window) = 'object'
            AND (application_window ->> 'opensAt')::timestamptz
                < (application_window ->> 'closesAt')::timestamptz
        )
    ),
    CONSTRAINT policy_version_reviewed    CHECK (review_status <> 'published' OR reviewed_at IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS policy_version_list_idx
    ON policy_version (imported_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS policy_version_policy_idx
    ON policy_version (policy_id, imported_at DESC);
CREATE INDEX IF NOT EXISTS policy_version_status_idx
    ON policy_version (review_status, policy_type);

-- ---------------------------------------------------------------------------
-- 政策来源：每个版本第一个文件是正文（body），其余是附件（attachment）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS policy_source (
    id                uuid PRIMARY KEY,
    policy_version_id uuid NOT NULL REFERENCES policy_version (id) ON DELETE CASCADE,
    kind              text NOT NULL CHECK (kind IN ('body', 'attachment')),
    ordinal           integer NOT NULL CHECK (ordinal >= 0),
    source_url        text NOT NULL,
    title             text NOT NULL,
    file_name         text NOT NULL,
    mime_type         text NOT NULL,
    fetched_at        timestamptz NOT NULL,
    content_hash      text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    byte_size         integer NOT NULL CHECK (byte_size > 0 AND byte_size <= 10485760),
    text              text NOT NULL,
    storage_key       text NOT NULL,

    CONSTRAINT policy_source_ordinal UNIQUE (policy_version_id, ordinal)
);

CREATE UNIQUE INDEX IF NOT EXISTS policy_source_single_body_idx
    ON policy_source (policy_version_id)
    WHERE kind = 'body';

-- ---------------------------------------------------------------------------
-- 条款与校验发现（条款抽取属 API-05，表结构先就位）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS policy_criterion (
    policy_version_id uuid NOT NULL REFERENCES policy_version (id) ON DELETE CASCADE,
    criterion_id      uuid NOT NULL,
    title             text NOT NULL CHECK (length(btrim(title)) > 0),
    expression        jsonb NOT NULL,
    source_spans      jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(source_spans) = 'array'),
    review_finding    text NOT NULL DEFAULT 'none' CHECK (review_finding IN ('none', 'conflict')),
    validation_status text NOT NULL DEFAULT 'pending'
                      CHECK (validation_status IN ('pending', 'passed', 'failed')),
    created_at        timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (policy_version_id, criterion_id)
);

CREATE TABLE IF NOT EXISTS policy_validation_finding (
    policy_version_id uuid NOT NULL,
    ordinal           integer NOT NULL CHECK (ordinal >= 0),
    criterion_id      uuid,
    code              text NOT NULL CHECK (length(btrim(code)) > 0),
    message           text NOT NULL,
    source_spans      jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(source_spans) = 'array'),

    PRIMARY KEY (policy_version_id, ordinal),
    CONSTRAINT policy_validation_finding_criterion
        FOREIGN KEY (policy_version_id, criterion_id)
        REFERENCES policy_criterion (policy_version_id, criterion_id)
        ON DELETE CASCADE
);

-- ---------------------------------------------------------------------------
-- 企业画像：事实以 (key, 周期) 唯一；value 为空表示「未知」，不能当作不满足
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS enterprise (
    id             uuid PRIMARY KEY,
    name_synthetic text NOT NULL CHECK (length(name_synthetic) BETWEEN 1 AND 100),
    period_start   date NOT NULL,
    period_end     date NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT enterprise_period CHECK (period_end >= period_start)
);

CREATE TABLE IF NOT EXISTS enterprise_fact (
    enterprise_id     uuid NOT NULL REFERENCES enterprise (id) ON DELETE CASCADE,
    key               text NOT NULL CHECK (key IN (
                          'region', 'industry', 'establishedDate', 'taxableIncome',
                          'rdExpense', 'assetsStart', 'assetsEnd',
                          'employeeCountStart', 'employeeCountEnd', 'rdActivity'
                      )),
    period_start      date NOT NULL,
    period_end        date NOT NULL,
    unit              text NOT NULL CHECK (unit IN ('code', 'date', 'CNY', 'person', 'boolean')),
    source            text NOT NULL CHECK (source IN ('user_input', 'verified_document')),
    verified          boolean NOT NULL DEFAULT false,
    value_code        text,
    value_date        date,
    value_amount      numeric(24, 6),
    value_boolean     boolean,
    updated_at        timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (enterprise_id, key, period_start, period_end),
    CONSTRAINT enterprise_fact_period CHECK (period_end >= period_start),
    CONSTRAINT enterprise_fact_key_unit CHECK (
        (key IN ('region', 'industry') AND unit = 'code')
        OR (key = 'establishedDate' AND unit = 'date')
        OR (key IN ('taxableIncome', 'rdExpense', 'assetsStart', 'assetsEnd') AND unit = 'CNY')
        OR (key IN ('employeeCountStart', 'employeeCountEnd') AND unit = 'person')
        OR (key = 'rdActivity' AND unit = 'boolean')
    ),
    -- 至多一个值列非空；四列全空表示「未知」
    CONSTRAINT enterprise_fact_value_shape CHECK (
        (value_code IS NOT NULL AND value_date IS NULL AND value_amount IS NULL AND value_boolean IS NULL)
        OR (value_code IS NULL AND value_date IS NOT NULL AND value_amount IS NULL AND value_boolean IS NULL)
        OR (value_code IS NULL AND value_date IS NULL AND value_amount IS NOT NULL AND value_boolean IS NULL)
        OR (value_code IS NULL AND value_date IS NULL AND value_amount IS NULL AND value_boolean IS NOT NULL)
        OR (value_code IS NULL AND value_date IS NULL AND value_amount IS NULL AND value_boolean IS NULL)
    )
);

-- ---------------------------------------------------------------------------
-- 评估运行：结果四态；validity（current/stale）在读取时计算，不落库
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS assessment_run (
    id                    uuid PRIMARY KEY,
    enterprise_id         uuid NOT NULL REFERENCES enterprise (id) ON DELETE RESTRICT,
    as_of                 timestamptz NOT NULL,
    status                text NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    fact_version          text NOT NULL CHECK (fact_version ~ '^[0-9a-f]{64}$'),
    model_or_rule_version text NOT NULL,
    created_at            timestamptz NOT NULL DEFAULT now(),
    finished_at           timestamptz
);

CREATE INDEX IF NOT EXISTS assessment_run_enterprise_idx
    ON assessment_run (enterprise_id, created_at DESC);

CREATE TABLE IF NOT EXISTS assessment (
    run_id            uuid NOT NULL,
    policy_id         uuid NOT NULL REFERENCES policy (id) ON DELETE RESTRICT,
    policy_version_id uuid NOT NULL,
    result            text NOT NULL CHECK (
                          result IN ('satisfied', 'not_satisfied', 'insufficient_evidence', 'conflict')
                      ),
    citation          jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(citation) = 'array'),
    created_at        timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (run_id, policy_id),
    CONSTRAINT assessment_run_fk FOREIGN KEY (run_id)
        REFERENCES assessment_run (id) ON DELETE CASCADE,
    CONSTRAINT assessment_policy_version FOREIGN KEY (policy_version_id, policy_id)
        REFERENCES policy_version (id, policy_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS assessment_policy_idx ON assessment (policy_id);

CREATE TABLE IF NOT EXISTS assessment_dependency (
    run_id            uuid NOT NULL,
    policy_id         uuid NOT NULL,
    fact_key          text NOT NULL,
    fact_period_start date NOT NULL,
    fact_period_end   date NOT NULL,

    PRIMARY KEY (run_id, policy_id, fact_key, fact_period_start, fact_period_end),
    CONSTRAINT assessment_dependency_assessment FOREIGN KEY (run_id, policy_id)
        REFERENCES assessment (run_id, policy_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS invalidation_event (
    id         uuid PRIMARY KEY,
    run_id     uuid NOT NULL REFERENCES assessment_run (id) ON DELETE CASCADE,
    reason     text NOT NULL CHECK (reason IN ('policy_version_changed', 'fact_changed')),
    policy_id  uuid,
    fact_key   text,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- 追踪：一次请求内 stage 序列；run_id 为空表示不挂在某次评估上
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS trace_step (
    id                    uuid PRIMARY KEY,
    trace_id              uuid NOT NULL,
    run_id                uuid REFERENCES assessment_run (id) ON DELETE CASCADE,
    sequence              integer NOT NULL CHECK (sequence >= 1),
    stage                 text NOT NULL,
    status                text NOT NULL CHECK (status IN ('succeeded', 'failed', 'skipped')),
    summary               text NOT NULL,
    evidence_refs         jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(evidence_refs) = 'array'),
    model_or_rule_version text NOT NULL,
    elapsed_ms            integer NOT NULL CHECK (elapsed_ms >= 0),
    created_at            timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT trace_step_sequence UNIQUE (trace_id, sequence)
);

CREATE INDEX IF NOT EXISTS trace_step_run_idx ON trace_step (run_id, sequence);

-- ---------------------------------------------------------------------------
-- 触发器
-- ---------------------------------------------------------------------------

-- updated_at 自动刷新
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

CREATE OR REPLACE TRIGGER policy_version_set_updated_at
    BEFORE UPDATE ON policy_version
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE OR REPLACE TRIGGER enterprise_set_updated_at
    BEFORE UPDATE ON enterprise
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE OR REPLACE TRIGGER enterprise_fact_set_updated_at
    BEFORE UPDATE ON enterprise_fact
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- 已发布版本及其子表不可改删（published 是终态；closed 只在读取时派生）
CREATE OR REPLACE FUNCTION guard_published_version() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.review_status = 'published' THEN
        RAISE EXCEPTION '已发布的政策版本不可修改或删除（policy_version %）', OLD.id
            USING ERRCODE = 'check_violation';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE TRIGGER policy_version_guard_published
    BEFORE UPDATE OR DELETE ON policy_version
    FOR EACH ROW EXECUTE FUNCTION guard_published_version();

CREATE OR REPLACE FUNCTION guard_published_children() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    parent_status text;
    target        uuid := COALESCE(NEW.policy_version_id, OLD.policy_version_id);
BEGIN
    SELECT review_status INTO parent_status FROM policy_version WHERE id = target;
    IF parent_status = 'published' THEN
        RAISE EXCEPTION '已发布版本的子表不可修改（表 %，版本 %）', TG_TABLE_NAME, target
            USING ERRCODE = 'check_violation';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE TRIGGER policy_source_guard_published
    BEFORE INSERT OR UPDATE OR DELETE ON policy_source
    FOR EACH ROW EXECUTE FUNCTION guard_published_children();

CREATE OR REPLACE TRIGGER policy_criterion_guard_published
    BEFORE INSERT OR UPDATE OR DELETE ON policy_criterion
    FOR EACH ROW EXECUTE FUNCTION guard_published_children();

CREATE OR REPLACE TRIGGER policy_validation_finding_guard_published
    BEFORE INSERT OR UPDATE OR DELETE ON policy_validation_finding
    FOR EACH ROW EXECUTE FUNCTION guard_published_children();

-- 只追加表：评估结果一旦写入即为历史，只能通过新运行覆盖
CREATE OR REPLACE FUNCTION reject_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '表 % 只允许插入，禁止 % 操作', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'check_violation';
END;
$$;

CREATE OR REPLACE TRIGGER assessment_no_update_delete
    BEFORE UPDATE OR DELETE ON assessment
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();

CREATE OR REPLACE TRIGGER assessment_dependency_no_update_delete
    BEFORE UPDATE OR DELETE ON assessment_dependency
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();

CREATE OR REPLACE TRIGGER invalidation_event_no_update_delete
    BEFORE UPDATE OR DELETE ON invalidation_event
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();

CREATE OR REPLACE TRIGGER trace_step_no_update_delete
    BEFORE UPDATE OR DELETE ON trace_step
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();

-- 运行记录允许推进 status/finished_at，其余列冻结
CREATE OR REPLACE FUNCTION guard_assessment_run_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.id <> OLD.id
       OR NEW.enterprise_id <> OLD.enterprise_id
       OR NEW.as_of <> OLD.as_of
       OR NEW.fact_version <> OLD.fact_version
       OR NEW.model_or_rule_version <> OLD.model_or_rule_version
       OR NEW.created_at <> OLD.created_at THEN
        RAISE EXCEPTION '评估运行只允许更新 status 与 finished_at（run %）', OLD.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE TRIGGER assessment_run_guard_update
    BEFORE UPDATE ON assessment_run
    FOR EACH ROW EXECUTE FUNCTION guard_assessment_run_update();

CREATE OR REPLACE TRIGGER assessment_run_no_delete
    BEFORE DELETE ON assessment_run
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();

INSERT INTO schema_migration (id, description)
VALUES ('001_init', '政策/来源/条款、企业画像、评估运行、失效事件与追踪表')
ON CONFLICT (id) DO NOTHING;

COMMIT;
