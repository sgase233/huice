import { useEffect, useState } from 'react';
import { api, ApiRequestError } from '../api/client';
import type { CandidateDetail, CandidateList, DiscoveryRunList, DiscoverySettings } from '../api/discoveryTypes';
import CandidateEditor from './CandidateEditor';
import type { PolicyDraft } from '../api/types';

const labels: Record<string, string> = { pending_review: '待审核', extraction_failed: '抽取失败', submitted: '已提交', discarded: '已丢弃', expired: '已过期', running: '运行中', succeeded: '已完成', partial_failed: '部分失败', failed: '失败' };
const message = (e: unknown) => e instanceof ApiRequestError ? `${e.message}${e.traceId ? `（追踪 ID：${e.traceId}）` : ''}` : e instanceof Error ? e.message : '请求失败';

export default function DiscoveryPanel({ onDirtyChange, accountId, onOpenDraft }: { onDirtyChange: (dirty: boolean) => void; accountId: string; onOpenDraft: (draft: PolicyDraft) => void }) {
  const [settings, setSettings] = useState<DiscoverySettings | null>(null);
  const [savedSettings, setSavedSettings] = useState<DiscoverySettings | null>(null);
  const [etag, setEtag] = useState<string | null>(null);
  const [urls, setUrls] = useState('');
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [runs, setRuns] = useState<DiscoveryRunList | null>(null);
  const [candidates, setCandidates] = useState<CandidateList | null>(null);
  const [status, setStatus] = useState('');
  const [applied, setApplied] = useState('');
  const [detail, setDetail] = useState<CandidateDetail | null>(null);
  const [candidateEtag, setCandidateEtag] = useState<string | null>(null);
  const [candidateDirty, setCandidateDirty] = useState(false);
  useEffect(() => { onDirtyChange(dirty || busy || candidateDirty); return () => onDirtyChange(false); }, [dirty, busy, candidateDirty, onDirtyChange]);
  useEffect(() => { const warn = (e: BeforeUnloadEvent) => { if (dirty) { e.preventDefault(); e.returnValue = ''; } }; window.addEventListener('beforeunload', warn); return () => window.removeEventListener('beforeunload', warn); }, [dirty]);
  async function loadSettings() {
    if (dirty && !window.confirm('放弃未保存的发现设置并重新读取？')) return;
    setBusy(true); setError(''); setNotice('');
    try { const r = await api.discoverySettings(); setSettings(r.data); setSavedSettings(r.data); setUrls(r.data.sourceUrls.join('\n')); setEtag(r.etag && /^"[^"]+"$/.test(r.etag) ? r.etag : null); setDirty(false); }
    catch (e) { setSettings(null); setSavedSettings(null); setEtag(null); setError(message(e)); } finally { setBusy(false); }
  }
  async function saveSettings() {
    if (!settings || !etag) return;
    const sourceUrls = urls.split(/\r?\n/).map(x => x.trim()).filter(Boolean);
    setBusy(true); setError(''); setNotice('');
    try { const r = await api.updateDiscoverySettings({ enabled: settings.enabled, sourceUrls, intervalMinutes: settings.intervalMinutes, timeZone: settings.timeZone }, etag); setSettings(r.data); setSavedSettings(r.data); setUrls(r.data.sourceUrls.join('\n')); setEtag(r.etag && /^"[^"]+"$/.test(r.etag) ? r.etag : null); setDirty(false); setNotice('发现设置已保存。'); }
    catch (e) { if (!(e instanceof ApiRequestError) || e.status === 0 || e.status === 409 || e.status >= 500) { setEtag(null); setError('设置版本已变化或保存结果不确定。输入已保留，请先核对后重新读取。'); } else setError(message(e)); } finally { setBusy(false); }
  }
  async function loadRuns(cursor?: string) { setBusy(true); setError(''); try { const r = await api.discoveryRuns(cursor); setRuns(old => ({ ...r.data, items: cursor && old ? [...old.items, ...r.data.items] : r.data.items })); } catch (e) { if (!cursor) setRuns(null); setError(message(e)); } finally { setBusy(false); } }
  async function loadCandidates(cursor?: string) { if (cursor && status !== applied) return; if (!cursor && candidateDirty && !window.confirm('候选有未保存的内容或待恢复的提交，确定离开？')) return; setBusy(true); setError(''); if (!cursor) { setCandidates(null); setDetail(null); } try { const r = await api.discoveryCandidates(status || undefined, cursor); setCandidates(old => ({ ...r.data, items: cursor && old ? [...old.items, ...r.data.items] : r.data.items })); setApplied(status); } catch (e) { setError(message(e)); } finally { setBusy(false); } }
  async function openCandidate(id: string) { if (candidateDirty && !window.confirm('候选有未保存的内容或待恢复的提交，确定离开？')) return; setBusy(true); setError(''); setDetail(null); setCandidateEtag(null); try { const r = await api.discoveryCandidate(id); if (r.data.id !== id) throw new Error('返回的候选身份不匹配。'); setDetail(r.data); setCandidateEtag(r.etag); } catch (e) { setError(message(e)); } finally { setBusy(false); } }
  return <section aria-label="政策发现"><h2>政策发现</h2><p>自动发现的候选需要人工审核；手动导入仍可使用。</p>{error && <p role="alert" className="notice warning">{error}</p>}{notice && <p role="status">{notice}</p>}{dirty && <p>发现设置有未保存的修改；下次运行时间来自上次读取。</p>}
    <h3>发现设置</h3><button disabled={busy} onClick={() => void loadSettings()}>读取发现设置</button>{settings && savedSettings && <div><p>上次读取或保存：{!savedSettings.sourceUrls.length ? '尚未配置来源。' : savedSettings.enabled ? '自动发现已启用。' : '自动发现已停用。'} 下次计划：{savedSettings.nextRunAt ?? '未安排'}</p>{!etag && <p>无法确认设置版本，请重新读取后保存。</p>}<label className="inline-check"><input type="checkbox" checked={settings.enabled} disabled={busy} onChange={e => { setSettings({ ...settings, enabled: e.target.checked }); setDirty(true); }} />启用自动发现</label><label>来源网址（每行一个）<textarea value={urls} disabled={busy} onChange={e => { setUrls(e.target.value); setDirty(true); }} /></label><label>抓取周期（分钟）<input type="number" min={settings.limits.minIntervalMinutes} max={settings.limits.maxIntervalMinutes} value={settings.intervalMinutes} disabled={busy} onChange={e => { setSettings({ ...settings, intervalMinutes: Number(e.target.value) }); setDirty(true); }} /></label><label>时区<input value={settings.timeZone} disabled={busy} onChange={e => { setSettings({ ...settings, timeZone: e.target.value }); setDirty(true); }} /></label><p>允许 {settings.limits.minIntervalMinutes}—{settings.limits.maxIntervalMinutes} 分钟，最多 {settings.limits.maxSourceUrls} 个来源。</p><button disabled={busy || !etag || !dirty} onClick={() => void saveSettings()}>保存发现设置</button></div>}
    <h3>运行记录</h3><button disabled={busy} onClick={() => void loadRuns()}>读取运行记录</button>{runs && <>{runs.items.length === 0 && <p>尚无运行记录；未启用或未到运行时间时不会产生记录。</p>}<ul>{runs.items.map(run => <li key={run.id}><p>{run.startedAt} · {labels[run.status] ?? '未知状态'} · {run.status === 'succeeded' && run.terminalReason === 'no_new_policy' ? run.sourcesChecked === 0 ? '本轮未检查来源，无法判断是否有新政策' : '没有新政策' : run.detail ?? run.terminalReason ?? '尚未结束'}</p><p>新候选 {run.newCandidates} · 确认重复 {run.duplicates} · 疑似重复 {run.suspectedDuplicates} · 失败来源 {run.failedSources} · 正文失败 {run.failedArticles} · {run.tokensUnknown ? '用量未知' : `用量 ${run.tokensUsed}`}</p>{run.sourceStatus.map((source, i) => <p key={i}>{source.url}：{source.status} {source.message ?? source.errorCategory ?? ''}</p>)}</li>)}</ul>{runs.nextCursor && <button disabled={busy} onClick={() => void loadRuns(runs.nextCursor!)}>加载更多运行记录</button>}</>}
    <h3>政策候选</h3><label>候选状态<select value={status} disabled={busy} onChange={e => setStatus(e.target.value)}><option value="">全部</option>{Object.entries(labels).filter(([k]) => ['pending_review','extraction_failed','submitted','discarded','expired'].includes(k)).map(([k,v]) => <option key={k} value={k}>{v}</option>)}</select></label><button disabled={busy} onClick={() => void loadCandidates()}>读取候选</button>{candidates && <>{status !== applied && <p>筛选已更改，请重新读取候选。</p>}{!candidates.items.length && status === applied && <p>当前筛选下没有候选。</p>}<ul>{candidates.items.map(item => <li key={item.id}><button disabled={busy} onClick={() => void openCandidate(item.id)}>{item.title ?? '标题待核对'} · {labels[item.status] ?? '未知状态'}</button>{(item.attachmentCount + item.unsupportedAttachmentCount > 0) && <span> · 附件需人工核对</span>}{item.suspectedDuplicateOf && <span> · 疑似重复，需核对</span>}</li>)}</ul>{candidates.nextCursor && <button disabled={busy || status !== applied} onClick={() => void loadCandidates(candidates.nextCursor!)}>加载更多候选</button>}</>}
    {detail && <article><h3>{detail.metadata.title ?? '标题待核对'}</h3><p>{labels[detail.status] ?? '未知状态'} · 来源：{detail.sourceUrl}</p><p>抓取时间：{detail.fetchedAt} · 正文哈希：{detail.contentHash}</p><p>缺少信息：{detail.missingFields.join('、') || '无'}</p>{detail.suspectedDuplicateOf && <p>疑似与候选 {detail.suspectedDuplicateOf} 重复{detail.suspectedDuplicateSourceUrl ? `，对照来源：${detail.suspectedDuplicateSourceUrl}` : '，对照来源不可用'}；请人工比较，疑似不等于确认重复。</p>}{detail.versionChanged && <p>同一来源内容有版本变化{detail.existingPolicyId ? `；已有政策 ID：${detail.existingPolicyId}` : ''}{detail.existingVersion ? `，已有版本：${detail.existingVersion}` : ''}{detail.duplicateOf ? `，上次候选：${detail.duplicateOf}` : ''}。请确认是否需要新增版本。</p>}{(detail.attachmentUrls.length > 0 || detail.unsupportedAttachmentUrls.length > 0) && <p className="notice warning">附件未自动解析，请人工核对；附件内容不能作为正文引文。</p>}{[...detail.attachmentUrls, ...detail.unsupportedAttachmentUrls].map((url, i) => <p key={i}>{url}</p>)}<details><summary>查看抓取正文</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{detail.bodyText}</pre></details><h4>已抽取条件</h4>{detail.criteria.map(c => <p key={c.id}>{c.title} · {c.validationStatus}</p>)}{detail.validationFindings.map((f, i) => <p key={i}>{f.code}：{f.message}</p>)}<CandidateEditor key={detail.id} candidate={detail} initialEtag={candidateEtag} accountId={accountId} onDirtyChange={setCandidateDirty} onUpdated={setDetail} onOpenDraft={onOpenDraft} /></article>}
  </section>;
}
