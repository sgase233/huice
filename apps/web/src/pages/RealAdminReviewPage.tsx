import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { api, ApiRequestError } from '../api/client';
import { criteriaIssue } from '../components/criteriaValidation';
import CriterionEditor, { withQuoteHashes } from '../components/CriterionEditor';
import PolicyImportForm from '../components/PolicyImportForm';
import DiscoveryPanel from './DiscoveryPanel';
import type { PolicyDraft, PolicyImportMetadata, CriterionDraft, ModelUsage } from '../api/types';

const draftStatusLabels: Record<PolicyDraft['status'], string> = { pending_parse: '待解析', pending_review: '待审核', published: '已发布', closed: '已关闭' };
const validationLabels = { pending: '待校验', passed: '校验通过', failed: '校验未通过' } as const;

export default function RealAdminReviewPage({ onUnauthorized, onDirtyChange, accountId }: { onUnauthorized: (status: number) => void; onDirtyChange: (dirty: boolean) => void; accountId: string }) {
  const cryptoReady = typeof globalThis.crypto?.randomUUID === 'function' && typeof globalThis.crypto?.subtle?.digest === 'function';
  const [listLoaded, setListLoaded] = useState(false);
  const [notice, setNotice] = useState('');
  const [items, setItems] = useState<PolicyDraft[]>([]);
  const [draft, setDraft] = useState<PolicyDraft | null>(null);
  const [usage, setUsage] = useState<ModelUsage | null>(null);
  const [extractRetry, setExtractRetry] = useState<{ id: string; version: string; etag: string; key: string } | null>(null);
  const [importDirty, setImportDirty] = useState(false);
  const [discoveryDirty, setDiscoveryDirty] = useState(false);
  const [importUncertain, setImportUncertain] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [localBackup, setLocalBackup] = useState<CriterionDraft[]>([]);
  const [latest, setLatest] = useState<{ draft: PolicyDraft; etag: string } | null>(null);
  const [criteria, setCriteria] = useState<CriterionDraft[]>([]);
  const [dirty, setDirty] = useState(false);
  const [etag, setEtag] = useState<string | null>(null);
  const [appliedFilters, setAppliedFilters] = useState({ status: '', type: '' });
  const [cursor, setCursor] = useState<string | null>(null);
  const [status, setStatus] = useState('');
  const [type, setType] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [invalidField, setInvalidField] = useState<{ index: number; field: string } | null>(null);
  const errorId = useId();
  const request = useRef(0);
  const addCriterion = useRef<HTMLButtonElement>(null);
  const lastCriterion = useRef<HTMLDivElement>(null);
  const errorSummary = useRef<HTMLParagraphElement>(null);
  useEffect(() => { if (error) errorSummary.current?.focus(); }, [error]);
  useEffect(() => () => { request.current++; }, []);
  useLayoutEffect(() => { onDirtyChange(dirty || importDirty || discoveryDirty || busy || localBackup.length > 0); return () => onDirtyChange(false); }, [dirty, importDirty, discoveryDirty, busy, localBackup.length, onDirtyChange]);
  useEffect(() => { const warn = (event: BeforeUnloadEvent) => { if (dirty || importDirty || localBackup.length > 0) { event.preventDefault(); event.returnValue = ''; } }; window.addEventListener('beforeunload', warn); return () => window.removeEventListener('beforeunload', warn); }, [dirty, importDirty, localBackup.length]);
  function assertIdentity(value: PolicyDraft, id: string, version: string) { if (value.id !== id || value.version !== version) throw new Error('返回的数据有误：草稿身份或版本不匹配。'); }
  function strongEtag(value: string | null) { return value && /^\"[^\"]+\"$/.test(value) ? value : null; }
  function discard() { return (!dirty && localBackup.length === 0) || window.confirm('当前修改尚未保存，确定放弃吗？'); }
  function applyDraft(value: PolicyDraft) { setUsage(null); setExtractRetry(null); setConfirmed(false); setLocalBackup([]); setLatest(null); setDraft(value); setCriteria(value.criteria.map(({ validationStatus: _status, ...criterion }) => criterion)); setDirty(false); }
  function fail(error: unknown) {
    if (error instanceof ApiRequestError && (error.status === 401 || error.status === 403)) { onUnauthorized(error.status); return; }
    if (error instanceof ApiRequestError) {
      const details = [error.details.fieldErrors, error.details.findings].flatMap(value => Array.isArray(value) ? value : []).filter((item): item is Record<string, unknown> => !!item && typeof item === 'object' && !Array.isArray(item));
      const messages = details.map(item => [item.field, item.code, item.message].filter(value => typeof value === 'string' && value).join('：')).filter(Boolean);
      setError([error.message, ...messages, error.traceId ? `追踪 ID：${error.traceId}` : ''].filter(Boolean).join('；'));
    } else setError(error instanceof Error ? error.message : '请求失败。');
  }
  async function list(next?: string) {
    if (!next && !discard()) return;
    if (next && (status !== appliedFilters.status || type !== appliedFilters.type)) return;
    if (!next) { setItems([]); setListLoaded(false); setCursor(null); setDraft(null); setEtag(null); setCriteria([]); setDirty(false); setLocalBackup([]); setLatest(null); setExtractRetry(null); setConfirmed(false); }
    const id = ++request.current; setBusy(true); setInvalidField(null); setError(''); setNotice('');
    try { const result = await api.listAdminPolicies({ status: status || undefined, type: type || undefined, cursor: next }); if (id === request.current) { setItems(previous => next ? [...previous, ...result.data.items] : result.data.items); setListLoaded(true); if (!next) { setImportUncertain(false); setAppliedFilters({ status, type }); } setCursor(result.data.nextCursor); if (!next) { setDraft(null); setDirty(false); } } }
    catch (e) { if (id === request.current) fail(e); }
    finally { if (id === request.current) setBusy(false); }
  }
  async function open(item: PolicyDraft, scroll = false) {
    if (!discard()) return;
    setDraft(null); setEtag(null); setCriteria([]); setDirty(false); setLocalBackup([]); setLatest(null); setExtractRetry(null); setConfirmed(false);
    const id = ++request.current; setBusy(true); setInvalidField(null); setError(''); setNotice('');
    try { const result = await api.getAdminPolicy(item.id, item.version); if (id === request.current) { assertIdentity(result.data, item.id, item.version); applyDraft(result.data); setEtag(strongEtag(result.etag)); if (scroll) requestAnimationFrame(() => document.getElementById('admin-policy-draft')?.scrollIntoView({ block: 'start' })); } }
    catch (e) { if (id === request.current) fail(e); }
    finally { if (id === request.current) setBusy(false); }
  }
  async function importPolicy(metadata: PolicyImportMetadata, files: File[]) {
    if (!discard()) return;
    const id = ++request.current; setBusy(true); setInvalidField(null); setError(''); setNotice('');
    try { const result = await api.importPolicy(metadata, files); if (id === request.current) { if (result.data.version !== metadata.version || (metadata.policyId && result.data.id !== metadata.policyId)) throw new Error('返回的数据有误：导入结果身份或版本不匹配。'); applyDraft(result.data); setEtag(strongEtag(result.etag)); } }
    catch (e) { if (id === request.current) { fail(e); if (!(e instanceof ApiRequestError) || e.status === 0 || e.status >= 500) { setImportUncertain(true); setError('导入结果不确定，请先刷新草稿列表核对，勿直接重复导入。'); } } }
    finally { if (id === request.current) setBusy(false); }
  }
  async function extract(retry = false) {
    if (!draft || (!retry && (!etag || !discard()))) return;
    const operation = retry ? extractRetry : { id: draft.id, version: draft.version, etag: etag!, key: crypto.randomUUID() };
    if (!operation) return;
    const id = ++request.current; setBusy(true); setInvalidField(null); setError(''); setNotice('');
    try {
      const result = await api.extractPolicy(operation.id, { version: operation.version }, operation.etag, operation.key);
      if (id === request.current) { assertIdentity(result.data.draft, operation.id, operation.version); applyDraft(result.data.draft); setUsage(result.data.usage); setEtag(strongEtag(result.etag)); }
    } catch (e) {
      if (id === request.current) {
        fail(e);
        if (!(e instanceof ApiRequestError) || e.status === 0 || e.status >= 500) { setExtractRetry(operation); setEtag(null); setError('抽取结果不确定。确认上次请求已结束后，可按原请求重试，或先读取最新草稿核对。'); }
        else if (e.status === 409) { setExtractRetry(null); setEtag(null); setError('草稿版本已变化，请读取最新草稿核对。'); }
      }
    } finally { if (id === request.current) setBusy(false); }
  }
  async function compareLatest() {
    if (!draft) return;
    const id = ++request.current; setBusy(true); setInvalidField(null); setError(''); setNotice('');
    try {
      const result = await api.getAdminPolicy(draft.id, draft.version);
      if (id === request.current) {
        assertIdentity(result.data, draft.id, draft.version);
        const currentEtag = strongEtag(result.etag);
        if (!currentEtag) throw new Error('无法确认最新草稿的有效版本，不能继续保存。');
        setLatest({ draft: result.data, etag: currentEtag });
      }
    } catch (e) { if (id === request.current) fail(e); }
    finally { if (id === request.current) setBusy(false); }
  }
  async function save() {
    if (!draft || !etag || !criteria.length) return;
    const focusField = (index: number, field: string) => requestAnimationFrame(() => { document.querySelector<HTMLElement>(`[data-criterion-index="${index}"] [data-error-field="${field}"]`)?.focus(); });
    const invalid = criteriaIssue(criteria); if (invalid) { setInvalidField({ index: invalid.index, field: invalid.field }); setError(invalid.message); focusField(invalid.index, invalid.field); return; }
    for (const [index, item] of criteria.entries()) {
      const spanIndex = item.sourceSpans.findIndex(span => !draft.sources.some(source => source.id === span.sourceId));
      if (spanIndex !== -1) { setInvalidField({ index, field: `span-${spanIndex}-source` }); setError('引用的来源不在当前草稿中，请重新选择来源或核对草稿。'); focusField(index, `span-${spanIndex}-source`); return; }
    }
    const id = ++request.current; setBusy(true); setInvalidField(null); setError(''); setNotice('');
    try {
      const payload = await withQuoteHashes(criteria);
      const result = await api.saveCriteria(draft.id, { version: draft.version, criteria: payload }, etag);
      if (id === request.current) { assertIdentity(result.data.draft, draft.id, draft.version); applyDraft(result.data.draft); setEtag(strongEtag(result.etag)); setNotice('条件已保存，服务端校验结果已更新。'); }
    } catch (e) {
      if (id === request.current) { fail(e); if (!(e instanceof ApiRequestError) || e.status === 409 || e.status === 0 || e.status >= 500) { setEtag(null); setError('保存发生冲突或结果不确定；本地编辑已保留，请先读取最新版本并人工核对。'); } }
    } finally { if (id === request.current) setBusy(false); }
  }
  async function publish() {
    if (!draft || !etag || dirty || localBackup.length || !confirmed || !draft.criteria.length || draft.criteria.some(c => c.validationStatus !== 'passed') || draft.validationFindings.some(f => f.criterionId !== null || f.code === 'POLICY_UNSATISFIABLE')) return;
    const id = ++request.current; setBusy(true); setInvalidField(null); setError(''); setNotice('');
    try {
      const result = await api.publishPolicy(draft.id, { version: draft.version, reviewConfirmed: true }, etag);
      if (id === request.current) { if (result.data.id !== draft.id || result.data.version !== draft.version) throw new Error('返回的数据有误：发布结果身份或版本不匹配。'); setConfirmed(false); setEtag(null); setDraft(null); setNotice('发布请求已成功，请刷新草稿列表查看状态。'); }
    } catch (e) {
      if (id === request.current) { fail(e); if (!(e instanceof ApiRequestError) || e.status === 409 || e.status === 0 || e.status >= 500) { setEtag(null); setConfirmed(false); setError('发布结果不确定或版本已变，请读取最新草稿核对，不要重复发布。'); } }
    } finally { if (id === request.current) setBusy(false); }
  }
  return <section className="page-block admin-review"><h1>政策审核</h1><p>后台草稿仅供授权人员查看；发布前请人工核对条件及来源。</p>{!cryptoReady && <p role="alert" className="notice warning">当前环境缺少安全加密功能，请通过 HTTPS 或本机 localhost 访问后再抽取或编辑保存。</p>}{notice && <p role="status">{notice}</p>}<DiscoveryPanel accountId={accountId} onDirtyChange={setDiscoveryDirty} onOpenDraft={item => { if (!busy) void open(item, true); }} /><PolicyImportForm onDirtyChange={setImportDirty} busy={busy || importUncertain} onImport={(metadata, files) => void importPolicy(metadata, files)} />{error && <p id={errorId} ref={errorSummary} tabIndex={-1} role="alert" className="notice warning">{error}</p>}
    <div className="panel"><label>状态<select disabled={busy} value={status} onChange={e => setStatus(e.target.value)}><option value="">全部</option><option value="pending_parse">待解析</option><option value="pending_review">待审核</option><option value="published">已发布</option><option value="closed">已关闭</option></select></label><label>类型<select disabled={busy} value={type} onChange={e => setType(e.target.value)}><option value="">全部</option><option value="tax">税收</option><option value="grant">资金</option></select></label><button disabled={busy} onClick={() => void list()}>{busy ? '加载中…' : '读取草稿'}</button></div>
    {listLoaded && !busy && items.length === 0 && status === appliedFilters.status && type === appliedFilters.type && <p>当前筛选条件下没有草稿。</p>}<ul>{items.map(item => <li key={`${item.id}/${item.version}`}><button disabled={busy} onClick={() => void open(item)}>{item.title} · {item.version} · {draftStatusLabels[item.status]}</button></li>)}</ul>{cursor && <button disabled={busy || status !== appliedFilters.status || type !== appliedFilters.type} onClick={() => void list(cursor)}>加载更多</button>}{listLoaded && (status !== appliedFilters.status || type !== appliedFilters.type) && <p>筛选条件已更改，请重新读取草稿；当前列表仍为上次的结果。</p>}
    {draft && <article id="admin-policy-draft" className="panel"><h2>{draft.title}</h2><p>{draft.issuer} · {draft.version} · {draftStatusLabels[draft.status]}</p>{!etag && <p className="notice warning">无法确认草稿的有效版本，暂时不能修改；请读取最新版本。</p>}{draft.status !== 'published' && draft.status !== 'closed' && <button disabled={busy || !etag || !cryptoReady} onClick={() => void extract()}>抽取条款</button>}{extractRetry && <button disabled={busy} onClick={() => void extract(true)}>按原请求重试抽取</button>}{usage && <p>抽取模式：{usage.mode === 'fixture' ? '固定样例' : usage.mode === 'saved' ? '已保存结果' : 'DeepSeek'} · 模型版本：{usage.modelVersion ?? '未提供'} · {usage.tokensUnknown ? '用量未知' : usage.mode === 'deepseek' && usage.tokensUnknown === undefined ? '用量状态未确认' : `输入 ${usage.inputTokens} tokens · 输出 ${usage.outputTokens} tokens`} · 耗时 {usage.elapsedMs} ms</p>}<h3>来源</h3>{draft.sources.map(source => <section key={source.id}><h4>{source.title}</h4><p>{source.sourceUrl}</p><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{source.text}</pre></section>)}<h3>条件及校验</h3>{draft.criteria.map(criterion => <section key={criterion.id}><h4>{criterion.title} · {validationLabels[criterion.validationStatus]}</h4>{criterion.sourceSpans.map((span, i) => <blockquote key={i}>{span.quote}<p>{span.section ?? span.page ?? '无定位'}</p></blockquote>)}</section>)}{!etag && <button disabled={busy} onClick={() => void compareLatest()}>读取最新版本以对照</button>}{latest && <section><h3>版本对照</h3><p>本地编辑仍保留。选择服务端版本后，可逐条重新应用本地条件；请人工检查所有差异。</p>{criteria.map(item => <details key={item.id}><summary>本地：{item.title || item.id}</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(item, null, 2)}</pre></details>)}{latest.draft.criteria.map(item => <details key={item.id}><summary>服务端：{item.title || item.id}</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(item, null, 2)}</pre></details>)}<button onClick={() => { if (window.confirm('放弃本地编辑并采用服务端版本？')) { applyDraft(latest.draft); setEtag(latest.etag); } }}>采用服务端版本</button><button onClick={() => { const saved = criteria; applyDraft(latest.draft); setLocalBackup(saved); setEtag(latest.etag); }}>以服务端版本为基础，逐条应用本地修改</button></section>}{localBackup.length > 0 && <section><h3>待人工重新应用的本地条件</h3>{localBackup.map(item => <div key={item.id}><span>{item.title || item.id}</span><button type="button" onClick={() => { setCriteria(old => old.some(c => c.id === item.id) ? old.map(c => c.id === item.id ? item : c) : [...old, item]); setLocalBackup(old => old.filter(c => c.id !== item.id)); setDirty(true); setConfirmed(false); }}>应用此条件</button><button type="button" onClick={() => setLocalBackup(old => old.filter(c => c.id !== item.id))}>丢弃此条件</button></div>)}</section>}{draft.status !== 'published' && draft.status !== 'closed' && <><h3>编辑条件</h3>{criteria.map((criterion, index) => <div key={criterion.id} data-criterion-index={index} ref={index === criteria.length - 1 ? lastCriterion : undefined}><fieldset disabled={busy}><CriterionEditor invalidField={error && invalidField?.index === index ? invalidField.field : null} errorId={errorId} value={criterion} sources={draft.sources} version={draft.version} onChange={next => { if (invalidField) { setInvalidField(null); setError(''); } setCriteria(old => old.map((item, i) => i === index ? next : item)); setDirty(true); setConfirmed(false); }} /></fieldset><button type="button" disabled={busy} onClick={() => { if (window.confirm('删除此条件？')) { if (invalidField) { setInvalidField(null); setError(''); } setCriteria(old => old.filter((_, i) => i !== index)); setDirty(true); setConfirmed(false); requestAnimationFrame(() => addCriterion.current?.focus()); } }}>删除条件</button></div>)}<button ref={addCriterion} type="button" disabled={busy || !cryptoReady} onClick={() => { setCriteria(old => [...old, { id: crypto.randomUUID(), title: '', expression: { op: 'manual_review', reason: '' }, sourceSpans: [], reviewFinding: 'none' }]); setDirty(true); setConfirmed(false); requestAnimationFrame(() => lastCriterion.current?.querySelector('input')?.focus()); }}>添加条件</button><button disabled={busy || !dirty || !etag || !criteria.length || localBackup.length > 0 || !cryptoReady} onClick={() => void save()}>保存并校验</button></>}{draft.status !== 'published' && draft.status !== 'closed' && <div><label className="inline-check"><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} />我已人工核对条件及来源</label><button disabled={busy || !etag || dirty || localBackup.length > 0 || !confirmed || !draft.criteria.length || draft.criteria.some(c => c.validationStatus !== 'passed') || draft.validationFindings.some(f => f.criterionId !== null || f.code === 'POLICY_UNSATISFIABLE')} onClick={() => void publish()}>确认发布</button></div>}{draft.validationFindings.map((finding, i) => <p key={i}>{finding.code}：{finding.message}</p>)}</article>}
  </section>;
}
