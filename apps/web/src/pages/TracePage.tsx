import { useEffect, useRef, useState } from 'react';
import { api, ApiRequestError } from '../api/client';
import type { TraceStep } from '../api/types';

const stageLabels: Record<TraceStep['stage'], string> = { ingestion: '政策导入', extraction: '条款抽取', citation_validation: '引用校验', human_review: '人工审核', profile: '企业画像', retrieval: '政策检索', evaluation: '规则判断', questioning: '补充提问', explanation: '结果解释' };
const statusLabels: Record<TraceStep['status'], string> = { ok: '完成', needs_review: '需要审核', retryable_error: '可重试错误', fatal_error: '处理失败' };

/** Render only after the restricted session and admin token have been supplied. */
export default function TracePage({ runId, onUnauthorized }: { runId: string | null; onUnauthorized: (status: number) => void }) {
  const [id, setId] = useState(runId ?? '');
  const [steps, setSteps] = useState<TraceStep[] | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const sequence = useRef(0);
  useEffect(() => { setId(runId ?? ''); setSteps(null); setError(''); setBusy(false); sequence.current++; }, [runId]);
  useEffect(() => () => { sequence.current++; }, []);
  async function load() {
    const request = ++sequence.current;
    setSteps(null); setError('');
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id)) { setError('请输入有效的运行 UUID。'); return; }
    setBusy(true);
    try {
      const result = await api.getTrace(id);
      if (request === sequence.current) setSteps([...result.data.steps].sort((a, b) => a.sequence - b.sequence));
    } catch (e) {
      if (request !== sequence.current) return;
      if (e instanceof ApiRequestError && (e.status === 401 || e.status === 403)) onUnauthorized(e.status);
      setError(e instanceof Error ? e.message : '轨迹读取失败。');
    } finally { if (request === sequence.current) setBusy(false); }
  }
  return <section className="page-block"><h1>智能体处理轨迹</h1><label>分析运行 UUID <input value={id} onChange={e => { sequence.current++; setBusy(false); setSteps(null); setError(''); setId(e.target.value); }} /></label><button className="button button-secondary" disabled={busy} onClick={() => void load()}>{busy ? '正在加载…' : '读取轨迹'}</button>{!id && !error && <p>尚未选择分析运行，请输入运行 UUID 后读取轨迹。</p>}{error && <p role="alert">{error}</p>}{steps && (steps.length === 0 ? <p>该运行没有轨迹步骤。</p> : <ol className="trace-steps">{steps.map((step, index) => <li className="panel" key={`${step.sequence}-${index}`}><h2>{step.sequence}. {stageLabels[step.stage]} · {statusLabels[step.status]}</h2><p>输入：{step.inputSummary}</p><p>输出：{step.outputSummary}</p><p>政策：{step.policyId ?? '无'} · 版本：{step.policyVersion ?? '无'}</p><p>模型或规则版本：{step.modelOrRuleVersion} · 耗时：{step.elapsedMs} ms</p>{step.errorCode && <p>错误：{step.errorCode}</p>}{step.humanAction && <p>人工操作：{step.humanAction}</p>}{step.objectRefs.map((ref, i) => <p key={i}>关联对象：{{ enterprise: '企业', policy: '政策', policyVersion: '政策版本' }[ref.type]} · {ref.id}</p>)}</li>)}</ol>)}</section>;
}
