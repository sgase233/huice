import type { EnterpriseInput, FactKey, FactUnit } from './api/types';

export const period = { startDate: '2026-01-01', endDate: '2026-12-31' };

export const initialEnterprise: EnterpriseInput = {
  nameSynthetic: '合成企业 A',
  period,
  facts: [
    { key: 'region', value: '440300', unit: 'code', period, source: 'user_input' },
    { key: 'industry', value: 'C39', unit: 'code', period, source: 'user_input' },
    { key: 'taxableIncome', value: null, unit: 'CNY', period, source: 'user_input' },
  ],
};

export const factLabels: Record<FactKey, string> = {
  region: '地区代码', industry: '行业代码', establishedDate: '成立日期', taxableIncome: '应纳税所得额',
  rdExpense: '研发费用', rdActivity: '研发活动', employeeCountStart: '期初人数',
  employeeCountEnd: '期末人数', assetsStart: '期初资产', assetsEnd: '期末资产',
  // rc.3 新增事实键，布尔标签与 true 的含义保持同向。
  revenue: '营业收入', equityFinancing: '新增股权融资', rdExpenseIncrease: '研发费用增量',
  rdExpenseRevenueRatio: '研发费用占营业收入比重', rdAddDeductionClaimed: '是否已申报研发费用加计扣除',
  creditRecord: '诚信记录是否良好',
  specificMarketSince: '细分市场起始日期', independentLegalEntity: '是否具有独立法人资格',
};

export const factUnits: Record<FactKey, FactUnit> = {
  region: 'code', industry: 'code', establishedDate: 'date', taxableIncome: 'CNY', rdExpense: 'CNY',
  rdActivity: 'boolean', employeeCountStart: 'person', employeeCountEnd: 'person', assetsStart: 'CNY', assetsEnd: 'CNY',
  revenue: 'CNY', equityFinancing: 'CNY', rdExpenseIncrease: 'CNY', rdExpenseRevenueRatio: 'ratio',
  rdAddDeductionClaimed: 'boolean', creditRecord: 'boolean',
  specificMarketSince: 'date', independentLegalEntity: 'boolean',
};

export const factUnitLabels: Record<FactUnit, string> = {
  code: '代码', date: '日期', CNY: '元', boolean: '是／否', person: '人', ratio: '比例（小数）',
};

export const explanationLabels = { deepseek: 'AI 生成的说明', saved: '已保存的说明', fixture: '固定示例说明' } as const;

export const taxNotice = '政策信息用于辅助整理，不构成税务专业意见；申报前请财税人员核对。';
