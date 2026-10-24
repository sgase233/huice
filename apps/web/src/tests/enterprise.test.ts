import { describe, expect, it } from 'vitest';
import { validateEnterprise } from '../pages/EnterprisePage';
import { initialEnterprise } from '../demo';

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
});
