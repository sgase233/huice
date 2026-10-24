import { useState } from 'react';
import type { DemoPolicy } from '../demo';
import StatusBadge from '../components/StatusBadge';

type Props = { policies: DemoPolicy[]; selectedId: string | null; onSelect: (id: string) => void };

export default function OpportunitiesPage({ policies, selectedId, onSelect }: Props) {
  const [type, setType] = useState('all');
  const [status, setStatus] = useState('all');
  const visible = policies.filter(policy => (type === 'all' || policy.type === type) && (status === 'all' || policy.status === status));
  return <section className="page-block" aria-labelledby="opportunities-title">
    <div className="page-heading"><div><span className="eyebrow">02 / 机会列表</span><h1 id="opportunities-title">先看候选，再核对依据</h1><p>候选仅代表政策元数据可见。只有主动打开固定场景后才展示其示例状态。</p></div><span className="phase-tag">候选 · 静态演示</span></div>
    <div className="panel filter-panel"><label>政策类型<select value={type} onChange={event => setType(event.target.value)}><option value="all">全部类型</option><option value="tax">税收</option><option value="grant">资金</option></select></label><label>窗口状态<select value={status} onChange={event => setStatus(event.target.value)}><option value="all">全部状态</option><option value="published">开放样例</option><option value="closed">历史关闭</option></select></label><span className="result-count">{visible.length} 项合成候选</span></div>
    {visible.length === 0 ? <div className="panel empty-state">当前筛选没有合成候选。请调整类型或状态。</div> : <div className="policy-grid">{visible.map(policy => <article className="panel policy-card" key={policy.id}>
      <div className="card-top"><span className="eyebrow">{policy.type === 'tax' ? '税收优惠' : '资金扶持'}</span><span className="muted">{policy.version}</span></div>
      <h2>{policy.title}</h2><p>{policy.issuer} · {policy.region}</p>
      <StatusBadge result={selectedId === policy.id ? policy.result : undefined} validity={selectedId === policy.id ? policy.validity : undefined} policyStatus={policy.status} />
      <p className="card-meta">生效：{policy.effectiveFrom}{policy.effectiveTo ? ` 至 ${policy.effectiveTo}` : ' 起'}</p>
      <button type="button" className="button button-secondary" onClick={() => onSelect(policy.id)}>查看固定示例详情</button>
    </article>)}</div>}
  </section>;
}
