-- 005：事实字典扩 6 键，并给 unit 枚举补 ratio（契约 1.0.0-rc.3）
--
-- 只加不改：既有的键、约束语义与数据都不动，只把允许集合放宽。可重复执行。
-- 背景：地方通知（研发投入补助、专精特新）的条款用到营业收入、股权融资、研发费用增量与
-- 占比、加计扣除申报、诚信记录六类事实；增量与占比按「预计算事实」入库，不新增算子。

BEGIN;

-- 1) 键枚举：10 -> 16
ALTER TABLE enterprise_fact DROP CONSTRAINT IF EXISTS enterprise_fact_key_check;
ALTER TABLE enterprise_fact
    ADD CONSTRAINT enterprise_fact_key_check CHECK (key IN (
        'region', 'industry', 'establishedDate', 'taxableIncome',
        'rdExpense', 'assetsStart', 'assetsEnd',
        'employeeCountStart', 'employeeCountEnd', 'rdActivity',
        'revenue', 'equityFinancing', 'rdExpenseIncrease',
        'rdExpenseRevenueRatio', 'rdAddDeductionClaimed', 'creditRecord'
    ));

-- 2) 单位枚举：补 ratio（占比用十进制小数，例如 3% 存 0.03）
ALTER TABLE enterprise_fact DROP CONSTRAINT IF EXISTS enterprise_fact_unit_check;
ALTER TABLE enterprise_fact
    ADD CONSTRAINT enterprise_fact_unit_check CHECK (unit IN (
        'code', 'date', 'CNY', 'person', 'boolean', 'ratio'
    ));

-- 3) 键与单位的配对：与 rules.FACT_UNITS 保持一致
ALTER TABLE enterprise_fact DROP CONSTRAINT IF EXISTS enterprise_fact_key_unit;
ALTER TABLE enterprise_fact
    ADD CONSTRAINT enterprise_fact_key_unit CHECK (
        (key IN ('region', 'industry') AND unit = 'code')
        OR (key = 'establishedDate' AND unit = 'date')
        OR (key IN ('taxableIncome', 'rdExpense', 'assetsStart', 'assetsEnd',
                    'revenue', 'equityFinancing', 'rdExpenseIncrease') AND unit = 'CNY')
        OR (key IN ('employeeCountStart', 'employeeCountEnd') AND unit = 'person')
        OR (key = 'rdExpenseRevenueRatio' AND unit = 'ratio')
        OR (key IN ('rdActivity', 'rdAddDeductionClaimed', 'creditRecord') AND unit = 'boolean')
    );

INSERT INTO schema_migration (id, description) VALUES (
    '005_fact_dictionary_extension',
    '事实字典扩 6 键（revenue/equityFinancing/rdExpenseIncrease/rdExpenseRevenueRatio/rdAddDeductionClaimed/creditRecord），unit 增加 ratio'
) ON CONFLICT (id) DO NOTHING;

COMMIT;
