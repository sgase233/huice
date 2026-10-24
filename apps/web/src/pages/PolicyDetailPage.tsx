import type { DemoPolicy } from '../demo';
import EvidenceRow from '../components/EvidenceRow';
import StatusBadge from '../components/StatusBadge';
import { taxNotice } from '../demo';

export default function PolicyDetailPage({ policy }: { policy: DemoPolicy | null }) {
  if (!policy) return <div className="panel empty-state"><h1>尚未选择场景</h1><p>请在机会列表选择一项固定合成示例。当前企业画像尚未进行真实分析。</p><a className="button" href="#opportunities">前往机会列表</a></div>;
  return <section className="page-block" aria-labelledby="policy-title">
    <div className="page-heading"><div><span className="eyebrow">03 / 政策详情与证据</span><h1 id="policy-title">{policy.title}</h1><p>{policy.issuer} · {policy.version} · {policy.type === 'tax' ? '税收政策' : '资金政策'}</p></div><StatusBadge result={policy.result} validity={policy.validity} policyStatus={policy.status} /></div>
    <div className="content-columns"><div className="stack"><section className="panel"><span className="eyebrow">固定结果说明</span><h2>为什么显示这个状态</h2><p>{policy.reason}</p>{policy.validity === 'stale' && <p className="notice warning">历史依据已失效。此处仅展示合成历史情形，需重新核对。</p>}{policy.status === 'closed' && <p className="notice warning">申报窗口已关闭。请查看历史信息，不提供立即申报入口。</p>}</section>
      <section className="panel"><span className="eyebrow">证据与来源</span><h2>条款引用示例</h2><p className="muted">下列文本是合成演示内容，不代表官方原文或已验证的政策依据。</p><ol className="evidence-list">{policy.evidence.map((item, index) => <EvidenceRow key={item} evidence={item} index={index} />)}</ol><p>示例来源占位：<a href={policy.sourceUrl} target="_blank" rel="noreferrer">政府网站首页（非条款原文）</a></p></section></div>
      <aside className="panel side-panel"><span className="eyebrow">场景信息</span><h2>检查点</h2><dl><dt>结果版本</dt><dd>{policy.version}</dd><dt>政策状态</dt><dd>{policy.status === 'closed' ? '申报窗口已关闭' : '开放样例'}</dd><dt>判断依据</dt><dd>固定合成场景，未调用规则服务</dd><dt>事实期间</dt><dd>2026-01-01 至 2026-12-31（示例）</dd></dl>{policy.type === 'tax' && <p className="notice">{taxNotice}</p>}<a href="#report" className="button">查看清单与报告示例</a></aside></div>
  </section>;
}
