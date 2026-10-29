-- 006：事实字典再扩 2 键（契约 1.0.0-rc.4）
--
-- 只加不改：既有的 16 个键、约束语义与数据都不动。可重复执行。
-- 背景：专精特新一类条款要求「从事该细分市场满 N 年」「具有独立法人资格」。年限是相对量，
-- 同一份企业快照换个年份重算就得出另一结论、不可复现，因此记的是「开始从事之日」这个
-- 日期（specificMarketSince，unit=date），年限交给 date 算子按判断时点算；法人资格记布尔。

BEGIN;

-- 1) 键枚举：16 -> 18
ALTER TABLE enterprise_fact DROP CONSTRAINT IF EXISTS enterprise_fact_key_check;
ALTER TABLE enterprise_fact
    ADD CONSTRAINT enterprise_fact_key_check CHECK (key IN (
        'region', 'industry', 'establishedDate', 'taxableIncome',
        'rdExpense', 'assetsStart', 'assetsEnd',
        'employeeCountStart', 'employeeCountEnd', 'rdActivity',
        'revenue', 'equityFinancing', 'rdExpenseIncrease',
        'rdExpenseRevenueRatio', 'rdAddDeductionClaimed', 'creditRecord',
        'specificMarketSince', 'independentLegalEntity'
    ));

-- 2) 单位枚举不变（date / boolean 早已在列），这里不重建 enterprise_fact_unit_check。

-- 3) 键与单位的配对：与 rules.FACT_UNITS 保持一致
ALTER TABLE enterprise_fact DROP CONSTRAINT IF EXISTS enterprise_fact_key_unit;
ALTER TABLE enterprise_fact
    ADD CONSTRAINT enterprise_fact_key_unit CHECK (
        (key IN ('region', 'industry') AND unit = 'code')
        OR (key IN ('establishedDate', 'specificMarketSince') AND unit = 'date')
        OR (key IN ('taxableIncome', 'rdExpense', 'assetsStart', 'assetsEnd',
                    'revenue', 'equityFinancing', 'rdExpenseIncrease') AND unit = 'CNY')
        OR (key IN ('employeeCountStart', 'employeeCountEnd') AND unit = 'person')
        OR (key = 'rdExpenseRevenueRatio' AND unit = 'ratio')
        OR (key IN ('rdActivity', 'rdAddDeductionClaimed', 'creditRecord',
                    'independentLegalEntity') AND unit = 'boolean')
    );

INSERT INTO schema_migration (id, description) VALUES (
    '006_fact_dictionary_extension_rc4',
    '事实字典再扩 2 键（specificMarketSince/independentLegalEntity），日期类键增加 specificMarketSince'
) ON CONFLICT (id) DO NOTHING;

COMMIT;
