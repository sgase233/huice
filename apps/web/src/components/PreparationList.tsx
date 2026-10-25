import type { PolicyType } from '../api/types';

export default function PreparationList({ type, items }: { type: PolicyType; items: string[] }) {
  return <section className="panel prep-panel"><div className="section-heading"><span className="eyebrow">{type === 'tax' ? '税收 · 自行判断' : '资金 · 项目申报'}</span><h2>准备材料示例</h2></div><ul className="checklist">{items.map(item => <li key={item}><span className="check-square" aria-hidden="true" />{item}</li>)}</ul><p className="muted">清单为合成界面样例，实际材料以已审核政策及官方入口为准。</p></section>;
}
