-- 008：材料要求入库（`preparation.items[]` 的数据来源，契约 1.0.0-rc.8）
--
-- 背景：在线链路的 `preparation.items[]` 一直恒为空，报告里只能写「具体材料要求待政策
-- 材料导入后补齐」。M3 的 15 行材料要求经 A/B 双人复核与仲裁后，13 行可导入——这份
-- 迁移给出存放它们的两张表，导入器是 `python -m app.materials import`。
--
-- 两条表分开的理由：
--
--   material_requirement        一行 = 一份材料（TAX-01、GRANT-03……），与政策无关；
--   material_requirement_policy 一行 = 「这份材料适用于哪个政策」。CSV 的
--                               `applies_to_policy_ids` 是业务代号（`CN-TAX-SME-2023-012`），
--                               库里政策只有 UUID，所以导入时按 `source_register.csv`
--                               的文号把它解析成 `policy.policy_version.document_number`，
--                               规则落在导入器里，不落在本表。
--
-- `applies_to_policy_codes` 仍原样保留业务代号：解析结果要能倒查回原始 CSV，重导入
-- 时也能看出「这份材料当初挂在哪几个代号上」。解析出的政策在 `material_requirement_policy`，
-- 为空 = 该 `policy_type` 下全部政策适用（CSV 的口径，见 MATERIAL_REQUIREMENTS §1）。
--
-- `proves_fact_keys` 不在库层再抄一份 18 键白名单：事实字典的真源是 `enterprise_fact`
-- 上的约束（005/006 维护）与 `rules.FACT_UNITS`，在这里再抄一次只会多一处会漂的副本。
-- 键的合法性由导入器校验（它有报错文案，能指出第几行第几个键），本表只保证是 JSON 数组。
--
-- 可重复执行：建表用 IF NOT EXISTS，导入器按 material_id upsert。

BEGIN;

CREATE TABLE IF NOT EXISTS material_requirement (
    material_id             text PRIMARY KEY,
    policy_type             text NOT NULL CHECK (policy_type IN ('tax', 'grant')),
    title                   text NOT NULL CHECK (length(btrim(title)) > 0),
    is_required             boolean NOT NULL,
    condition_desc          text,
    description             text NOT NULL CHECK (length(btrim(description)) > 0),
    proves_fact_keys        jsonb NOT NULL DEFAULT '[]'::jsonb
                            CHECK (jsonb_typeof(proves_fact_keys) = 'array'),
    applies_to_policy_codes jsonb NOT NULL DEFAULT '[]'::jsonb
                            CHECK (jsonb_typeof(applies_to_policy_codes) = 'array'),
    source_basis            text NOT NULL,
    review_status           text NOT NULL,
    imported_at             timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT material_requirement_id CHECK (material_id ~ '^(TAX|GRANT)-[0-9]{2}$'),
    -- 必交不留触发条件、条件交必须有触发条件：两者同时成立或同时不成立，
    -- 展示层就无法说清「什么时候才需要这份材料」。
    CONSTRAINT material_requirement_condition CHECK (
        (is_required AND condition_desc IS NULL)
        OR (NOT is_required AND nullif(btrim(condition_desc), '') IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS material_requirement_policy (
    material_id text NOT NULL
                REFERENCES material_requirement (material_id) ON DELETE CASCADE,
    policy_id   uuid NOT NULL REFERENCES policy (id) ON DELETE CASCADE,

    PRIMARY KEY (material_id, policy_id)
);

CREATE INDEX IF NOT EXISTS material_requirement_policy_policy_idx
    ON material_requirement_policy (policy_id);

CREATE INDEX IF NOT EXISTS material_requirement_type_idx
    ON material_requirement (policy_type, material_id);

INSERT INTO schema_migration (id, description) VALUES (
    '008_material_requirements',
    '材料要求与「材料→政策」映射入库；preparation.items 由此生成，无事实键材料由运行期标注为需人工核对'
) ON CONFLICT (id) DO NOTHING;

COMMIT;
