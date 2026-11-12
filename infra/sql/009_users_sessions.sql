-- 009：账号、可撤销会话与企业归属（E1，#46）
--
-- 背景：系统此前没有用户概念，企业画像与判断运行对任何持有演示会话的人一视同仁。
-- #46 要求「企业用户 / 审核员」两种角色：企业用户只看本人企业的数据，审核员可读全部。
-- 这份迁移给出承载它的三处结构，HTTP 层在 services/api/app/routes/auth.py。
--
-- 三处设计取舍：
--
--   1. 归属只有一个真源：`enterprise.owner_user_id`。
--      没有再加 `app_user.enterprise_id`——两列互为反向指针必然要有人维护同步，而同步
--      漏掉一次就变成「账号说自己是 A 企业、企业说自己是 B 账号」。唯一约束一条就够：
--      `owner_user_id` 上建唯一索引，同时保证「一个账号最多拥有一家企业」与
--      「一家企业最多一个账号」；审核员账号没有企业，该列为 NULL（NULL 不参与唯一比较）。
--      注册事务里先插 app_user 再插 enterprise，任何一步失败整体回滚，不留半个账号。
--
--   2. 历史数据保持无主：6 家合成企业与评分用 run 的 `owner_user_id` / `user_id` 留空，
--      不会被第一个注册的账号「认领」（EXTENSION_INTERFACES §3.3）。无主企业对普通账号
--      不可见，只有审核员可读；演示身份只作用于无主数据。
--
--   3. 会话可撤销且只存摘要：`user_session.token_hash` 是 Cookie 明文的 sha256，明文只
--      出现在 Set-Cookie 里；`csrf_hash` 同理。登出写 `revoked_at`，改密撤销该账号全部
--      会话，两者都不删行——删行会让「这个会话是被登出的还是从来没存在过」无法区分，
--      而客户端需要前者来清本地状态。
--      过期策略（绝对 12 小时、空闲 30 分钟滑动）在应用层判定，不改 `expires_at` 之外的
--      语义：`expires_at` 是绝对上限，`last_seen_at` 供空闲判定使用。
--
--   4. 演示配额是会话域的附属表：`demo_quota` 按 `sha256(sid)` 记该演示会话建了几家
--      无主合成企业，上限 20 条。它属于「凭据 ↔ 能力」这条线，与登录会话同一份迁移，
--      不另编一个号——010 已登记给政策发现（#54 D2.5）。
--
-- 可重复执行：建表用 IF NOT EXISTS；加列/加约束/建索引都先判存在，重复跑是空操作。

BEGIN;

-- ---------------------------------------------------------------------------
-- 账号
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_user (
    id            uuid PRIMARY KEY,
    -- 存规范化后的用户名（小写、去首尾空白），唯一比较才有意义
    username      text NOT NULL UNIQUE,
    password_hash text NOT NULL CHECK (length(password_hash) > 0),
    role          text NOT NULL CHECK (role IN ('enterprise', 'reviewer')),
    status        text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),

    -- 3—32 位小写字母/数字/下划线/点/连字符：注册时按同一规则规范化，
    -- 避免 'Alice' 与 'alice' 两个账号在大小写不敏感的库里互相踩
    CONSTRAINT app_user_username_shape CHECK (username ~ '^[a-z0-9_.-]{3,32}$')
);

-- ---------------------------------------------------------------------------
-- 会话：只存令牌与 CSRF 的摘要
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_session (
    id           uuid PRIMARY KEY,
    user_id      uuid NOT NULL REFERENCES app_user (id) ON DELETE CASCADE,
    token_hash   text NOT NULL UNIQUE CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    csrf_hash    text NOT NULL CHECK (csrf_hash ~ '^[0-9a-f]{64}$'),
    created_at   timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz NOT NULL,
    revoked_at   timestamptz,

    CONSTRAINT user_session_expiry CHECK (expires_at > created_at)
);

CREATE INDEX IF NOT EXISTS user_session_user_idx ON user_session (user_id);
CREATE INDEX IF NOT EXISTS user_session_live_idx
    ON user_session (expires_at) WHERE revoked_at IS NULL;

-- ---------------------------------------------------------------------------
-- 归属：企业 → 账号（唯一真源），运行 → 发起人
-- ---------------------------------------------------------------------------
ALTER TABLE enterprise ADD COLUMN IF NOT EXISTS owner_user_id uuid;

DO $$
BEGIN
    ALTER TABLE enterprise
        ADD CONSTRAINT enterprise_owner_user_fk
        FOREIGN KEY (owner_user_id) REFERENCES app_user (id) ON DELETE RESTRICT;
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

-- 唯一而不是普通索引：一个账号只能拥有一家企业；NULL（无主/历史合成企业）不参与比较
CREATE UNIQUE INDEX IF NOT EXISTS enterprise_owner_user_uidx
    ON enterprise (owner_user_id) WHERE owner_user_id IS NOT NULL;

ALTER TABLE assessment_run ADD COLUMN IF NOT EXISTS user_id uuid;

DO $$
BEGIN
    ALTER TABLE assessment_run
        ADD CONSTRAINT assessment_run_user_fk
        FOREIGN KEY (user_id) REFERENCES app_user (id) ON DELETE SET NULL;
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS assessment_run_user_idx
    ON assessment_run (user_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- 演示会话的建样配额（#54 D1.5）
-- ---------------------------------------------------------------------------
-- demo cookie 的载荷里带一个 `sid`；这里按 sha256(sid) 记「这个演示会话建了几家企业」。
-- 首建与计数必须是一条语句：两条（先查后插）在并发下会丢计数，20 条上限就形同虚设。
-- 行在 issue-session.py 签发时写入（created = 0），过期时间与 cookie 同寿命。
CREATE TABLE IF NOT EXISTS demo_quota (
    sid_hash   text PRIMARY KEY CHECK (sid_hash ~ '^[0-9a-f]{64}$'),
    created    int NOT NULL DEFAULT 0 CHECK (created >= 0),
    expires_at timestamptz NOT NULL
);

INSERT INTO schema_migration (id, description) VALUES (
    '009_users_sessions',
    '账号与可撤销会话；企业与判断运行的归属列（历史数据保持无主，不认领给首个注册账号）'
) ON CONFLICT (id) DO NOTHING;

COMMIT;
