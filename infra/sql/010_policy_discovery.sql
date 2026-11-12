-- 010：政策发现、候选暂存区与运行记录（E2，#47）
--
-- 背景：#47 要求系统按审核员配置的范围和周期，主动去官方网站发现新政策，整理成
-- 「待审核候选」；审核员看过、改过之后才能提交为政策草稿。原来的手动导入（API-04）
-- 不依赖本迁移的任何一张表，继续可用。
--
-- 五处取舍：
--
--   1. 候选有独立暂存区，不进政策库：`policy_candidate` 与 `policy` / `policy_version`
--      / `criterion` 完全不共表。候选正文与抽取结果留在这里，提交时才经由现有导入
--      链路（同一套校验）写成草稿。这样「候选未审核不进政策库」是结构上成立的，
--      不是靠前端不显示。
--
--   2. 运行记录只增不改：`discovery_run` 的 started_at / trigger / 计划来源不可变，
--      只有 status、finished_at 与计数列允许更新，delete 直接拒绝（同 assessment_run
--      的 guard/reject 触发器）。「这次跑了什么、为什么失败」是审计证据，不能事后改写。
--
--   3. 判重分两层，跨批次也要挡住重复推荐：
--      · `policy_candidate.content_hash` 上建**部分唯一索引**——只约束仍待处理的候选
--        （pending_review / extraction_failed），让同一批里同内容只进一条；
--      · `policy_candidate_fingerprint` 长期保留「这份内容已经出现过、后来怎么处理了」，
--        候选正文被清理后仍然记得它提交过或丢弃过，下一轮不会再推荐一次。
--      只靠候选表判重的话，候选过期清理一结束，同样的政策就会被反复推荐。
--
--   4. `discovery_page` 记「每个网址上次看到的内容哈希」：同一网址内容变了（政策修订）
--      要能识别成新候选并提示版本变化，而不是当成重复静默丢掉。
--
--   5. 轮换是一个事务：把上一批未处理候选置 `expired`、插入新的运行行、推进
--      next_run_at 三件事一起提交或一起回滚（E0 D3.5）。中途失败时旧候选仍为 expired，
--      不自动恢复——页面显示本轮失败原因，而不是把过期候选变回可提交。
--
-- 可重复执行：建表用 IF NOT EXISTS；触发器用 CREATE OR REPLACE；重复跑是空操作。

BEGIN;

-- ---------------------------------------------------------------------------
-- 配置：单行表
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS discovery_settings (
    id               smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    enabled          boolean NOT NULL DEFAULT false,
    -- 网址列表（不是「站点 + 栏目」）：每个元素是一条具体的栏目/入口页 URL。
    -- 元素格式在应用层校验（HTTPS + gov.cn 白名单 + 非内网），库里只保证是数组。
    source_urls      jsonb NOT NULL DEFAULT '[]'::jsonb,
    -- 抓取周期：下限 6 小时（D3.1）。上限 30 天，避免「配置成一年一次」后被误当成
    -- 永不运行；两者都在应用层回显给前端（limits）。
    interval_minutes integer NOT NULL DEFAULT 1440,
    time_zone        text NOT NULL DEFAULT 'Asia/Shanghai',
    -- 下一次到点时间；为空表示「从未运行过，下一次 timer 唤醒即视为到点」。
    next_run_at      timestamptz,
    -- ETag 用的修订号：PUT 必须带 If-Match，避免两个浏览器互相覆盖配置。
    revision         uuid NOT NULL,
    updated_at       timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT discovery_settings_interval
        CHECK (interval_minutes BETWEEN 360 AND 43200),
    CONSTRAINT discovery_settings_sources
        CHECK (jsonb_typeof(source_urls) = 'array' AND jsonb_array_length(source_urls) <= 20),
    CONSTRAINT discovery_settings_time_zone CHECK (length(btrim(time_zone)) > 0)
);

INSERT INTO discovery_settings (id, enabled, source_urls, interval_minutes, revision)
VALUES (1, false, '[]'::jsonb, 1440, gen_random_uuid())
ON CONFLICT (id) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 运行记录
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS discovery_run (
    id              uuid PRIMARY KEY,
    -- 谁触发的：timer（systemd 定时）、cli（人工执行）、api（后台手动触发，首版不开放）。
    trigger         text NOT NULL DEFAULT 'timer'
                    CHECK (trigger IN ('timer', 'cli', 'api')),
    started_at      timestamptz NOT NULL,
    finished_at     timestamptz,
    -- 进程视角的状态：running 期间其余列都在变化，只有 finished_at 为空。
    status          text NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running', 'succeeded', 'partial_failed', 'failed')),
    -- 可区分的终态（D3.5）。与 status 分开：status 说「跑完没有、是否部分失败」，
    -- 这里说「为什么是这个结果」，前端用它区分「没有新政策」和「抓取失败」。
    terminal_reason text
                    CHECK (terminal_reason IN (
                        'completed', 'no_new_policy', 'fetch_failed', 'partial_failed',
                        'budget_exceeded', 'oversize', 'timeout', 'lock_lost'
                    )),
    sources_checked integer NOT NULL DEFAULT 0,
    links_seen      integer NOT NULL DEFAULT 0,
    duplicates      integer NOT NULL DEFAULT 0,
    new_candidates  integer NOT NULL DEFAULT 0,
    failed_sources  integer NOT NULL DEFAULT 0,
    tokens_used     integer NOT NULL DEFAULT 0,
    tokens_unknown  boolean NOT NULL DEFAULT false,
    -- 停机补跑时记「比计划晚了多少秒」；正常到点运行为空。
    late_by_seconds integer,
    -- 每个来源的结果：{url, status, links, error, errorCategory}，错误不放堆栈。
    source_status   jsonb NOT NULL DEFAULT '[]'::jsonb,
    -- 给人看的一句话结论（不含 URL 之外的用户数据）。
    detail          text,

    CONSTRAINT discovery_run_finish CHECK (
        (status = 'running' AND finished_at IS NULL AND terminal_reason IS NULL)
        OR (status <> 'running' AND finished_at IS NOT NULL AND terminal_reason IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS discovery_run_started_idx
    ON discovery_run (started_at DESC);

-- ---------------------------------------------------------------------------
-- 候选暂存区
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS policy_candidate (
    id                  uuid PRIMARY KEY,
    run_id              uuid NOT NULL REFERENCES discovery_run (id),
    -- 候选自己的修订号（ETag）：PATCH / submit / discard 必须带 If-Match。
    revision            uuid NOT NULL,
    status              text NOT NULL DEFAULT 'pending_review'
                        CHECK (status IN (
                            'pending_review', 'extraction_failed',
                            'submitted', 'discarded', 'expired'
                        )),

    -- ---- 取证字段（只读，PATCH 不接受）：审核员据此复核「这条推荐从哪来的」
    source_url          text NOT NULL,
    fetched_at          timestamptz NOT NULL,
    -- 规范化正文的 sha256：判重用。同一份政策换个链接（不同官方站点转载）也能识别。
    content_hash        text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    -- 原始字节的 sha256：留证用。与 content_hash 不同——页面框架不同会改变原始哈希，
    -- 但正文相同（见 data/policies/discovery_sources_phase1.csv 的 S01/S10 样本）。
    raw_hash            text CHECK (raw_hash IS NULL OR raw_hash ~ '^[0-9a-f]{64}$'),
    storage_key         text,
    body_text           text NOT NULL,

    -- ---- 待核元数据：候选阶段允许为空（原文没给的字段不编造），提交前由审核员补全
    -- 提交目标：policy_id 为空表示新建政策；非空表示给既有政策增加一个新版本
    -- （同 URL 内容变化时的「版本冲突」处理入口）。
    policy_id           uuid,
    version             text,
    title               text,
    policy_type         text CHECK (policy_type IS NULL OR policy_type IN ('tax', 'grant')),
    issuer              text,
    region              text,
    document_number     text,
    applicable_entities jsonb,
    published_at        date,
    effective_from      date,
    effective_to        date,
    application_window  jsonb,
    official_entry_url  text,
    -- 原始页面里找到、但首版未纳入抽取范围的附件链接（PDF 已抽取，其余记在这里）
    attachment_urls     jsonb NOT NULL DEFAULT '[]'::jsonb,
    -- 提交前仍缺哪些必填字段：前端据此高亮，服务端提交时再校验一次
    missing_fields      jsonb NOT NULL DEFAULT '[]'::jsonb,

    -- ---- 抽取结果
    criteria            jsonb NOT NULL DEFAULT '[]'::jsonb,
    validation_findings jsonb NOT NULL DEFAULT '[]'::jsonb,
    extraction          jsonb,

    -- ---- 重复与版本提示
    duplicate_of        uuid REFERENCES policy_candidate (id),
    existing_policy_id  uuid,
    existing_version    text,
    version_changed     boolean NOT NULL DEFAULT false,

    -- ---- 处理结果
    submitted_policy_id uuid,
    submitted_version   text,
    submitted_at        timestamptz,
    discarded_at        timestamptz,
    expired_at          timestamptz,
    discard_reason      text,

    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT policy_candidate_submitted CHECK (
        (status = 'submitted') = (submitted_policy_id IS NOT NULL)
        AND (status = 'submitted') = (submitted_version IS NOT NULL)
        AND (status = 'submitted') = (submitted_at IS NOT NULL)
    ),
    CONSTRAINT policy_candidate_discarded CHECK (
        (status = 'discarded') = (discarded_at IS NOT NULL)
    ),
    CONSTRAINT policy_candidate_expired CHECK (
        (status = 'expired') = (expired_at IS NOT NULL)
    )
);

-- 同一批里同内容只进一条；跨批次由 fingerprint 表拦（见文件头第 3 条取舍）。
CREATE UNIQUE INDEX IF NOT EXISTS policy_candidate_active_hash
    ON policy_candidate (content_hash)
    WHERE status IN ('pending_review', 'extraction_failed');

CREATE INDEX IF NOT EXISTS policy_candidate_status_idx
    ON policy_candidate (status, created_at DESC);

CREATE INDEX IF NOT EXISTS policy_candidate_run_idx
    ON policy_candidate (run_id);

CREATE INDEX IF NOT EXISTS policy_candidate_url_idx
    ON policy_candidate (source_url);

-- ---------------------------------------------------------------------------
-- 跨批次判重指纹：候选正文清理后仍然记得处理过什么
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS policy_candidate_fingerprint (
    content_hash        text PRIMARY KEY CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    canonical_url       text NOT NULL,
    first_seen_at       timestamptz NOT NULL,
    last_seen_at        timestamptz NOT NULL,
    -- 上一次处理结果：pending 表示仍挂在一个待处理候选上。
    outcome             text NOT NULL CHECK (outcome IN (
                            'pending', 'submitted', 'discarded', 'expired'
                        )),
    candidate_id        uuid,
    submitted_policy_id uuid
);

-- ---------------------------------------------------------------------------
-- 网址 → 上次内容哈希：识别「同一链接内容变了」
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS discovery_page (
    canonical_url     text PRIMARY KEY,
    last_content_hash text NOT NULL CHECK (last_content_hash ~ '^[0-9a-f]{64}$'),
    last_candidate_id uuid,
    last_seen_at      timestamptz NOT NULL,
    -- 上次这条链接出现在哪个来源页、第几次运行里
    last_run_id       uuid
);

-- ---------------------------------------------------------------------------
-- 运行记录只增不改：计数器与状态可以推进，身份与开始时间不行
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION guard_discovery_run_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.id <> OLD.id
       OR NEW.started_at <> OLD.started_at
       OR NEW.trigger <> OLD.trigger THEN
        RAISE EXCEPTION '发现运行只允许更新状态与计数（run %）', OLD.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS discovery_run_guard_update ON discovery_run;
CREATE TRIGGER discovery_run_guard_update
    BEFORE UPDATE ON discovery_run
    FOR EACH ROW EXECUTE FUNCTION guard_discovery_run_update();

DROP TRIGGER IF EXISTS discovery_run_no_delete ON discovery_run;
CREATE TRIGGER discovery_run_no_delete
    BEFORE DELETE ON discovery_run
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();

INSERT INTO schema_migration (id, description) VALUES (
    '010_policy_discovery',
    '发现配置、运行记录、候选暂存区与跨批次判重指纹；候选不进政策库，提交时复用现有导入链路'
) ON CONFLICT (id) DO NOTHING;

COMMIT;
