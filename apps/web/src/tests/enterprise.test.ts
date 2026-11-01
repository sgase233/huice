import { describe, expect, it } from 'vitest';
import { validateEnterprise } from '../pages/EnterprisePage';
import { initialEnterprise } from '../demo';
import type { FactInput } from '../api/types';

describe('synthetic enterprise input', () => {
  it('distinguishes unknown, zero, and false without treating them as missing', () => {
    const input = structuredClone(initialEnterprise);
    input.facts[2].value = '0';
    input.facts.push({ key: 'rdActivity', value: false, unit: 'boolean', period: input.period, source: 'user_input' });
    expect(validateEnterprise(input)).toEqual([]);
    input.facts[2].value = null;
    expect(validateEnterprise(input)).toEqual([]);
  });

  it('rejects wrong money precision, missing core facts, and duplicate key and period', () => {
    const input = structuredClone(initialEnterprise);
    input.facts[2].value = '1.234';
    input.facts.push({ ...input.facts[0] });
    input.facts[1].value = null;
    const result = validateEnterprise(input).join(' ');
    expect(result).toContain('最多两位小数');
    expect(result).toContain('期间重复');
    expect(result).toContain('行业代码');
  });

  it('validates rc.3 boolean, signed money, ratio, and separate periods', () => {
    const input = structuredClone(initialEnterprise);
    input.facts.push(
      { key: 'creditRecord', value: false, unit: 'boolean', period: input.period, source: 'user_input' },
      { key: 'rdAddDeductionClaimed', value: true, unit: 'boolean', period: input.period, source: 'user_input' },
      { key: 'rdExpenseIncrease', value: '-125000.50', unit: 'CNY', period: input.period, source: 'user_input' },
      { key: 'rdExpenseRevenueRatio', value: '0.03', unit: 'ratio', period: input.period, source: 'user_input' },
      { key: 'rdExpenseIncrease', value: '0', unit: 'CNY', period: { startDate: '2025-01-01', endDate: '2025-12-31' }, source: 'user_input' },
    );
    expect(validateEnterprise(input)).toEqual([]);
    input.facts.at(-5)!.value = 'true';
    input.facts.at(-2)!.value = '3%';
    expect(validateEnterprise(input).join(' ')).toContain('诚信记录');
    expect(validateEnterprise(input).join(' ')).toContain('研发费用占营业收入比重');
  });

  it.each([
    ['taxableIncome', 'CNY', '-10.00', true],
    ['rdExpenseIncrease', 'CNY', '-125.50', true],
    ['revenue', 'CNY', '-1', false],
    ['equityFinancing', 'CNY', '1.234', false],
    ['rdExpenseRevenueRatio', 'ratio', '0', true],
    ['rdExpenseRevenueRatio', 'ratio', '0.000001', true],
    ['rdExpenseRevenueRatio', 'ratio', '-0.01', false],
    ['rdExpenseRevenueRatio', 'ratio', '3%', false],
    ['rdExpenseRevenueRatio', 'ratio', '1e-2', false],
    ['rdExpenseRevenueRatio', 'ratio', '0.0000001', false],
  ] as const)('validates %s %s value %s', (key, unit, value, valid) => {
    const input = structuredClone(initialEnterprise);
    input.facts = input.facts.filter(fact => fact.key !== key);
    input.facts.push({ key, unit, value, period: input.period, source: 'user_input' });
    expect(validateEnterprise(input).length === 0).toBe(valid);
  });

  it.each(['rdActivity', 'rdAddDeductionClaimed', 'creditRecord', 'independentLegalEntity'] as const)('accepts boolean and null only for %s', key => {
    const input = structuredClone(initialEnterprise);
    const fact: FactInput = { key, unit: 'boolean', value: null, period: input.period, source: 'user_input' };
    input.facts.push(fact);
    for (const value of [true, false, null]) { fact.value = value; expect(validateEnterprise(input)).toEqual([]); }
    fact.value = 'true'; expect(validateEnterprise(input)).not.toEqual([]);
  });

  it('reports an invalid date against the correct fact label', () => {
    const input = structuredClone(initialEnterprise);
    input.facts.push({ key: 'establishedDate', unit: 'date', value: '2026-02-30', period: input.period, source: 'user_input' });
    expect(validateEnterprise(input)).toContain('成立日期应为有效的 YYYY-MM-DD 日期。');
  });

  it('validates the specific market start date and its profile period', () => {
    const input = structuredClone(initialEnterprise);
    const fact: FactInput = { key: 'specificMarketSince', unit: 'date', value: '2020-02-29', period: input.period, source: 'user_input' };
    input.facts.push(fact);
    expect(validateEnterprise(input)).toEqual([]);
    fact.value = '2021-02-29';
    expect(validateEnterprise(input)).toContain('细分市场起始日期应为有效的 YYYY-MM-DD 日期。');
    fact.value = '2020-02-29'; fact.period = { startDate: '2025-01-01', endDate: '2025-12-31' };
    expect(validateEnterprise(input).join(' ')).toContain('细分市场起始日期的期间必须与画像期间一致');
  });
});
