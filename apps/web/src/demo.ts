import type { AssessmentResult, EnterpriseInput, FactKey, FactUnit, PolicySummary, Validity } from './api/types';

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
};

export const factUnits: Record<FactKey, FactUnit> = {
  region: 'code', industry: 'code', establishedDate: 'date', taxableIncome: 'CNY', rdExpense: 'CNY',
  rdActivity: 'boolean', employeeCountStart: 'person', employeeCountEnd: 'person', assetsStart: 'CNY', assetsEnd: 'CNY',
};

export type DemoPolicy = PolicySummary & {
  result: AssessmentResult;
  validity: Validity;
  reason: string;
  evidence: string[];
  checklist: string[];
};

export const demoPolicies: DemoPolicy[] = [
  {
    id: '22222222-2222-4222-8222-222222222222', title: '合成演示：研发类税收政策', type: 'tax',
    issuer: '演示发布者', region: '440300', version: 'demo-v1', status: 'published',
    effectiveFrom: '2026-01-01', effectiveTo: null, applicationWindow: null,
    sourceUrl: 'https://www.gov.cn/', result: 'insufficient_evidence', validity: 'current',
    reason: '合成示例缺少研发费用事实，需要补充资料后由规则重新分析。',
    evidence: ['合成条款示例：研发活动与费用需有相应记录。'],
    checklist: ['研发活动记录', '费用明细与凭证', '财税人员复核记录'],
  },
  {
    id: '33333333-3333-4333-8333-333333333333', title: '合成演示：深圳资金扶持通知', type: 'grant',
    issuer: '演示发布者', region: '440300', version: 'demo-v1', status: 'published',
    effectiveFrom: '2026-01-01', effectiveTo: null, applicationWindow: null,
    sourceUrl: 'https://www.gov.cn/', result: 'satisfied', validity: 'current',
    reason: '固定样例场景展示“条件初步满足”；未读取当前画像或执行资格判断。',
    evidence: ['合成条款示例：地区条件。'],
    checklist: ['企业基本信息', '项目说明材料', '经办人复核记录'],
  },
  {
    id: '44444444-4444-4444-8444-444444444444', title: '合成演示：行业排除情形', type: 'tax',
    issuer: '演示发布者', region: '440300', version: 'demo-v1', status: 'published',
    effectiveFrom: '2026-01-01', effectiveTo: null, applicationWindow: null,
    sourceUrl: 'https://www.gov.cn/', result: 'not_satisfied', validity: 'current',
    reason: '固定样例场景展示行业排除；不代表对当前企业作出结论。',
    evidence: ['合成条款示例：部分行业不适用。'],
    checklist: ['行业分类证明', '经办人复核记录'],
  },
  {
    id: '55555555-5555-4555-8555-555555555555', title: '合成演示：历史通知冲突情形', type: 'grant',
    issuer: '演示发布者', region: '440300', version: 'demo-v0', status: 'closed',
    effectiveFrom: '2025-01-01', effectiveTo: '2025-12-31',
    applicationWindow: { opensAt: '2025-01-01T00:00:00+08:00', closesAt: '2025-12-31T23:59:59+08:00' },
    sourceUrl: 'https://www.gov.cn/', result: 'conflict', validity: 'stale',
    reason: '固定样例中两处合成条款表述冲突，需人工核对；该历史窗口已关闭。',
    evidence: ['合成正文示例：适用范围包含该行业。', '合成附件示例：适用范围排除该行业。'],
    checklist: ['正文与附件的版本记录', '人工核对意见'],
  },
];

export const demoNotice = '静态合成演示 · 未进行真实资格判断';
export const taxNotice = '政策信息辅助整理；非税务专业意见；申报前请企业财税人员核对。';
