import { useEffect, useState } from 'react';
import { api, ApiRequestError } from '../api/client';
import type { CandidateDetail, CandidateMetadata } from '../api/discoveryTypes';
import type { CriterionDraft, PolicyDraft, SourceDocument } from '../api/types';
import CriterionEditor, { withQuoteHashes } from '../components/CriterionEditor';
import { criteriaIssue } from '../components/criteriaValidation';

type PendingSubmit = { candidateId: string; etag: string; key: string };
const strong = (value: string | null) => value && /^"[^"]+"$/.test(value) ? value : null;
const text = (error: unknown) => {
  if (!(error instanceof ApiRequestError)) return error instanceof Error ? error.message : '请求失败';
  const fields = Array.isArray(error.details.fieldErrors) ? error.details.fieldErrors.flatMap(item => item && typeof item === 'object' && typeof item.message === 'string' ? [`${typeof item.field === 'string' ? `${item.field}：` : ''}${item.message}`] : []) : [];
  return [error.message, ...fields, error.traceId ? `追踪 ID：${error.traceId}` : ''].filter(Boolean).join('；');
};
const definitive = (error: unknown) => error instanceof ApiRequestError && error.status >= 400 && error.status < 500;

export default function CandidateEditor({ candidate, initialEtag, accountId, onDirtyChange, onUpdated, onOpenDraft }: { candidate: CandidateDetail; initialEtag: string | null; accountId: string; onDirtyChange: (dirty: boolean) => void; onUpdated: (value: CandidateDetail) => void; onOpenDraft: (draft: PolicyDraft) => void }) {
  const storageKey = `huice.discovery.submit.${encodeURIComponent(accountId)}.${encodeURIComponent(candidate.id)}`;
  const [server, setServer] = useState(candidate);
  const [etag, setEtag] = useState(strong(initialEtag));
  const [metadata, setMetadata] = useState<CandidateMetadata>(candidate.metadata);
  const [criteria, setCriteria] = useState<CriterionDraft[]>(candidate.criteria.map(({ validationStatus: _status, ...item }) => item));
  const [dirty, setDirty] = useState(false);
  const [criteriaDirty, setCriteriaDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [createdDraft, setCreatedDraft] = useState<PolicyDraft | null>(null);
  const [reason, setReason] = useState('');
  const [latest, setLatest] = useState<{ data: CandidateDetail; etag: string } | null>(null);
  const [pending, setPending] = useState<PendingSubmit | null>(() => {
    try { const raw = localStorage.getItem(storageKey); if (!raw) return null; const value = JSON.parse(raw); return value.candidateId === candidate.id && typeof value.key === 'string' && strong(value.etag) ? value : null; } catch { return null; }
  });
  const editable = server.status === 'pending_review' || server.status === 'extraction_failed';
  const cryptoReady = typeof crypto?.randomUUID === 'function' && !!crypto?.subtle;
  const source: SourceDocument = { id: server.id, kind: 'body', title: '候选正文', sourceUrl: server.sourceUrl, fetchedAt: server.fetchedAt, contentHash: server.contentHash, text: server.bodyText };
  useEffect(() => { onDirtyChange(dirty || busy || !!pending); return () => onDirtyChange(false); }, [dirty, busy, pending, onDirtyChange]);
  useEffect(() => { const warn = (event: BeforeUnloadEvent) => { if (dirty || pending || busy) { event.preventDefault(); event.returnValue = ''; } }; window.addEventListener('beforeunload', warn); return () => window.removeEventListener('beforeunload', warn); }, [dirty, pending, busy]);
  function apply(result: CandidateDetail, nextEtag: string | null) { if (result.id !== candidate.id) throw new Error('返回的候选身份不匹配。'); setServer(result); setMetadata(result.metadata); setCriteria(result.criteria.map(({ validationStatus: _status, ...item }) => item)); setEtag(strong(nextEtag)); setDirty(false); setCriteriaDirty(false); setLatest(null); onUpdated(result); }
  function updateMeta<K extends keyof CandidateMetadata>(key: K, value: CandidateMetadata[K]) { setMetadata(old => ({ ...old, [key]: value })); setDirty(true); }
  async function refresh() { setBusy(true); setError(''); try { const result = await api.discoveryCandidate(candidate.id); if (result.data.id !== candidate.id || !strong(result.etag)) throw new Error('无法确认最新候选身份及版本。'); if (dirty) setLatest({ data: result.data, etag: result.etag! }); else apply(result.data, result.etag); } catch (e) { setError(text(e)); } finally { setBusy(false); } }
  async function save() {
    if (!etag || !editable || !cryptoReady) return;
    const issue = criteriaDirty && criteria.length ? criteriaIssue(criteria) : null;
    if (issue) { setError(issue.message); return; }
    if (criteriaDirty && criteria.some(c => c.sourceSpans.some(span => span.sourceId !== candidate.id || span.sourceUrl !== server.sourceUrl || span.sourceVersion !== 'candidate'))) { setError('条件引用必须指向此候选的正文。'); return; }
    setBusy(true); setError(''); setNotice('');
    try { const result = await api.updateDiscoveryCandidate(candidate.id, { metadata, ...(criteriaDirty ? { criteriaDrafts: await withQuoteHashes(criteria) } : {}) }, etag); apply(result.data, result.etag); setNotice('候选已保存，服务端校验结果已更新。'); }
    catch (e) { if (!definitive(e) || (e instanceof ApiRequestError && e.status === 409)) { setEtag(null); setError('保存结果不确定或版本已变化，本地输入已保留；请读取最新版本并核对。'); } else setError(text(e)); } finally { setBusy(false); }
  }
  async function submit(operation?: PendingSubmit) {
    if (!operation && (!etag || dirty || !editable || !cryptoReady)) return;
    const next = operation ?? { candidateId: candidate.id, etag: etag!, key: crypto.randomUUID() };
    try { localStorage.setItem(storageKey, JSON.stringify(next)); } catch { setError('无法保存重试信息，暂不能安全提交。请检查浏览器存储设置。'); return; }
    setPending(next); setBusy(true); setError(''); setNotice('');
    try { const result = await api.submitDiscoveryCandidate(candidate.id, next.etag, next.key); if (result.data.status !== 'pending_review' || (server.metadata.policyId && result.data.id !== server.metadata.policyId) || (server.metadata.version && result.data.version !== server.metadata.version)) throw new Error('返回的草稿身份或状态不匹配。'); localStorage.removeItem(storageKey); setPending(null); setEtag(null); setCreatedDraft(result.data); setNotice(`已生成待审草稿：${result.data.title}（${result.data.version}，政策 ID：${result.data.id}）。尚未发布。`); }
    catch (e) { if (definitive(e) && e instanceof ApiRequestError && ![401, 403, 429].includes(e.status)) { localStorage.removeItem(storageKey); setPending(null); setEtag(null); setError(`${text(e)} 请读取最新候选核对。`); } else setError('提交结果尚不确定，请恢复会话后使用原请求重试；不要换键再次提交。'); } finally { setBusy(false); }
  }
  async function discard() { if (!etag || dirty || !editable || !window.confirm('确认丢弃此候选？丢弃后不会生成政策草稿。')) return; setBusy(true); setError(''); setNotice(''); try { const result = await api.discardDiscoveryCandidate(candidate.id, etag, reason.trim()); apply(result.data, result.etag); setNotice('候选已丢弃。'); } catch (e) { if (!definitive(e) || (e instanceof ApiRequestError && e.status === 409)) { setEtag(null); setError('丢弃结果不确定或版本已变化，请读取最新候选核对。'); } else setError(text(e)); } finally { setBusy(false); } }
  const disabled = busy || !!pending || !editable;
  return <section aria-label="候选审核"><h4>候选审核</h4>{error && <p role="alert" className="notice warning">{error}</p>}{notice && <p role="status">{notice}</p>}{createdDraft && <button disabled={busy} onClick={() => onOpenDraft(createdDraft)}>打开待审草稿</button>}{pending && <p>上次提交结果尚未确认；可安全地使用原请求重试。</p>}{pending && <button disabled={busy} onClick={() => void submit(pending)}>按原请求重试提交</button>}{!etag && !pending && <p>候选版本不可用，请读取最新版本后操作。</p>}<button disabled={busy || !!pending} onClick={() => void refresh()}>读取最新候选</button>{latest && <section><h5>版本对照</h5><p>本地输入尚未保存，请逐项对照；采用最新版本将放弃本地输入。</p><details><summary>查看本地输入</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify({ metadata, criteria }, null, 2)}</pre></details><details><summary>查看服务端最新内容</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify({ metadata: latest.data.metadata, criteria: latest.data.criteria }, null, 2)}</pre></details><button onClick={() => { if (window.confirm('确认放弃本地输入并采用服务端版本？')) apply(latest.data, latest.etag); }}>放弃本地输入并采用最新版本</button></section>}
    <fieldset disabled={disabled}><legend>政策信息</legend>{([['version','政策版本'],['policyId','已有政策 ID（新建留空）'],['title','标题'],['issuer','发布机关'],['region','地区代码'],['documentNumber','文号'],['publishedAt','发布日期'],['effectiveFrom','生效日期'],['effectiveTo','失效日期'],['officialEntryUrl','官方办理网址']] as const).map(([key,label]) => <label key={key}>{label}<input type={['publishedAt','effectiveFrom','effectiveTo'].includes(key) ? 'date' : 'text'} value={metadata[key] ?? ''} onChange={e => updateMeta(key, e.target.value || null)} /></label>)}<label>政策类型<select value={metadata.type ?? ''} onChange={e => updateMeta('type', (e.target.value || null) as CandidateMetadata['type'])}><option value="">待核对</option><option value="tax">税收</option><option value="grant">资金</option></select></label><label>适用主体（每行一项）<textarea value={(metadata.applicableEntities ?? []).join('\n')} onChange={e => updateMeta('applicableEntities', e.target.value ? e.target.value.split('\n') : null)} /></label><label>申报开始时间（含时区）<input value={metadata.applicationWindow?.opensAt ?? ''} onChange={e => updateMeta('applicationWindow', { opensAt: e.target.value, closesAt: metadata.applicationWindow?.closesAt ?? '' })} /></label><label>申报结束时间（含时区）<input value={metadata.applicationWindow?.closesAt ?? ''} onChange={e => updateMeta('applicationWindow', { opensAt: metadata.applicationWindow?.opensAt ?? '', closesAt: e.target.value })} /></label><button type="button" onClick={() => updateMeta('applicationWindow', null)}>清除申报窗口</button></fieldset>
    <h5>结构化条件</h5>{criteria.map((item, i) => <div key={item.id}><fieldset disabled={disabled}><CriterionEditor value={item} sources={[source]} version="candidate" invalidField={null} errorId="" onChange={next => { setCriteria(old => old.map((c, index) => index === i ? next : c)); setCriteriaDirty(true); setDirty(true); }} /></fieldset><button disabled={disabled} onClick={() => { if (window.confirm('删除此条件？')) { setCriteria(old => old.filter((_, index) => index !== i)); setCriteriaDirty(true); setDirty(true); } }}>删除条件</button></div>)}<button disabled={disabled || !cryptoReady} onClick={() => { setCriteria(old => [...old, { id: crypto.randomUUID(), title: '', expression: { op: 'manual_review', reason: '' }, sourceSpans: [], reviewFinding: 'none' }]); setCriteriaDirty(true); setDirty(true); }}>添加条件</button><button disabled={disabled || !dirty || !etag || !cryptoReady} onClick={() => void save()}>保存候选并校验</button>
    <h5>提交或丢弃</h5><p>提交会生成待审草稿，不会直接发布。请先核对正文、附件和条件。</p><button disabled={disabled || dirty || !etag || !cryptoReady || server.status !== 'pending_review' || server.missingFields.length > 0 || !server.criteria.length || server.criteria.some(c => c.validationStatus !== 'passed')} onClick={() => void submit()}>提交为待审草稿</button><label>丢弃原因（可选）<input maxLength={500} value={reason} disabled={disabled} onChange={e => setReason(e.target.value)} /></label><button disabled={disabled || dirty || !etag} onClick={() => void discard()}>丢弃候选</button>
  </section>;
}
