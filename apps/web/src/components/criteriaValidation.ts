import type { CriterionDraft, Expression } from '../api/types';
import { factUnits } from '../demo';
const decimal = /^-?(0|[1-9][0-9]*)(\.[0-9]{1,6})?$/;
function validDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || value.startsWith('0000')) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
}
type Issue = { message: string; field: string };
function expressionIssue(node: Expression, path = 'root', depth = 0): Issue | null {
  const issue = (message: string, field: string): Issue => ({ message, field: `expression-${path}-${field}` });
  if (depth > 8) return issue('规则嵌套不能超过 8 层。', 'type');
  if (node.op === 'and' || node.op === 'or') {
    if (node.children.length < 2) return issue('且／或至少需要两个子条件。', 'type');
    return node.children.map((child, index) => expressionIssue(child, `${path}.${index}`, depth + 1)).find(Boolean) ?? null;
  }
  if (node.op === 'not') return expressionIssue(node.child, `${path}.0`, depth + 1);
  if (node.op === 'manual_review') return node.reason.trim() && node.reason.length <= 200 ? null : issue('请填写不超过 200 字的人工核查原因。', 'reason');
  if (node.period !== 'assessment' && (!validDate(node.period.startDate) || !validDate(node.period.endDate) || node.period.startDate > node.period.endDate)) return issue('请填写有效的事实期间。', !validDate(node.period.startDate) ? 'period-start' : 'period-end');
  if (node.op === 'industry_exclusion') return node.excluded.length && node.excluded.every(x => x.trim()) && new Set(node.excluded).size === node.excluded.length ? null : issue('排除行业需要不重复的非空代码。', 'excluded');
  if (node.op === 'date') return validDate(node.value) ? null : issue('请填写日期。', 'value');
  if (node.op === 'quarterly_average') {
    if (node.period !== 'assessment' && (!node.period.startDate.endsWith('-01-01') || !node.period.endDate.endsWith('-12-31') || node.period.startDate.slice(0, 4) !== node.period.endDate.slice(0, 4))) return issue('季度平均的期间须为完整自然年度。', 'period-start');
    return decimal.test(node.value) ? null : issue('季度平均阈值须为十进制数。', 'value');
  }
  if (node.unit !== factUnits[node.factKey] || node.unit === 'date') return issue('事实与单位不匹配，日期事实请使用日期规则。', 'fact');
  if (node.unit === 'boolean') return typeof node.value === 'boolean' && ['eq', 'ne'].includes(node.comparator) ? null : issue('布尔值仅支持等于或不等于。', 'value');
  if (node.unit === 'code') return typeof node.value === 'string' && node.value.trim() && ['eq', 'ne'].includes(node.comparator) ? null : issue('代码不能为空且仅支持等于或不等于。', 'value');
  return typeof node.value === 'string' && decimal.test(node.value) && (!['person', 'ratio'].includes(node.unit) || !node.value.startsWith('-')) ? null : issue('请输入有效的十进制数。', 'value');
}
function tooManyNodes(root: Expression): boolean {
  const pending = [root]; let count = 0;
  while (pending.length) {
    const node = pending.pop()!;
    if (++count > 200) return true;
    if (node.op === 'and' || node.op === 'or') pending.push(...node.children);
    else if (node.op === 'not') pending.push(node.child);
  }
  return false;
}
export function criteriaIssue(criteria: CriterionDraft[]): (Issue & { index: number }) | null {
  for (const [index, criterion] of criteria.entries()) {
    if (!criterion.title.trim()) return { index, field: 'title', message: '请填写条件标题。' };
    if (tooManyNodes(criterion.expression)) return { index, field: 'expression-root-type', message: `${criterion.title}：整棵规则不能超过 200 个节点。` };
    const error = expressionIssue(criterion.expression); if (error) return { ...error, index, message: `${criterion.title}：${error.message}` };
    if (!criterion.sourceSpans.length) return { index, field: 'add-span', message: `${criterion.title}：至少添加一条原文引用。` };
    for (const [spanIndex, span] of criterion.sourceSpans.entries()) {
      const field = !span.quote.trim() ? 'quote' : span.page !== null && (!Number.isInteger(span.page) || span.page < 1) ? 'page' : span.page === null && !span.section?.trim() ? 'section' : null;
      if (field) return { index, field: `span-${spanIndex}-${field}`, message: `${criterion.title}：引用须填写原文及页码或章节。` };
    }
  }
  return null;
}
export function criteriaError(criteria: CriterionDraft[]): string | null { return criteriaIssue(criteria)?.message ?? null; }
