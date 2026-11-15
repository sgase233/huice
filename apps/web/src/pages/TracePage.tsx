import { useEffect, useRef, useState } from 'react';
import { api, ApiRequestError } from '../api/client';
import type { RunSummary, TraceStep } from '../api/types';
import { formatTime } from '../formatTime';

const stageLabels: Record<TraceStep['stage'], string> = { ingestion: '政策导入', extraction: '条款抽取', citation_validation: '引用校验', human_review: '人工审核', profile: '企业画像', retrieval: '政策检索', evaluation: '规则判断', questioning: '补充提问', explanation: '结果解释' };
const runStatus: Record<RunSummary['status'], string> = { succeeded: '已完成', running: '运行中', queued: '排队中', failed: '失败' };
const statusLabels: Record<TraceStep['status'], string> = { ok: '完成', needs_review: '需要审核', retryable_error: '可重试错误', fatal_error: '处理失败' };

/** The caller restricts this view to reviewer sessions. */
export default function TracePage({ runId, onUnauthorized }: { runId: string | null; onUnauthorized: (status: number) => void }) {
  const [id, setId] = useState('');
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loadingRuns, setLoadingRuns] = useState(true);
  const [listError, setListError] = useState('');
  const [steps, setSteps] = useState<TraceStep[] | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const sequence = useRef(0);
  const listSequence = useRef(0);
  useEffect(() => { setSteps(null); setError(''); setBusy(false); sequence.current++; setId(runs.some(item => item.id === runId) ? runId! : ''); }, [runId]);
  useEffect(() => { void loadRuns(); return () => { sequence.current++; listSequence.current++; }; }, []);
  async function loadRuns(next?: string) {
    const request = ++listSequence.current; setLoadingRuns(true); setListError('');
    try {
      const response = await api.listRuns(undefined, next);
      if (request !== listSequence.current) return;
      setRuns(old => next ? [...old, ...response.data.items.filter(item => !old.some(entry => entry.id === item.id))] : response.data.items);
      setCursor(response.data.nextCursor);
      if (!next && runId && response.data.items.some(item => item.id === runId)) setId(runId);
    } catch (e) {
      if (request !== listSequence.current) return;
      if (e instanceof ApiRequestError && (e.status === 401 || e.status === 403)) onUnauthorized(e.status);
      setListError(e instanceof ApiRequestError && (e.status === 401 || e.status === 403) ? '无法访问运行记录，请检查登录状态或联系管理员。' : '运行记录暂时无法读取，请重试。');
    } finally { if (request === listSequence.current) setLoadingRuns(false); }
  }
  async function load() {
    const request = ++sequence.current;
    setSteps(null); setError('');
    if (!id || !runs.some(item => item.id === id)) return;
    setBusy(true);
    try {
      const result = await api.getTrace(id);
      if (request === sequence.current) setSteps([...result.data.steps].sort((a, b) => a.sequence - b.sequence));
    } catch (e) {
      if (request !== sequence.current) return;
      if (e instanceof ApiRequestError && (e.status === 401 || e.status === 403)) onUnauthorized(e.status);
      setError(e instanceof ApiRequestError && e.status === 404 ? '这条运行记录不存在或已不可查看，请重新选择。' : e instanceof ApiRequestError && (e.status === 401 || e.status === 403) ? '无法查看处理轨迹，请检查登录状态或联系管理员。' : '处理轨迹暂时无法读取，请重试。');
    } finally { if (request === sequence.current) setBusy(false); }
  }
  return <section className="page-block"><h1>智能体处理轨迹</h1><label className="trace-input">选择运行记录 <select value={id} disabled={loadingRuns && runs.length === 0} onChange={e => { sequence.current++; setBusy(false); setSteps(null); setError(''); setId(e.target.value); }}><option value="">{loadingRuns && runs.length === 0 ? '正在读取运行记录…' : '请选择运行记录'}</option>{runs.map((item, index) => <option key={item.id} value={item.id}>{formatTime(item.createdAt)}（北京时间） · {runStatus[item.status] ?? '未知状态'} · 第 {index + 1} 条</option>)}</select></label>{listError && <p role="alert">{listError} <button type="button" onClick={() => void loadRuns(cursor ?? undefined)}>重试读取记录</button></p>}{!loadingRuns && !listError && runs.length === 0 && <p>当前没有可见的运行记录。</p>}{cursor && <button type="button" className="button button-secondary" disabled={loadingRuns} onClick={() => void loadRuns(cursor)}>{loadingRuns ? '正在加载…' : '加载更多运行记录'}</button>}<button className="button button-secondary" disabled={busy || !id} onClick={() => void load()}>{busy ? '正在加载…' : '读取轨迹'}</button>{!id && !error && runs.length > 0 && <p>请选择运行记录后查看处理过程。</p>}{error && <p role="alert">{error}</p>}{steps && (steps.length === 0 ? <p>这条分析记录没有处理步骤。</p> : <><p>按已记录的顺序展示；当前记录未区分政策处理与企业分析。</p><ol className="trace-steps">{steps.map((step, index) => <li className="panel" key={`${step.sequence}-${index}`}><h2>{step.sequence}. {stageLabels[step.stage]} · {statusLabels[step.status]}</h2><p>输入：{step.inputSummary}</p><p>输出：{step.outputSummary}</p><p>政策：{step.policyId ?? '无'} · 版本：{step.policyVersion ?? '无'}</p><p>使用的模型或规则版本：{step.modelOrRuleVersion} · 耗时：{step.elapsedMs} 毫秒</p>{step.errorCode && <p>该步骤处理遇到问题，请查看处理结果或联系管理员。</p>}{step.humanAction && <p>人工操作：{step.humanAction}</p>}{step.objectRefs.map((ref, i) => <p key={i}>关联对象：{{ enterprise: '企业', policy: '政策', policyVersion: '政策版本' }[ref.type]} · {ref.id}</p>)}</li>)}</ol></>)}</section>;
}
