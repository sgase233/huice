import { validExpression } from '../api/client';
import type { Expression } from '../api/types';
import { factLabels, factUnitLabels } from '../demo';

const labels: Record<Expression['op'], string> = {
  and: '且（全部条件）', or: '或（任一条件）', not: '非', compare: '事实比较',
  date: '日期比较', industry_exclusion: '行业排除', quarterly_average: '季度平均', manual_review: '人工核查',
};
const comparisons = { lt: '小于', lte: '小于或等于', eq: '等于', gte: '大于或等于', gt: '大于', ne: '不等于' };

function leafText(expression: Exclude<Expression, { op: 'and' | 'or' | 'not' }>) {
  if (expression.op === 'manual_review') return expression.reason || '未提供核查原因';
  const period = expression.period === 'assessment' ? '评估期间' : `${expression.period.startDate} 至 ${expression.period.endDate}`;
  if (expression.op === 'industry_exclusion') return `${period}：排除行业代码 ${expression.excluded.join('、') || '未提供'}`;
  const field = expression.op === 'quarterly_average' ? (expression.metric === 'assets' ? '资产' : '从业人数') : factLabels[expression.factKey];
  const value = typeof expression.value === 'boolean' ? (expression.value ? '是' : '否') : expression.value;
  const unit = 'unit' in expression ? factUnitLabels[expression.unit] : '';
  return `${period}：${field} ${comparisons[expression.comparator]} ${value} ${unit}`;
}

function Node({ expression, path }: { expression: Expression; path: string }) {
  const children = expression.op === 'and' || expression.op === 'or' ? expression.children : expression.op === 'not' ? [expression.child] : [];
  return <li><span className="expression-path">{path} </span><strong>{labels[expression.op]}</strong>{expression.op === 'and' || expression.op === 'or' || expression.op === 'not' ? <ul>{children.map((child, index) => <Node key={index} expression={child} path={`${path}.${index + 1}`} />)}</ul> : <span>：{leafText(expression)}</span>}</li>;
}

export default function ExpressionTree({ expression }: { expression: unknown }) {
  return <details className="expression-tree"><summary>查看资格逻辑结构</summary>{validExpression(expression) ? <><p>以下为该条款的规则结构；服务未提供各节点的独立判断结果。</p><p>编号表示节点层级。</p><ul><Node expression={expression} path="1" /></ul></> : <p>该版本的规则结构未提供或无法识别，请核对政策来源。</p>}</details>;
}
