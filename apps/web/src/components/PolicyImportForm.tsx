import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import type { PolicyImportMetadata } from '../api/types';

export default function PolicyImportForm({ busy, onImport, onDirtyChange }: { busy: boolean; onDirtyChange: (dirty: boolean) => void; onImport: (metadata: PolicyImportMetadata, files: File[]) => void }) {
  const [data, setData] = useState({ policyId: '', version: '', title: '', type: 'tax' as 'tax' | 'grant', issuer: '', region: '', documentNumber: '', applicableEntities: '', publishedAt: '', effectiveFrom: '', effectiveTo: '', opensAt: '', closesAt: '', sourceUrl: '', officialEntryUrl: '' });
  const [body, setBody] = useState<File | null>(null);
  const [attachments, setAttachments] = useState<File[]>([]);
  const [error, setError] = useState('');
  const [errorField, setErrorField] = useState('');
  const errorId = useId();
  useLayoutEffect(() => { onDirtyChange(Object.entries(data).some(([key, value]) => key !== 'type' && value !== '') || body !== null || attachments.length > 0 || data.type !== 'tax'); return () => onDirtyChange(false); }, [data, body, attachments, onDirtyChange]);
  const errorSummary = useRef<HTMLParagraphElement>(null);
  const form = useRef<HTMLFormElement>(null);
  function report(message: string, field: string) { setErrorField(field); setError(message); requestAnimationFrame(() => form.current?.querySelector<HTMLElement>(`[data-error-field="${field}"]`)?.focus()); }
  useEffect(() => { if (error) errorSummary.current?.focus(); }, [error]);
  function clearError() { setErrorField(''); setError(''); }
  function errorAttrs(field: string) { return error && errorField === field ? { 'aria-invalid': true, 'aria-describedby': errorId } : {}; }
  function set(key: keyof typeof data, value: string) { clearError(); setData(old => ({ ...old, [key]: value })); }
  function submit(event: React.FormEvent) {
    event.preventDefault();
    const files = [...(body ? [body] : []), ...attachments];
    if (!body || files.length > 5 || files.some(file => file.size > 10 * 1024 * 1024)) { report('请选择正文，最多上传 5 个文件，每个不超过 10 MiB。', !body || (body && body.size > 10 * 1024 * 1024) ? 'body' : 'attachments'); return; }
    if (!data.applicableEntities.split('\n').some(item => item.trim())) { report('至少填写一个适用主体。', 'applicableEntities'); return; }
    if (data.publishedAt > data.effectiveFrom || (data.effectiveTo && data.effectiveTo < data.effectiveFrom)) { report('请核对发布日期、生效日期和失效日期的先后顺序。', data.publishedAt > data.effectiveFrom ? 'effectiveFrom' : 'effectiveTo'); return; }
    if (!!data.opensAt !== !!data.closesAt) { report('申报窗口的开始和结束时间必须一起填写。', data.opensAt ? 'closesAt' : 'opensAt'); return; }
    if (data.opensAt && data.closesAt && data.opensAt >= data.closesAt) { report('申报结束时间必须晚于开始时间。', 'closesAt'); return; }
    setErrorField(''); setError('');
    onImport({ ...(data.policyId ? { policyId: data.policyId } : {}), version: data.version, title: data.title, type: data.type, issuer: data.issuer, region: data.region, documentNumber: data.documentNumber || null, applicableEntities: data.applicableEntities.split('\n').map(x => x.trim()).filter(Boolean), publishedAt: data.publishedAt, effectiveFrom: data.effectiveFrom, effectiveTo: data.effectiveTo || null, applicationWindow: data.opensAt && data.closesAt ? { opensAt: new Date(data.opensAt).toISOString(), closesAt: new Date(data.closesAt).toISOString() } : null, sourceUrl: data.sourceUrl, officialEntryUrl: data.officialEntryUrl }, files);
  }
  return <form ref={form} className="panel" onSubmit={submit}><h2>导入政策草稿</h2><fieldset disabled={busy}><legend>导入信息</legend>{error && <p id={errorId} ref={errorSummary} tabIndex={-1} role="alert">{error}</p>}{([['policyId', '已有政策 ID（新政策留空）', 'text'], ['version', '版本', 'text'], ['title', '标题', 'text'], ['issuer', '发布机关', 'text'], ['region', '地区', 'text'], ['documentNumber', '文号（可选）', 'text'], ['publishedAt', '发布日期', 'date'], ['effectiveFrom', '生效日期', 'date'], ['effectiveTo', '失效日期（可选）', 'date'], ['opensAt', '申报开始（可选）', 'datetime-local'], ['closesAt', '申报结束（可选）', 'datetime-local'], ['sourceUrl', '官方原文网址', 'url'], ['officialEntryUrl', '官方办理网址', 'url']] as const).map(([key, label, type]) => <label key={key}>{label}<input {...errorAttrs(key)} data-error-field={key} type={type} required={['version', 'title', 'issuer', 'region', 'publishedAt', 'effectiveFrom', 'sourceUrl', 'officialEntryUrl'].includes(key)} value={data[key]} onChange={e => set(key, e.target.value)} /></label>)}<label>政策类型<select value={data.type} onChange={e => set('type', e.target.value)}><option value="tax">税收</option><option value="grant">资金</option></select></label><label>适用主体（每行一项）<textarea {...errorAttrs('applicableEntities')} data-error-field="applicableEntities" value={data.applicableEntities} onChange={e => set('applicableEntities', e.target.value)} /></label><label>政策正文（PDF 或 UTF-8 文本）<input {...errorAttrs('body')} data-error-field="body" type="file" required accept=".pdf,.txt,.html,.htm" onChange={e => { clearError(); setBody(e.target.files?.[0] ?? null); }} /></label><label>附件（最多四个）<input {...errorAttrs('attachments')} data-error-field="attachments" type="file" multiple accept=".pdf,.txt,.html,.htm" onChange={e => { clearError(); setAttachments(Array.from(e.target.files ?? [])); }} /></label><button disabled={busy} type="submit">导入草稿</button></fieldset></form>;
}
