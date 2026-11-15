import { useEffect, useRef, useState } from 'react';
import { api, ApiRequestError } from '../api/client';
import type { AssessmentRun, Enterprise, EnterpriseSummary, RunSummary } from '../api/types';
import { factLabels } from '../demo';
import StatusBadge from '../components/StatusBadge';
import MetricsPanel from './MetricsPanel';
import { formatTime } from '../formatTime';

export default function ReviewerReadPage() {
  const [enterprises, setEnterprises] = useState<EnterpriseSummary[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [selected, setSelected] = useState<Enterprise | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [runCursor, setRunCursor] = useState<string | null>(null);
  const [run, setRun] = useState<AssessmentRun | null>(null);
  const [policyTitles, setPolicyTitles] = useState<Record<string, string>>({});
  const resultRef = useRef<HTMLElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const sequence = useRef(0);
  useEffect(() => () => { sequence.current++; }, []);
  useEffect(() => { if (run) resultRef.current?.focus(); }, [run]);
  const fail = (e: unknown) => e instanceof ApiRequestError && (e.status === 401 || e.status === 403) ? '无法访问这些信息，请检查登录状态或联系管理员。' : e instanceof ApiRequestError && e.status === 404 ? '所选信息不存在或已不可查看，请刷新后重试。' : '信息暂时无法读取，请重试。';
  async function list(next?: string) {
    const request = ++sequence.current; setBusy(true); setError('');
    try { const result = await api.listEnterprises(next); if (request === sequence.current) { setEnterprises(old => next ? [...old, ...result.data.items] : result.data.items); setCursor(result.data.nextCursor); setLoaded(true); } }
    catch (e) { if (request === sequence.current) setError(fail(e)); } finally { if (request === sequence.current) setBusy(false); }
  }
  async function open(item: EnterpriseSummary) {
    const request = ++sequence.current; setBusy(true); setError(''); setSelected(null); setRuns([]); setRun(null); setRunCursor(null);
    try {
      const [profile, history] = await Promise.all([api.getEnterprise(item.id), api.listRuns(item.id)]);
      if (request !== sequence.current) return;
      if (history.data.items.some(entry => entry.enterpriseId.toLowerCase() !== item.id.toLowerCase())) throw new Error('接口契约错误：运行记录不属于所选企业。');
      setSelected(profile.data); setRuns(history.data.items); setRunCursor(history.data.nextCursor);
    } catch (e) { if (request === sequence.current) setError(fail(e)); } finally { if (request === sequence.current) setBusy(false); }
  }
  async function moreRuns() {
    if (!selected || !runCursor) return;
    const request = ++sequence.current; setBusy(true); setError('');
    try { const result = await api.listRuns(selected.id, runCursor); if (request !== sequence.current) return; if (result.data.items.some(entry => entry.enterpriseId.toLowerCase() !== selected.id.toLowerCase())) throw new Error('接口契约错误：运行记录不属于所选企业。'); setRuns(old => [...old, ...result.data.items]); setRunCursor(result.data.nextCursor); }
    catch (e) { if (request === sequence.current) setError(fail(e)); } finally { if (request === sequence.current) setBusy(false); }
  }
  async function openRun(item: RunSummary) {
    if (item.status !== 'succeeded') return;
    const request = ++sequence.current; setBusy(true); setError(''); setRun(null);
    try { const result = await api.getRun(item.id); if (request !== sequence.current) return; if (!selected || result.data.enterpriseId.toLowerCase() !== selected.id.toLowerCase()) throw new Error('接口契约错误：运行记录与所选企业不一致。'); const titles = await Promise.allSettled(result.data.assessments.map(assessment => api.getPolicy(assessment.policyId, result.data.policyVersionMap[assessment.policyId]))); if (request !== sequence.current) return; setPolicyTitles(Object.fromEntries(titles.flatMap((title, index) => title.status === 'fulfilled' && title.value.data.id === result.data.assessments[index].policyId && title.value.data.version === result.data.policyVersionMap[result.data.assessments[index].policyId] ? [[result.data.assessments[index].policyId, title.value.data.title]] : []))); setRun(result.data); }
    catch (e) { if (request === sequence.current) setError(fail(e)); } finally { if (request === sequence.current) setBusy(false); }
  }
  async function download(item: RunSummary) {
    if (item.status !== 'succeeded' || !item.reportAvailable) return;
    const request = ++sequence.current; setBusy(true); setError('');
    try {
      const result = await api.downloadReport(item.id);
      if (request !== sequence.current) return;
      const url = URL.createObjectURL(new Blob([result.data], { type: 'text/markdown;charset=utf-8' }));
      const link = document.createElement('a'); link.href = url; link.download = `assessment-${item.id}.md`; link.click(); URL.revokeObjectURL(url);
    } catch (e) { if (request === sequence.current) setError(fail(e)); } finally { if (request === sequence.current) setBusy(false); }
  }
  return <section className="page-block reviewer-read"><h1>企业与运行记录</h1>
    {error && <p role="alert" className="notice warning">{error}</p>}
    {run && <section className="panel reviewer-result" ref={resultRef} tabIndex={-1}><h2>运行结果</h2><p>分析时间：{formatTime(run.createdAt)}（北京时间）</p>{run.assessments.map((item, index) => <article key={item.policyId}><h3>{policyTitles[item.policyId] ?? `政策 ${index + 1}`}</h3><StatusBadge result={item.result} validity={item.validity} /><p>{item.explanation}</p></article>)}</section>}
    <button disabled={busy} onClick={() => void list()}>读取企业列表</button>
    {loaded && enterprises.length === 0 && <p>当前没有可见企业。</p>}
    <ul>{enterprises.map(item => <li key={item.id}><button disabled={busy} onClick={() => void open(item)}>{item.nameSynthetic}</button> · {item.ownership === 'self' ? '本人企业' : item.ownership === 'other' ? '其他企业' : '无主样本'} · {item.period.startDate} 至 {item.period.endDate}</li>)}</ul>
    {cursor && <button disabled={busy} onClick={() => void list(cursor)}>加载更多企业</button>}
    {selected && <section className="panel"><h2>{selected.nameSynthetic}</h2><ul>{selected.facts.map(item => <li key={`${item.key}/${item.period.startDate}/${item.period.endDate}`}>{factLabels[item.key]}：{item.value === null ? '未知' : typeof item.value === 'boolean' ? (item.value ? '是' : '否') : item.value}（{item.period.startDate} 至 {item.period.endDate}）</li>)}</ul>
      <h3>运行记录</h3>{runs.length === 0 && <p>当前没有可见运行记录。</p>}<ul className="reviewer-runs">{runs.map(item => <li key={item.id}><span>{formatTime(item.createdAt)}（北京时间） · {item.status === 'succeeded' ? '已完成' : item.status === 'running' ? '运行中' : item.status === 'queued' ? '排队中' : item.status === 'failed' ? '失败' : '未知状态'}</span><div className="reviewer-run-actions">{item.status === 'succeeded' && <button disabled={busy} onClick={() => void openRun(item)}>查看结果</button>}{item.status === 'succeeded' && item.reportAvailable && <button disabled={busy} onClick={() => void download(item)}>下载报告</button>}</div></li>)}</ul>{runCursor && <button disabled={busy} onClick={() => void moreRuns()}>加载更多运行记录</button>}
    </section>}
    <MetricsPanel enterpriseId={selected?.id ?? null} />
  </section>;
}
