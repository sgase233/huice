import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import { formatTime } from '../formatTime';
import type { AssessmentRunsResponse, CompletenessResponse, MetricAvailability, ReviewWorkloadResponse } from '../api/types';
import { factLabels } from '../demo';
import type { FactKey } from '../api/types';

const status: Record<string, string> = { filled: '已填写', filled_unverified: '已填写', answered_unknown: '已回答未知', not_answered: '待填写', not_applicable: '不适用', applicability_unknown: '适用性未知', not_due: '未到期' };
const today = () => new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
function metric(value: number | null | undefined, availability: MetricAvailability | undefined, open = false) {
  if (open && value == null) return '未结算';
  if (!availability) return '覆盖状态未知';
  if (availability.state === 'not_collected') return '未采集';
  if (value == null) return '未提供';
  return `${value}${availability.state === 'partial' ? `（部分覆盖${availability.coverageFrom ? `，自 ${formatTime(availability.coverageFrom)}` : ''}）` : ''}`;
}
function TrendChart({ title, points }: { title: string; points: { date: string; value: number | null; availability: MetricAvailability | undefined }[] }) {
  const max = Math.max(1, ...points.map(p => p.availability && p.availability.state !== 'not_collected' && p.value !== null ? p.value : 0));
  return <figure className="metrics-chart"><figcaption>{title}（具体数值及覆盖状态见下表）</figcaption>{points.map(p => <div className="metrics-bar-row" key={p.date}><span>{p.date}</span>{p.availability && p.availability.state !== 'not_collected' && p.value !== null ? <span className="metrics-bar-track" aria-hidden="true"><span className="metrics-bar" style={{ width: `${p.value / max * 100}%` }} /></span> : <span>未采集或未知</span>}<span>{metric(p.value, p.availability)}</span></div>)}</figure>;
}
function DraftColumns({ days }: { days: ReviewWorkloadResponse['days'] }) {
  const max = Math.max(1, ...days.map(day => day.availability.newDrafts?.state !== 'not_collected' && day.availability.newDrafts ? day.newDrafts : 0));
  return <figure className="metrics-columns"><figcaption>每日新增草稿</figcaption><div className="metrics-columns-scroll" tabIndex={0} role="region" aria-label="每日新增草稿柱状图，可横向滚动"><div className="metrics-columns-plot">{days.map(day => {
    const available = day.availability.newDrafts && day.availability.newDrafts.state !== 'not_collected';
    return <div className="metrics-column" key={day.date}><span>{metric(day.newDrafts, day.availability.newDrafts)}</span><div className="metrics-column-track" aria-hidden="true">{available && <div className="metrics-column-bar" style={{ height: `${day.newDrafts / max * 100}%` }} />}</div><time>{day.date}</time></div>;
  })}</div></div></figure>;
}

function RunDonut({ days }: { days: AssessmentRunsResponse['days'] }) {
  if (!days.length) return null;
  const complete = days.every(day => day.succeeded !== null && day.failed !== null && day.availability.succeeded?.state === 'precise' && day.availability.failed?.state === 'precise');
  if (!complete) return <section className="metrics-donut-section" aria-label="已结束运行分布"><h5>已结束运行分布</h5><p>所选期间的成功或失败数据未完整覆盖，暂不计算比例。请查看下方逐日数据。</p></section>;
  const success = days.reduce((sum, day) => sum + day.succeeded!, 0);
  const failure = days.reduce((sum, day) => sum + day.failed!, 0);
  const total = success + failure;
  return <section className="metrics-donut-section" aria-label="已结束运行分布"><h5>已结束运行分布</h5>{total === 0 ? <p>所选期间没有已结束的运行，暂无比例。</p> : <div className="metrics-donut-layout"><div className="metrics-donut" role="img" aria-label={`已结束运行 ${total} 次，成功 ${success} 次，失败 ${failure} 次`} style={{ background: `conic-gradient(var(--metrics-success) 0 ${success / total * 100}%, var(--metrics-failure) ${success / total * 100}% 100%)` }}><div className="metrics-donut-center"><strong>{total}</strong><span>已结束运行</span></div></div><div className="metrics-donut-legend"><p><span className="metrics-dot metrics-dot-success" aria-hidden="true" />成功：{success} 次（{(success / total * 100).toFixed(1)}%）</p><p><span className="metrics-dot metrics-dot-failure" aria-hidden="true" />失败：{failure} 次（{(failure / total * 100).toFixed(1)}%）</p></div></div>}<p>按运行开始日归属；未完成运行不计入此图。</p></section>;
}

export default function MetricsPanel({ enterpriseId }: { enterpriseId: string | null }) {
  const [from, setFrom] = useState(today); const [to, setTo] = useState(today);
  const [review, setReview] = useState<ReviewWorkloadResponse | null>(null); const [runs, setRuns] = useState<AssessmentRunsResponse | null>(null);
  const [complete, setComplete] = useState<CompletenessResponse | null>(null); const [error, setError] = useState(''); const [busy, setBusy] = useState(false);
  const sequence = useRef(0);
  useEffect(() => { setComplete(null); setBusy(false); return () => { sequence.current++; }; }, [enterpriseId]);
  async function trends() {
    if (!from || !to || from > to || to > today() || (Date.parse(to) - Date.parse(from)) / 86400000 > 30) { setError('请选择不晚于今天、最多 31 天的有效日期范围。'); return; }
    const id = ++sequence.current; setBusy(true); setError(''); setReview(null); setRuns(null);
    try { const [a,b] = await Promise.all([api.reviewWorkload(from,to), api.assessmentTrends(from,to)]); if (id === sequence.current) { setReview(a.data); setRuns(b.data); } }
    catch (e) { if (id === sequence.current) setError('统计暂时无法读取，请重试。'); } finally { if (id === sequence.current) setBusy(false); }
  }
  async function completeness() {
    if (!enterpriseId) return; const id = ++sequence.current; setBusy(true); setError(''); setComplete(null);
    try { const result = await api.completeness(enterpriseId); if (id === sequence.current) setComplete(result.data); }
    catch (e) { if (id === sequence.current) setError('完整度暂时无法读取，请重试。'); } finally { if (id === sequence.current) setBusy(false); }
  }
  return <section className="panel metrics-panel"><h2>资料完整度与运行统计</h2>
    {error && <p role="alert" className="notice warning">{error}</p>}
    <h3>企业资料完整度</h3><p>选择上方企业后读取。完整度按当前资料和已发布政策的静态引用统计，人工核查项目单列。</p>
    <button disabled={!enterpriseId || busy} onClick={() => void completeness()}>{busy ? '读取中…' : '读取所选企业完整度'}</button>
    {complete && <div><p>统计时间：{formatTime(complete.generatedAt)} · 依据时间：{complete.asOf.includes('T') ? formatTime(complete.asOf) : complete.asOf} · 口径：{complete.definitionVersion}</p>
      <p>已填写 {complete.totals.filledCount} / 适用 {complete.totals.applicableCount} 项{complete.totals.applicabilityUnknownCount > 0 ? '；存在适用性未知，未展示总体比例' : complete.totals.ratio === null ? '；比例不适用' : `（${(complete.totals.ratio * 100).toFixed(1)}%）`}</p>
      <ul>{complete.groups.map(g => <li key={g.key}>{g.label}：{g.filledCount} / {g.applicableCount}{g.ratio === null ? '（不适用）' : ''}</li>)}</ul>
      <h4>字段与期间</h4>{complete.units.length === 0 ? <p>当前范围没有适用字段。</p> : <ul>{complete.units.map((u,i) => <li key={`${u.factKey}-${u.periodStart}-${i}`}>{factLabels[u.factKey as FactKey] || u.factKey} · {u.periodStart} 至 {u.periodEnd} · {status[u.status] || '未知状态'}</li>)}</ul>}
      {complete.manualReview.length > 0 && <><h4>人工核查</h4><ul>{complete.manualReview.map((m,i) => <li key={i}>{m.title}：{m.reason}</li>)}</ul></>}
    </div>}
    <h3>审核工作量与分析运行趋势</h3><p>按北京时间自然日统计，最多 31 天。窄屏下可横向滚动表格查看全部指标。</p><div className="filter-panel"><label>起始日期<input type="date" value={from} max={today()} onChange={e => setFrom(e.target.value)} /></label><label>结束日期<input type="date" value={to} max={today()} onChange={e => setTo(e.target.value)} /></label><button disabled={busy} onClick={() => void trends()}>读取统计</button></div>
    {review && <><h4>审核工作量</h4><p>生成于 {formatTime(review.generatedAt)} · {review.timeZone}；当天日终待办在结算后提供。</p>{review.days.length === 0 && <p>所选期间暂无统计记录。</p>}<DraftColumns days={review.days} /><TrendChart title="每日完成审核" points={review.days.map(d => ({ date: d.date, value: d.reviewsCompleted, availability: d.availability.reviewsCompleted }))} /><div className="metrics-table" tabIndex={0} role="region" aria-label="逐日统计表，可横向滚动"><table><thead><tr><th>日期</th><th>新增草稿</th><th>完成审核</th><th>日终待办</th></tr></thead><tbody>{review.days.map(d => <tr key={d.date}><th>{d.date}</th><td>{metric(d.newDrafts,d.availability.newDrafts)}</td><td>{metric(d.reviewsCompleted,d.availability.reviewsCompleted)}</td><td>{metric(d.pendingAtEndOfDay,d.availability.pendingAtEndOfDay,d.dayOpen)}</td></tr>)}</tbody></table></div></>}
    {runs && <><h4>分析运行趋势</h4><p>生成于 {formatTime(runs.generatedAt)} · 运行按开始日归属；耗时仅统计可信样本。</p>{runs.days.length === 0 && <p>所选期间暂无统计记录。</p>}<RunDonut days={runs.days} /><TrendChart title="每日分析运行总量" points={runs.days.map(d => ({ date: d.date, value: d.total, availability: d.availability.total }))} /><div className="metrics-table" tabIndex={0} role="region" aria-label="逐日统计表，可横向滚动"><table><thead><tr><th>日期</th><th>总量</th><th>成功</th><th>失败</th><th>未完成</th><th>耗时样本数</th><th>平均耗时（毫秒）</th></tr></thead><tbody>{runs.days.map(d => <tr key={d.date}><th>{d.date}</th>{(['total','succeeded','failed','unfinished','durationSampleCount','avgDurationMs'] as const).map(k => <td key={k}>{metric(d[k],d.availability[k])}</td>)}</tr>)}</tbody></table></div></>}
  </section>;
}
