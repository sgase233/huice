-- 011：政策版本的日期来源（VIS-06，#48 的 B1a）
--
-- 背景：`policy_version` 只有裸日期（`published_at` / `effective_from` / `effective_to`
-- / `application_window`），没有任何地方记「这个日期是原文写的、还是推算的、还是登记
-- 表里填的」。时间线（VIS-AT-04）因此无法区分「已核实」与「推测」，也就不能展示。
--
-- 四处取舍：
--
--   1. **另建一张表，不往 `policy_version` 上加列。** 001 的
--      `policy_version_guard_published` 规定已发布版本不可修改，而回填的目标恰恰全是
--      已发布版本：加列再回填会在触发器上直接报 `check_violation`，只能靠临时禁用触发器
--      绕过——那正是这条守卫要防的事。多一张旁表，已发布行一个字节都不动，代价只是一次
--      按主键的 join。
--
--   2. **一张版本一行，`provenance` 一个 jsonb 对象。** 日期有四个位置（生效起、生效止、
--      申报窗口、备注），拆成「值 + 状态 + 出处」三元列就是十几列，且以后加字段要再改表；
--      jsonb 与既有的 `application_window` 同构，读出来直接就是 §4.2 的五态结构。
--
--   3. **没有行 = 没有记录过来源**，不等于「来源未知」。缺省状态由读取层按「有值 →
--      `unverified`、无值 → `unknown`」派生，不落库——把派生值写进库会让「从未标注」和
--      「审核员明确标了未核实」变成一回事，审计时分不开。
--
--   4. **本迁移不对外暴露任何字段**：字段进 `PolicySummary`/`PolicyDetail`/`PolicyDraft`
--      与响应属于 B1b，随契约一次升版。数据层可以先合，不会产生半成品契约，也不会让
--      `generate_types --check` 多红一次。回填数据也不写在迁移里，由
--      `infra/deploy/backfill-date-provenance.py` 负责，便于逐条复核与幂等重跑。
--
-- 可重复执行、也能升级旧库：`CREATE TABLE IF NOT EXISTS` + `DROP TRIGGER IF EXISTS`。

BEGIN;

CREATE TABLE IF NOT EXISTS policy_version_date_provenance (
    policy_version_id uuid PRIMARY KEY
                      REFERENCES policy_version (id) ON DELETE CASCADE,
    -- 结构校验只保证「是一个对象」；五个状态、verified 必须有出处这些字段级规则在写入
    -- 路径与回填脚本里校验。jsonb 上逐字段 CHECK 会把规则复制到两处，改口径时必漏一处。
    provenance        jsonb NOT NULL CHECK (jsonb_typeof(provenance) = 'object'),
    updated_at        timestamptz NOT NULL DEFAULT now()
);

DROP TRIGGER IF EXISTS policy_version_date_provenance_set_updated_at
    ON policy_version_date_provenance;
CREATE TRIGGER policy_version_date_provenance_set_updated_at
    BEFORE UPDATE ON policy_version_date_provenance
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

INSERT INTO schema_migration (id, description) VALUES (
    '011_date_provenance',
    '政策版本的日期来源旁表（一条版本一行，jsonb）；已发布版本保持不可变，回填走 backfill-date-provenance.py'
) ON CONFLICT (id) DO NOTHING;

COMMIT;
