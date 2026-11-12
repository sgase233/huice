import { useEffect, useRef, useState } from 'react';
import { api, ApiRequestError } from '../api/client';
import type { AssessmentRun, Enterprise, EnterpriseSummary, RunSummary } from '../api/types';
import { factLabels } from '../demo';
import StatusBadge from '../components/StatusBadge';

export default function ReviewerReadPage() {
  const [enterprises, setEnterprises] = useState<EnterpriseSummary[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [selected, setSelected] = useState<Enterprise | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [runCursor, setRunCursor] = useState<string | null>(null);
  const [run, setRun] = useState<AssessmentRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const sequence = useRef(0);
  useEffect(() => () => { sequence.current++; }, []);
  const fail = (e: unknown) => e instanceof ApiRequestError ? `${e.message}${e.traceId ? `（追踪 ID：${e.traceId}）` : ''}` : e instanceof Error ? e.message : '请求失败。';
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
    try { const result = await api.getRun(item.id); if (request !== sequence.current) return; if (!selected || result.data.enterpriseId.toLowerCase() !== selected.id.toLowerCase()) throw new Error('接口契约错误：运行记录与所选企业不一致。'); setRun(result.data); }
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
  return <section className="page-block reviewer-read"><h1>企业与运行记录（只读）</h1><p>服务端决定可见范围；此处不提供修改企业事实的操作。</p>
    {error && <p role="alert" className="notice warning">{error}</p>}
    <button disabled={busy} onClick={() => void list()}>读取企业列表</button>
    {loaded && enterprises.length === 0 && <p>当前没有可见企业。</p>}
    <ul>{enterprises.map(item => <li key={item.id}><button disabled={busy} onClick={() => void open(item)}>{item.nameSynthetic}</button> · {item.ownership === 'self' ? '本人企业' : item.ownership === 'other' ? '其他企业' : '无主样本'} · {item.period.startDate} 至 {item.period.endDate}</li>)}</ul>
    {cursor && <button disabled={busy} onClick={() => void list(cursor)}>加载更多企业</button>}
    {selected && <section className="panel"><h2>{selected.nameSynthetic}</h2><p>企业 ID：{selected.id}</p><ul>{selected.facts.map(item => <li key={`${item.key}/${item.period.startDate}/${item.period.endDate}`}>{factLabels[item.key]}：{item.value === null ? '未知' : typeof item.value === 'boolean' ? (item.value ? '是' : '否') : item.value}（{item.period.startDate} 至 {item.period.endDate}，{item.verified ? '已核实' : '未核实'}）</li>)}</ul>
      <h3>运行记录</h3>{runs.length === 0 && <p>当前没有可见运行记录。</p>}<ul>{runs.map(item => <li key={item.id}>{item.createdAt} · {item.status === 'succeeded' ? '已完成' : item.status === 'running' ? '运行中' : item.status === 'queued' ? '排队中' : item.status === 'failed' ? '失败' : '未知状态'} · {item.id} {item.status === 'succeeded' && <button disabled={busy} onClick={() => void openRun(item)}>查看结果</button>} {item.status === 'succeeded' && item.reportAvailable && <button disabled={busy} onClick={() => void download(item)}>下载报告</button>}</li>)}</ul>{runCursor && <button disabled={busy} onClick={() => void moreRuns()}>加载更多运行记录</button>}
    </section>}
    {run && <section className="panel"><h2>运行结果</h2>{run.assessments.map(item => <article key={item.policyId}><h3>政策 {item.policyId}</h3><StatusBadge result={item.result} validity={item.validity} /><p>{item.explanation}</p></article>)}</section>}
  </section>;
}
