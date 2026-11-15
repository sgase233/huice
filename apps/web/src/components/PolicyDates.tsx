import type { DateProvenanceField, PolicyDetail } from '../api/types';
const labels: Record<string, string> = { verified: '已核实', derived: '推导日期', unverified: '待核实', not_applicable: '不适用', unknown: '未知' };
function provenance(value: DateProvenanceField | null | undefined) {
  if (!value) return '来源与可信度尚未提供';
  return [labels[value.status] ?? '未知状态', value.source === 'body' ? '正文' : value.source === 'attachment' ? '附件' : '', value.locator, value.basis, value.derivedFrom ? `推导依据：${value.derivedFrom}` : '', value.note].filter(Boolean).join(' · ');
}
export default function PolicyDates({ policy }: { policy: PolicyDetail }) {
  return <section className="panel"><h2>政策日期与申报窗口</h2><dl>
    <dt>生效日期</dt><dd>{policy.effectiveFrom ?? '未提供'} · {provenance(policy.dateProvenance?.effectiveFrom)}</dd>
    <dt>有效截止</dt><dd>{policy.effectiveTo ?? '未提供'} · {provenance(policy.dateProvenance?.effectiveTo)}</dd>
    <dt>申报窗口</dt><dd>{policy.applicationWindow ? `${policy.applicationWindow.opensAt} 至 ${policy.applicationWindow.closesAt}` : '未提供'} · {provenance(policy.dateProvenance?.applicationWindow)}</dd>
  </dl>{policy.dateProvenance?.note && <p>{policy.dateProvenance.note}</p>}{typeof policy.sourceUrl === 'string' && /^https:\/\//i.test(policy.sourceUrl) && <a href={policy.sourceUrl} target="_blank" rel="noreferrer">查看政策原文</a>}</section>;
}
