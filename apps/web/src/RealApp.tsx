import { useEffect, useRef, useState } from 'react';
import { api, ApiRequestError } from './api/client';
import type { AssessmentRun, Calculation, Enterprise, EnterpriseInput, PolicyDetail, PolicySummary } from './api/types';
import { factLabels, initialEnterprise } from './demo';
import EnterprisePage from './pages/EnterprisePage';
import StatusBadge from './components/StatusBadge';

const message = (error: unknown) => error instanceof ApiRequestError ? `${error.message}${error.traceId ? `（追踪 ID：${error.traceId}）` : ''}` : error instanceof Error ? error.message : '请求失败。';
type Page = 'enterprise' | 'opportunities' | 'policy';
const pageFromHash = (): Page => location.hash === '#opportunities' ? 'opportunities' : location.hash === '#policy' ? 'policy' : 'enterprise';

function CalculationDetails({ calculation }: { calculation: Calculation }) {
  return <section aria-label="服务端计算过程"><h3>服务端计算过程</h3><p>公式：{calculation.formula}</p><ul>{calculation.inputs.map((input, index) => <li key={index}>{factLabels[input.factRef.key]}（{input.factRef.period.startDate} 至 {input.factRef.period.endDate}）：{input.value ?? '未知'}</li>)}</ul><p>中间值：{calculation.intermediateValues.length ? calculation.intermediateValues.join('、') : '无'}</p><p>结果：{calculation.value ?? '未知'} {calculation.unit}</p></section>;
}

export default function RealApp() {
  const [enterprise, setEnterprise] = useState<Enterprise | null>(null);
  const [draft, setDraft] = useState<EnterpriseInput>(initialEnterprise);
  const [etag, setEtag] = useState<string | null>(null);
  const [policies, setPolicies] = useState<PolicySummary[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [run, setRun] = useState<AssessmentRun | null>(null);
  const [runFresh, setRunFresh] = useState(false);
  const [detail, setDetail] = useState<PolicyDetail | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const requestId = useRef(0);
  const refreshPolicies = useRef<() => void>(() => {});
  const refreshPolicy = useRef<(id: string) => void>(() => {});
  const lastPolicyId = useRef<string | null>(null);
  const skipPolicyHash = useRef(false);
  const [page, setPage] = useState<Page>(pageFromHash);
  const [type, setType] = useState<'' | 'tax' | 'grant'>('');
  const [status, setStatus] = useState<'' | 'published' | 'closed'>('');
  useEffect(() => {
    const onHashChange = () => {
      if (!['', '#enterprise', '#opportunities', '#policy'].includes(location.hash)) return;
      const next = pageFromHash();
      setPage(next);
      if (next === 'opportunities') refreshPolicies.current();
      if (next === 'policy') {
        if (skipPolicyHash.current) skipPolicyHash.current = false;
        else if (lastPolicyId.current) refreshPolicy.current(lastPolicyId.current);
      }
    };
    window.addEventListener('hashchange', onHashChange);
    if (pageFromHash() === 'opportunities') void loadPolicies();
    return () => window.removeEventListener('hashchange', onHashChange);
  }, []);

  function navigate(next: Page) { setPage(next); location.hash = next; }


  async function loadPolicies(next?: string) {
    const id = ++requestId.current;
    setBusy(true); setError('');
    setRunFresh(false);
    try {
      const region = enterprise?.facts.find(f => f.key === 'region' && f.period.startDate === enterprise.period.startDate && f.period.endDate === enterprise.period.endDate)?.value;
      const result = await api.listPolicies({ region: typeof region === 'string' ? region : undefined, type: type || undefined, status: status || undefined, cursor: next });
      if (id !== requestId.current) return;
      if (run) { const refreshed = await api.getRun(run.id); if (id !== requestId.current) return; setRun(refreshed.data); setRunFresh(true); }
      setPolicies(previous => next ? [...previous, ...result.data.items] : result.data.items);
      setCursor(result.data.nextCursor);
    } catch (e) { if (id === requestId.current) setError(message(e)); } finally { if (id === requestId.current) setBusy(false); }
  }
  refreshPolicies.current = () => { void loadPolicies(); };
  async function analyze() {
    if (!enterprise || !selected.length || selected.length > 7) return;
    const id = ++requestId.current;
    setBusy(true); setError('');
    try {
      const result = await api.createRun({ enterpriseId: enterprise.id, policyIds: selected, asOf: new Date().toISOString(), period: enterprise.period });
      if (id !== requestId.current) return;
      setRun(result.data); setRunFresh(true); setDetail(null); lastPolicyId.current = null;
    } catch (e) { if (id === requestId.current) { setRunFresh(false); setError(message(e)); } } finally { if (id === requestId.current) setBusy(false); }
  }
  async function openPolicy(id: string) {
    const request = ++requestId.current;
    lastPolicyId.current = id;
    if (location.hash !== '#policy') skipPolicyHash.current = true;
    setBusy(true); setError(''); setDetail(null); navigate('policy');
    try {
      const refreshed = run ? (await api.getRun(run.id)).data : null;
      if (request !== requestId.current) return;
      if (refreshed) { setRun(refreshed); setRunFresh(true); }
      if (enterprise && refreshed) {
        const current = await api.getEnterprise(enterprise.id);
        if (request !== requestId.current) return;
        setEnterprise(current.data); setEtag(current.etag);
      }
      const version = refreshed?.policyVersionMap[id];
      if (refreshed?.assessments.some(item => item.policyId === id) && !version) throw new Error('接口契约错误：分析结果缺少政策版本。');
      const response = await api.getPolicy(id, version);
      if (request !== requestId.current) return;
      if (response.data.id !== id || (version && response.data.version !== version)) throw new Error('接口契约错误：政策版本与分析快照不一致。');
      const evaluated = refreshed?.assessments.find(item => item.policyId === id);
      if (evaluated && (evaluated.criteriaResults.length !== response.data.criteria.length || evaluated.criteriaResults.some(result => !response.data.criteria.some(criterion => criterion.id === result.criterionId)))) throw new Error('接口契约错误：政策条件与分析快照不一致。');
      setDetail(response.data);
    } catch (e) { if (request === requestId.current) { setRunFresh(false); setError(message(e)); } } finally { if (request === requestId.current) setBusy(false); }
  }
  refreshPolicy.current = id => { void openPolicy(id); };
  const assessment = runFresh ? run?.assessments.find(item => item.policyId === detail?.id) : undefined;
  return <div className="app-shell"><a className="skip-link" href="#real-main">跳到主要内容</a><header className="site-header"><div className="header-inner"><strong>惠策协同 · 真实接口</strong><a href="?mode=demo#enterprise">切换固定样例演示</a></div></header>
    <div className="shell-layout"><nav className="side-nav real-nav" aria-label="主导航"><div className="nav-caption">工作流程</div><button aria-current={page === 'enterprise' ? 'page' : undefined} disabled={busy} onClick={() => navigate('enterprise')}>企业画像</button><button aria-current={page === 'opportunities' ? 'page' : undefined} disabled={busy} onClick={() => { if (location.hash === '#opportunities') void loadPolicies(); else navigate('opportunities'); }}>机会列表</button><button aria-current={page === 'policy' ? 'page' : undefined} disabled={busy} onClick={() => { if (lastPolicyId.current) void openPolicy(lastPolicyId.current); else navigate('policy'); }}>政策详情</button></nav>
    <main id="real-main" className="main-content"><div className="demo-banner">合成企业数据 · 真实接口分析</div>
      {error && <p role="alert" className="notice warning">{error}</p>}
      <div hidden={page !== 'enterprise'}>{enterprise ? <section className="panel"><h1>已保存合成企业：{enterprise.nameSynthetic}</h1><p>画像版本：{enterprise.factVersion}</p><p>ETag：{etag || '服务未返回'}</p><button className="button" onClick={() => { setDraft({ nameSynthetic: enterprise.nameSynthetic, period: enterprise.period, facts: enterprise.facts.map(({ verified: _verified, ...fact }) => fact) }); setEnterprise(null); setRun(null); setSelected([]); setDetail(null); lastPolicyId.current = null; }}>复制为新样本</button></section> : <EnterprisePage real value={draft} onSave={async input => { const request = ++requestId.current; setBusy(true); try { const result = await api.createEnterprise(input); if (request !== requestId.current) throw new Error("请求已被新操作替代，请重新核对。"); setEnterprise(result.data); setEtag(result.etag); setRun(null); setSelected([]); setDetail(null); lastPolicyId.current = null; } catch (e) { throw new Error(message(e)); } finally { if (request === requestId.current) setBusy(false); } }} />}</div>
      {page === 'opportunities' && <section className="page-block"><h1>政策机会</h1>{error && policies.length > 0 && <p className="notice warning">列表可能是上次加载的内容，请重试后核对筛选结果。</p>}<p>候选政策不代表符合条件。选择 1—7 项后发起分析。</p><div className="panel filter-panel"><label>政策类型<select disabled={busy} value={type} onChange={e => setType(e.target.value as typeof type)}><option value="">全部类型</option><option value="tax">税收</option><option value="grant">资金</option></select></label><label>窗口状态<select disabled={busy} value={status} onChange={e => setStatus(e.target.value as typeof status)}><option value="">全部状态</option><option value="published">开放</option><option value="closed">已关闭</option></select></label><button onClick={() => void loadPolicies()} disabled={busy}>应用筛选</button></div><p>已选择 {selected.length} 项政策（包含其他筛选页中的选择）。 <button disabled={busy || selected.length === 0} onClick={() => setSelected([])}>清空选择</button></p><button className="button" disabled={busy || !enterprise || selected.length === 0} onClick={() => void analyze()}>{busy ? '处理中…' : '分析所选政策'}</button>{!enterprise && <p>请先保存企业画像。</p>}{policies.length === 0 && !busy && <p>暂无政策。可重试加载。</p>}<button onClick={() => void loadPolicies()} disabled={busy}>刷新列表</button><div className="policy-grid">{policies.map(policy => { const result = runFresh ? run?.assessments.find(item => item.policyId === policy.id) : undefined; const pendingRefresh = !runFresh && run?.assessments.some(item => item.policyId === policy.id); return <article className="panel" key={policy.id}><label><input type="checkbox" checked={selected.includes(policy.id)} disabled={busy || (!selected.includes(policy.id) && selected.length >= 7)} onChange={e => setSelected(old => e.target.checked ? [...old, policy.id] : old.filter(id => id !== policy.id))} />选择分析</label><h2>{policy.title}</h2><p>{policy.issuer} · {policy.region} · {policy.version}</p>{pendingRefresh ? <p>结果暂不可用，请刷新后核对。</p> : <StatusBadge result={result?.result} validity={result?.validity} policyStatus={policy.status} />}{result && <p>分析依据版本：{run?.policyVersionMap[policy.id] ?? '缺失'}</p>}<button onClick={() => void openPolicy(policy.id)} disabled={busy}>查看详情</button></article>; })}</div>{cursor && <button disabled={busy} onClick={() => void loadPolicies(cursor)}>加载更多</button>}</section>}
      {page === 'policy' && (detail ? <section className="page-block"><h1>{detail.title}</h1><p>{detail.issuer} · {detail.version}</p><StatusBadge result={assessment?.result} validity={assessment?.validity} policyStatus={detail.status} />{detail.status === 'closed' && <p>申报窗口已关闭，仅供查看历史信息。</p>}{assessment && <><p>{assessment.explanation}</p><p>解释模式：{assessment.explanationMode}</p>{assessment.missingFacts.length > 0 && <p>缺少事实：{assessment.missingFacts.map(ref => `${factLabels[ref.key]}（${ref.period.startDate} 至 ${ref.period.endDate}）`).join('、')}</p>}{assessment.warnings.map(w => <p className="notice warning" key={w}>{w}</p>)}{assessment.staleReasons.map((r, i) => <p className="notice warning" key={i}>{r.message}</p>)}{assessment.validUntil && <p>有效截止：{assessment.validUntil}</p>}</>}
        {detail.criteria.map(criterion => { const result = assessment?.criteriaResults.find(c => c.criterionId === criterion.id); const evidence = result?.evidenceRefs ?? criterion.sourceSpans; return <article className="panel" key={criterion.id}><h2>{criterion.title}</h2>{result && <><StatusBadge result={result.result} /><p>{result.reason}</p>{result.factRefs.map((ref, i) => { const fact = run?.factVersion === enterprise?.factVersion ? enterprise?.facts.find(f => f.key === ref.key && f.period.startDate === ref.period.startDate && f.period.endDate === ref.period.endDate) : undefined; return <p key={i}>{factLabels[ref.key]}（{ref.period.startDate} 至 {ref.period.endDate}）：{fact ? `${String(fact.value ?? '未知')} ${fact.unit} · ${fact.source === 'user_input' ? '用户填写' : '合成材料'} · ${fact.verified ? '已核实' : '未核实'}` : run?.factVersion === enterprise?.factVersion ? '未填写' : '历史事实不可用'}</p>; })}{result.calculation && <CalculationDetails calculation={result.calculation} />}</>}{evidence.map((source, i) => <blockquote key={i}><p>{source.quote}</p><a href={source.sourceUrl} target="_blank" rel="noreferrer">官方原文</a> · {source.sourceVersion} {source.section} {source.page && `第 ${source.page} 页`}<details><summary>文本哈希</summary>{source.textHash}</details></blockquote>)}</article>; })}<p>政策信息辅助整理；非税务专业意见；申报前请企业财税人员核对。</p></section> : <p>{busy ? '正在加载详情…' : '请选择政策查看详情。'}</p>)}
    </main></div></div>;
}
