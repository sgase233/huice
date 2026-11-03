import { describe, expect, it } from 'vitest';
import { criteriaError, criteriaIssue } from '../components/criteriaValidation';
import type { CriterionDraft } from '../api/types';
const condition: CriterionDraft = { id: 'id', title: '条件', expression: { op: 'compare', factKey: 'taxableIncome', period: 'assessment', comparator: 'eq', unit: 'CNY', value: '0' }, sourceSpans: [{ sourceId: 's', sourceUrl: 'https://www.gov.cn/', sourceVersion: 'v1', page: 1, section: null, quote: '引用', textHash: '' }], reviewFinding: 'none' };
describe('审核规则输入', () => {
  it('缺少引用时定位添加引用按钮', () => {
    expect(criteriaIssue([{ ...condition, sourceSpans: [] }])).toMatchObject({ index: 0, field: 'add-span', message: '条件：至少添加一条原文引用。' });
  });
  it('保留零值并阻止非法十进制值', () => {
    expect(criteriaError([condition])).toBeNull();
    expect(criteriaError([{ ...condition, expression: { op: 'compare', factKey: 'taxableIncome', period: 'assessment', comparator: 'eq', unit: 'CNY', value: '1e3' } }])).toContain('十进制');
  });
  it('递归检查缺少人工核查原因的嵌套条件', () => {
    expect(criteriaError([{ ...condition, expression: { op: 'not', child: { op: 'manual_review', reason: '' } } }])).toContain('人工核查原因');
    expect(criteriaIssue([{ ...condition, expression: { op: 'and', children: [{ op: 'manual_review', reason: '已填写' }, { op: 'not', child: { op: 'manual_review', reason: '' } }] } }])).toMatchObject({ index: 0, field: 'expression-root.1.0-reason' });
  });
  it('拒绝不存在的日期和无效页码，同时允许闰日', () => {
    const date = { op: 'date' as const, factKey: 'establishedDate' as const, period: 'assessment' as const, comparator: 'eq' as const, value: '2023-02-29' };
    expect(criteriaError([{ ...condition, expression: date }])).toContain('日期');
    expect(criteriaError([{ ...condition, expression: { ...date, value: '2024-02-29' } }])).toBeNull();
    expect(criteriaError([{ ...condition, sourceSpans: [{ sourceId: 's', sourceUrl: 'https://www.gov.cn/', sourceVersion: 'v1', page: -1, section: '第一条', quote: '引用', textHash: '' }] }])).toContain('引用');
  });
  it('限制整棵规则树的节点总数', () => {
    const leaf = { op: 'manual_review' as const, reason: '人工核查' };
    expect(criteriaError([{ ...condition, expression: { op: 'and', children: Array.from({ length: 200 }, () => leaf) } }])).toContain('200 个节点');
    expect(criteriaError([{ ...condition, expression: { op: 'and', children: Array.from({ length: 199 }, () => leaf) } }])).toBeNull();
  });
  it('覆盖八类规则及嵌套组合的有效输入', () => {
    const manual = { op: 'manual_review' as const, reason: '需要人工复核' };
    const expressions: CriterionDraft['expression'][] = [
      { op: 'and', children: [manual, manual] },
      { op: 'or', children: [manual, { op: 'not', child: manual }] },
      { op: 'not', child: manual },
      { op: 'compare', factKey: 'taxableIncome', period: 'assessment', comparator: 'gte', unit: 'CNY', value: '-0.25' },
      { op: 'date', factKey: 'establishedDate', period: 'assessment', comparator: 'lte', value: '2024-02-29' },
      { op: 'industry_exclusion', factKey: 'industry', period: 'assessment', excluded: ['F51', 'F52'] },
      { op: 'quarterly_average', metric: 'employeeCount', period: { startDate: '2025-01-01', endDate: '2025-12-31' }, comparator: 'lte', value: '0', unit: 'person' },
      manual,
    ];
    for (const expression of expressions) expect(criteriaError([{ ...condition, expression }])).toBeNull();
  });
  it('限制布尔、行业清单和季度期间的非法输入', () => {
    const boolean = { op: 'compare' as const, factKey: 'rdActivity' as const, period: 'assessment' as const, comparator: 'eq' as const, unit: 'boolean' as const, value: false };
    expect(criteriaError([{ ...condition, expression: boolean }])).toBeNull();
    expect(criteriaError([{ ...condition, expression: { ...boolean, comparator: 'gt' } }])).toContain('布尔值');
    expect(criteriaError([{ ...condition, expression: { op: 'industry_exclusion', factKey: 'industry', period: 'assessment', excluded: ['F51', 'F51'] } }])).toContain('不重复');
    expect(criteriaIssue([{ ...condition, expression: { op: 'quarterly_average', metric: 'assets', period: { startDate: '2025-02-01', endDate: '2025-12-31' }, comparator: 'gte', value: '1.25', unit: 'CNY' } }])).toMatchObject({ field: 'expression-root-period-start' });
  });

});
