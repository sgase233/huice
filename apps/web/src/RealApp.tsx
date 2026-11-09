import WorkflowIcon from './components/WorkflowIcon';
import MagnifyingDock from './components/MagnifyingDock';
import useWorkflowTransition from './components/useWorkflowTransition';
import ThemeToggle from './components/ThemeToggle';
import { useEffect, useRef, useState } from 'react';
import { api, ApiRequestError, setAdminToken } from './api/client';
import TracePage from './pages/TracePage';
import RealAdminReviewPage from './pages/RealAdminReviewPage';
import type { AssessmentRun, Calculation, Enterprise, EnterpriseInput, FactRef, PolicyDetail, PolicySummary } from './api/types';
import { explanationLabels, factLabels, factUnitLabels, factUnits, initialEnterprise } from './demo';
import EnterprisePage from './pages/EnterprisePage';
import StatusBadge from './components/StatusBadge';
import NextQuestion from './components/NextQuestion';
import RealReportPage from './pages/RealReportPage';

const message = (error: unknown) => error instanceof ApiRequestError ? `${error.message}${error.traceId ? `（追踪 ID：${error.traceId}）` : ''}` : error instanceof Error ? error.message : '请求失败。';
const formatFact = (value: string | boolean | null, unit: keyof typeof factUnitLabels) => value === null ? '未知' : typeof value === 'boolean' ? (value ? '是' : '否') : value + (unit === 'CNY' || unit === 'person' ? ` ${factUnitLabels[unit]}` : '');
type Page = 'enterprise' | 'opportunities' | 'policy' | 'report' | 'trace' | 'admin';
const pageFromHash = (): Page => location.hash === '#admin' ? 'admin' : location.hash === '#trace' ? 'trace' : location.hash === '#opportunities' ? 'opportunities' : location.hash === '#policy' ? 'policy' : location.hash === '#report' ? 'report' : 'enterprise';

function CalculationDetails({ calculation }: { calculation: Calculation }) {
  return <section aria-label="服务端计算过程"><h3>服务端计算过程</h3><p>公式：{calculation.formula}</p><ul>{calculation.inputs.map((input, index) => <li key={index}>{factLabels[input.factRef.key]}（{input.factRef.period.startDate} 至 {input.factRef.period.endDate}）：{input.value ?? '未知'}</li>)}</ul><p>中间值：{calculation.intermediateValues.length ? calculation.intermediateValues.join('、') : '无'}</p><p>结果：{calculation.value ?? '未知'} {factUnitLabels[calculation.unit]}</p></section>;
}

export default function RealApp() {
  const [adminDirty, setAdminDirty] = useState(false);
  const [adminAuthorized, setAdminAuthorized] = useState(false);
  const [tokenInput, setTokenInput] = useState('');
  const [authError, setAuthError] = useState('');
  const [enterprise, setEnterprise] = useState<Enterprise | null>(null);
  const [draft, setDraft] = useState<EnterpriseInput>(initialEnterprise);
  const [etag, setEtag] = useState<string | null>(null);
  const [policies, setPolicies] = useState<PolicySummary[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [run, setRun] = useState<AssessmentRun | null>(null);
  const [runIds, setRunIds] = useState<string[]>([]);
  const [runFresh, setRunFresh] = useState(false);
  const [skipped, setSkipped] = useState<FactRef[]>([]);
  const [retryRun, setRetryRun] = useState(false);
  const [detail, setDetail] = useState<PolicyDetail | null>(null);
  const [reportPolicies, setReportPolicies] = useState<PolicyDetail[]>([]);
  const [reportReady, setReportReady] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const requestId = useRef(0);
  const skippedByRun = useRef<Record<string, FactRef[]>>({});
  const refreshPolicies = useRef<() => void>(() => {});
  const refreshPolicy = useRef<(id: string) => void>(() => {});
  const refreshReport = useRef<() => void>(() => {});
  const skipReportHash = useRef(false);
  const skipHomeConfirmation = useRef(false);
  const lastPolicyId = useRef<string | null>(null);
  const skipPolicyHash = useRef(false);
  const [page, setPage] = useState<Page>(pageFromHash);
  const mainContent = useWorkflowTransition(page);
  const navigationState = useRef({ page, adminDirty });
  navigationState.current = { page, adminDirty };
  const [type, setType] = useState<'' | 'tax' | 'grant'>('');
  const [status, setStatus] = useState<'' | 'published' | 'closed'>('');
  useEffect(() => {
    const onHashChange = () => {
      if (!['', '#enterprise', '#opportunities', '#policy', '#report', '#trace', '#admin'].includes(location.hash)) return;
      const next = pageFromHash();
      const confirmedHome = location.hash === '' && skipHomeConfirmation.current;
      skipHomeConfirmation.current = false;
      if (!confirmedHome && navigationState.current.page === 'admin' && next !== 'admin' && navigationState.current.adminDirty && !window.confirm('审核修改尚未保存，确定离开吗？')) { history.replaceState(history.state, '', '#admin'); return; }
      setPage(next);
      if (next === 'opportunities') refreshPolicies.current();
      if (next === 'report') {
        if (skipReportHash.current) skipReportHash.current = false;
        else refreshReport.current();
      }
      if (next === 'policy') {
        if (skipPolicyHash.current) skipPolicyHash.current = false;
        else if (lastPolicyId.current) refreshPolicy.current(lastPolicyId.current);
      }
    };
    window.addEventListener('hashchange', onHashChange);
    if (pageFromHash() === 'opportunities') void loadPolicies();
    return () => window.removeEventListener('hashchange', onHashChange);
  }, []);

  function navigate(next: Page) { if (page === 'admin' && next !== 'admin' && adminDirty && !window.confirm('审核修改尚未保存，确定离开吗？')) return false; navigationState.current = { page: next, adminDirty }; setPage(next); location.hash = next; return true; }


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
      skippedByRun.current = { [result.data.id]: [] };
      setRun(result.data); setRunIds([result.data.id]); setRunFresh(true); setSkipped([]); setRetryRun(false); setDetail(null); lastPolicyId.current = null;
    } catch (e) { if (id === requestId.current) { setRunFresh(false); setError(message(e)); } } finally { if (id === requestId.current) setBusy(false); }
  }
  async function continueRun(nextSkipped: FactRef[], answer?: string | boolean) {
    if (!run || !enterprise) return;
    const question = run.assessments.find(item => item.nextQuestion)?.nextQuestion;
    if (answer !== undefined && (!question || !etag)) { setError('当前问题或企业信息的版本不可用，请刷新后再试。'); return; }
    const id = ++requestId.current;
    setBusy(true); setError('');
    let saved = false;
    try {
      if (answer !== undefined && question && etag) {
        const updated = await api.updateFact(enterprise.id, question.factRef.key, { value: answer, unit: factUnits[question.factRef.key], period: question.factRef.period, source: 'user_input' }, etag);
        saved = true;
        if (id !== requestId.current) return;
        setEnterprise(updated.data); setEtag(updated.etag);
        setRunFresh(false);
      }
      setSkipped(nextSkipped);
      const result = await api.createRun({ enterpriseId: run.enterpriseId, policyIds: run.assessments.map(item => item.policyId), asOf: run.asOf, period: run.period, supersedesRunId: run.id, skippedFactRefs: nextSkipped });
      if (id !== requestId.current) return;
      skippedByRun.current[result.data.id] = nextSkipped;
      setRun(result.data); setRunIds(previous => [...previous, result.data.id]); setRunFresh(true); setRetryRun(false);
    } catch (e) {
      if (id !== requestId.current) return;
      if (saved) { setRetryRun(true); setError(`事实已保存，但分析未完成。${message(e)}`); }
      else if (answer === undefined) { setRetryRun(true); setError(message(e)); }
      else {
        setError(`${message(e)} 请重新读取画像并核对后重试。`);
        try { const current = await api.getEnterprise(enterprise.id); if (id === requestId.current) { setEnterprise(current.data); setEtag(current.etag); } } catch { setEtag(null); }
      }
    } finally { if (id === requestId.current) setBusy(false); }
  }
  async function selectRun(runId: string) {
    const id = ++requestId.current;
    setBusy(true); setError(''); setRunFresh(false); setReportReady(false);
    try {
      const result = await api.getRun(runId);
      if (id !== requestId.current) return;
      setRun(result.data); setRunFresh(true); setSkipped(skippedByRun.current[runId] ?? []); setRetryRun(false); setDetail(null); lastPolicyId.current = null;
    } catch (e) { if (id === requestId.current) setError(message(e)); } finally { if (id === requestId.current) setBusy(false); }
  }
  async function openPolicy(id: string) {
    if (page === 'admin' && !navigate('policy')) return;
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
      if (refreshed?.assessments.some(item => item.policyId === id) && !version) throw new Error('返回的数据有误：分析结果缺少政策版本。');
      const response = await api.getPolicy(id, version);
      if (request !== requestId.current) return;
      if (response.data.id !== id || (version && response.data.version !== version)) throw new Error('返回的数据有误：政策版本与本次分析记录不一致。');
      const evaluated = refreshed?.assessments.find(item => item.policyId === id);
      if (evaluated && (evaluated.criteriaResults.length !== response.data.criteria.length || evaluated.criteriaResults.some(result => !response.data.criteria.some(criterion => criterion.id === result.criterionId)))) throw new Error('返回的数据有误：政策条件与本次分析记录不一致。');
      setDetail(response.data);
    } catch (e) { if (request === requestId.current) { setRunFresh(false); setError(message(e)); } } finally { if (request === requestId.current) setBusy(false); }
  }
  refreshPolicy.current = id => { void openPolicy(id); };
  async function openReport() {
    if (page === 'admin' && !navigate('report')) return;
    if (!run) { navigate('report'); return; }
    const id = ++requestId.current;
    setBusy(true); setError(''); setReportReady(false);
    if (location.hash !== '#report') { skipReportHash.current = true; navigate('report'); }
    try {
      const current = (await api.getRun(run.id)).data;
      for (const item of current.assessments) {
        const preparation = item.preparation;
        if (!preparation || !['tax', 'grant'].includes(preparation.type) || !['tax_self_assessment', 'grant_application'].includes(preparation.handlingMode) || (preparation.type === 'tax') !== (preparation.handlingMode === 'tax_self_assessment') || typeof preparation.officialEntryUrl !== 'string' || !/^https:\/\//i.test(preparation.officialEntryUrl) || !Array.isArray(preparation.items) || preparation.items.some(entry => !entry || typeof entry.title !== 'string' || typeof entry.description !== 'string' || typeof entry.missing !== 'boolean' || !((entry.materialId === undefined && entry.status === undefined) || (typeof entry.materialId === 'string' && entry.materialId.length > 0 && ['facts_filled', 'facts_missing', 'manual_check', 'policy_closed'].includes(entry.status))) || !Array.isArray(entry.requiredFactRefs) || entry.requiredFactRefs.some(ref => !ref || !Object.hasOwn(factLabels, ref.key) || !ref.period || typeof ref.period.startDate !== 'string' || typeof ref.period.endDate !== 'string'))) throw new Error('返回的数据有误：准备清单无效。');
      }
      const details = await Promise.all(current.assessments.map(item => {
        const version = current.policyVersionMap[item.policyId];
        if (!version) throw new Error('返回的数据有误：报告缺少政策版本。');
        return api.getPolicy(item.policyId, version);
      }));
      if (id !== requestId.current) return;
      if (details.some((response, index) => response.data.id !== current.assessments[index].policyId || response.data.version !== current.policyVersionMap[response.data.id])) throw new Error('返回的数据有误：报告政策版本不一致。');
      setRun(current); setRunFresh(true); setReportPolicies(details.map(item => item.data)); setReportReady(true);
    } catch (e) { if (id === requestId.current) setError(message(e)); } finally { if (id === requestId.current) setBusy(false); }
  }
  refreshReport.current = () => { void openReport(); };
  async function downloadReport() {
    if (!run) return;
    setBusy(true); setError('');
    try {
      const result = await api.downloadReport(run.id);
      const url = URL.createObjectURL(new Blob([result.data], { type: 'text/markdown;charset=utf-8' }));
      const link = document.createElement('a'); link.href = url; link.download = `assessment-${run.id}.md`; link.click();
      URL.revokeObjectURL(url);
    } catch (e) { setError(message(e)); } finally { setBusy(false); }
  }
  const assessment = runFresh ? run?.assessments.find(item => item.policyId === detail?.id) : undefined;
  const question = runFresh && run?.id === runIds[runIds.length - 1] ? run.assessments.find(item => item.nextQuestion)?.nextQuestion : null;
  return <div className="app-shell"><a className="skip-link" href="#real-main">跳到主要内容</a><header className="site-header"><div className="header-inner"><a className="brand" href="/" aria-label="惠策协同首页" onClick={event => { if (page === 'admin' && adminDirty && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey) { if (!window.confirm('审核修改尚未保存，确定离开吗？')) event.preventDefault(); else skipHomeConfirmation.current = true; } }}><span className="brand-mark" aria-hidden="true">惠</span><span><strong>惠策协同</strong><small>企业政策核对工作台</small></span></a><div className="header-actions"><a href="?mode=demo#enterprise">切换固定样例演示</a><ThemeToggle /></div></div></header>
    <div className="shell-layout"><MagnifyingDock page={page} real><button className="dock-item" aria-label="企业画像" aria-current={page === 'enterprise' ? 'page' : undefined} disabled={busy} onClick={() => navigate('enterprise')}><WorkflowIcon name="enterprise" /><span className="dock-label">企业画像</span></button><button className="dock-item" aria-label="机会列表" aria-current={page === 'opportunities' ? 'page' : undefined} disabled={busy} onClick={() => { if (location.hash === '#opportunities') void loadPolicies(); else navigate('opportunities'); }}><WorkflowIcon name="opportunities" /><span className="dock-label">机会列表</span></button><button className="dock-item" aria-label="政策详情" aria-current={page === 'policy' ? 'page' : undefined} disabled={busy} onClick={() => { if (lastPolicyId.current) void openPolicy(lastPolicyId.current); else navigate('policy'); }}><WorkflowIcon name="policy" /><span className="dock-label">政策详情</span></button><button className="dock-item" aria-label="清单与报告" aria-current={page === 'report' ? 'page' : undefined} disabled={busy} onClick={() => void openReport()}><WorkflowIcon name="report" /><span className="dock-label">清单与报告</span></button><button className="dock-item" aria-label="处理轨迹" aria-current={page === 'trace' ? 'page' : undefined} disabled={busy} onClick={() => navigate('trace')}><WorkflowIcon name="trace" /><span className="dock-label">处理轨迹</span></button><button className="dock-item" aria-label="政策审核" aria-current={page === 'admin' ? 'page' : undefined} disabled={busy} onClick={() => navigate('admin')}><WorkflowIcon name="admin" /><span className="dock-label">政策审核</span></button></MagnifyingDock>
    <main id="real-main" ref={mainContent} className="main-content"><div className="demo-banner">当前使用示例企业数据，分析结果需结合政策原文复核</div>
      {error && <p role="alert" className="notice warning">{error}</p>}
      {runIds.length > 1 && page === 'opportunities' && <label>本次会话的分析历史 <select disabled={busy || retryRun} value={run?.id ?? ''} onChange={event => void selectRun(event.target.value)}>{runIds.map((id, index) => <option value={id} key={id}>第 {index + 1} 次分析 · {id}</option>)}</select></label>}
      {run && page === 'opportunities' && <>{retryRun && <button disabled={busy} onClick={() => void continueRun(skipped)}>重试分析</button>}{question && enterprise && !retryRun && <NextQuestion key={`${question.factRef.key}/${question.factRef.period.startDate}/${question.factRef.period.endDate}`} question={question} enterprise={enterprise} pending={busy} onAnswer={value => void continueRun(skipped, value)} onSkip={() => void continueRun([...skipped, question.factRef])} />}{runFresh && !question && run.id === runIds[runIds.length - 1] && <p>当前没有需要补充的问题；如有人工核查事项，请查看政策详情。</p>}</>}
      <div hidden={page !== 'enterprise'}>{enterprise ? <section className="panel"><h1>已保存合成企业：{enterprise.nameSynthetic}</h1><details><summary>查看企业信息的版本详情</summary><p>信息版本：{enterprise.factVersion}</p><p>保存版本标识：{etag || '服务未返回'}</p></details><button className="button" onClick={() => { setDraft({ nameSynthetic: enterprise.nameSynthetic, period: enterprise.period, facts: enterprise.facts.map(({ verified: _verified, ...fact }) => fact) }); setEnterprise(null); setRun(null); setSelected([]); setDetail(null); lastPolicyId.current = null; }}>复制为新样本</button></section> : <EnterprisePage real value={draft} onSave={async input => { const request = ++requestId.current; setBusy(true); try { const result = await api.createEnterprise(input); if (request !== requestId.current) throw new Error("请求已被新操作替代，请重新核对。"); setEnterprise(result.data); setEtag(result.etag); setRun(null); setSelected([]); setDetail(null); lastPolicyId.current = null; } catch (e) { throw new Error(message(e)); } finally { if (request === requestId.current) setBusy(false); } }} />}</div>
      {page === 'opportunities' && <section className="page-block"><h1>政策机会</h1>{error && policies.length > 0 && <p className="notice warning">列表可能是上次加载的内容，请重试后核对筛选结果。</p>}<p>候选政策不代表符合条件。选择 1—7 项后发起分析。</p><div className="panel filter-panel"><label>政策类型<select disabled={busy} value={type} onChange={e => setType(e.target.value as typeof type)}><option value="">全部类型</option><option value="tax">税收</option><option value="grant">资金</option></select></label><label>窗口状态<select disabled={busy} value={status} onChange={e => setStatus(e.target.value as typeof status)}><option value="">全部状态</option><option value="published">开放</option><option value="closed">已关闭</option></select></label><button className="button button-secondary" onClick={() => void loadPolicies()} disabled={busy}>应用筛选</button></div><p>已选择 {selected.length} 项政策（包含其他筛选页中的选择）。 <button className="button button-secondary" disabled={busy || selected.length === 0} onClick={() => setSelected([])}>清空选择</button></p><button className="button" disabled={busy || !enterprise || selected.length === 0} onClick={() => void analyze()}>{busy ? '处理中…' : '分析所选政策'}</button>{!enterprise && <p>请先保存企业画像。</p>}{policies.length === 0 && !busy && <p>暂无政策。可重试加载。</p>}<button className="button button-secondary" onClick={() => void loadPolicies()} disabled={busy}>刷新列表</button><div className="policy-grid">{policies.map(policy => { const result = runFresh ? run?.assessments.find(item => item.policyId === policy.id) : undefined; const pendingRefresh = !runFresh && run?.assessments.some(item => item.policyId === policy.id); return <article className="panel" key={policy.id}><label className="inline-check"><input type="checkbox" checked={selected.includes(policy.id)} disabled={busy || (!selected.includes(policy.id) && selected.length >= 7)} onChange={e => setSelected(old => e.target.checked ? [...old, policy.id] : old.filter(id => id !== policy.id))} />选择分析</label><h2>{policy.title}</h2><p>{policy.issuer} · {policy.region} · {policy.version}</p>{pendingRefresh ? <p>结果暂不可用，请刷新后核对。</p> : <StatusBadge result={result?.result} validity={result?.validity} policyStatus={policy.status} />}{result && <p>分析依据版本：{run?.policyVersionMap[policy.id] ?? '缺失'}</p>}<button className="button button-secondary" onClick={() => void openPolicy(policy.id)} disabled={busy}>查看详情</button></article>; })}</div>{cursor && <button disabled={busy} onClick={() => void loadPolicies(cursor)}>加载更多</button>}</section>}
      {(page === 'trace' || page === 'admin') && (adminAuthorized ? <><button className="button button-secondary" onClick={() => { if (adminDirty && !window.confirm('审核修改尚未保存，确定退出吗？')) return; setAdminToken(null); setAdminAuthorized(false); setTokenInput(''); setAuthError(''); }}>退出管理授权</button>{page === 'trace' ? <TracePage runId={run?.id ?? null} onUnauthorized={status => { setAdminToken(null); setAdminAuthorized(false); setTokenInput(''); setAuthError(status === 401 ? '管理令牌无效，请重新授权。' : '演示会话无效或已过期，请从演示入口重新进入。'); }} /> : <RealAdminReviewPage onDirtyChange={setAdminDirty} onUnauthorized={status => { setAdminToken(null); setAdminAuthorized(false); setTokenInput(''); setAuthError(status === 401 ? '管理令牌无效，请重新授权。' : '演示会话无效或已过期，请从演示入口重新进入。'); }} />}</> : <section className="panel"><h1>受限功能授权</h1>{authError && <p role="alert">{authError}</p>}<form className="authorization-form" onSubmit={event => { event.preventDefault(); if (!tokenInput.trim()) return; setAdminToken(tokenInput.trim()); setTokenInput(''); setAuthError(''); setAdminAuthorized(true); }}><label>管理令牌 <input type="password" autoComplete="off" value={tokenInput} onChange={event => setTokenInput(event.target.value)} /></label><button className="button" type="submit">使用令牌</button></form><p>令牌仅保存在当前页面内存中。</p></section>)}
      {page === 'report' && (run && reportReady ? <RealReportPage run={run} policies={reportPolicies} busy={busy} onDownload={() => void downloadReport()} /> : <p>{busy ? '正在加载报告…' : error ? '报告暂不可用。' : '请先完成分析，再打开报告。'}</p>)}
      {page === 'policy' && (detail ? <section className="page-block"><h1>{detail.title}</h1><p>{detail.issuer} · {detail.version}</p><StatusBadge result={assessment?.result} validity={assessment?.validity} policyStatus={detail.status} />{detail.status === 'closed' && <p>申报窗口已关闭，仅供查看历史信息。</p>}{assessment && <><p>{assessment.explanation}</p><p>说明来源：{explanationLabels[assessment.explanationMode]}</p>{assessment.missingFacts.length > 0 && <p>缺少的企业信息：{assessment.missingFacts.map(ref => `${factLabels[ref.key]}（${ref.period.startDate} 至 ${ref.period.endDate}）`).join('、')}</p>}{assessment.warnings.map(w => <p className="notice warning" key={w}>{w}</p>)}{assessment.staleReasons.map((r, i) => <p className="notice warning" key={i}>{r.message}</p>)}{assessment.validUntil && <p>有效截止：{assessment.validUntil}</p>}</>}
        {detail.criteria.map(criterion => { const result = assessment?.criteriaResults.find(c => c.criterionId === criterion.id); const evidence = result?.evidenceRefs ?? criterion.sourceSpans; return <article className="panel" key={criterion.id}><h2>{criterion.title}</h2>{result && <><StatusBadge result={result.result} /><p>{result.reason}</p>{result.factRefs.map((ref, i) => { const fact = run?.factVersion === enterprise?.factVersion ? enterprise?.facts.find(f => f.key === ref.key && f.period.startDate === ref.period.startDate && f.period.endDate === ref.period.endDate) : undefined; return <p key={i}>{factLabels[ref.key]}（{ref.period.startDate} 至 {ref.period.endDate}）：{fact ? `${formatFact(fact.value, fact.unit)} · ${fact.source === 'user_input' ? '用户填写' : '合成材料'} · ${fact.verified ? '已核实' : '未核实'}` : run?.factVersion === enterprise?.factVersion ? '未填写' : '历史事实不可用'}</p>; })}{result.calculation && <CalculationDetails calculation={result.calculation} />}</>}{evidence.map((source, i) => <blockquote key={i}><p>{source.quote}</p><a href={source.sourceUrl} target="_blank" rel="noreferrer">官方原文</a> · {source.sourceVersion} {source.section} {source.page && `第 ${source.page} 页`}<details><summary>查看原文校验信息</summary>{source.textHash}</details></blockquote>)}</article>; })}<p>政策信息辅助整理；非税务专业意见；申报前请企业财税人员核对。</p></section> : <p>{busy ? '正在加载详情…' : '请选择政策查看详情。'}</p>)}
    </main></div></div>;
}
