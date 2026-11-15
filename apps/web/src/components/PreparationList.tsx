import type { PolicyType } from '../api/types';

export default function PreparationList({ type, items }: { type: PolicyType; items: string[] }) {
  return <section className="panel prep-panel"><div className="section-heading"><span className="eyebrow">{type === 'tax' ? '税收 · 自行判断' : '资金 · 项目申报'}</span><h2>准备材料示例</h2></div><ul className="checklist">{items.map(item => <li key={item}><span className="check-square" aria-hidden="true" />{item}</li>)}</ul><p className="muted">合成材料示例；实际要求请核对政策及官方入口。</p></section>;
}
