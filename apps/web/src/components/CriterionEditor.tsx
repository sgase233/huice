import { useRef } from 'react';
import type { CriterionDraft, SourceDocument, SourceSpan } from '../api/types';
import ExpressionEditor from './ExpressionEditor';

async function hash(text: string) {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
}
export default function CriterionEditor({ value, sources, version, onChange, invalidField, errorId }: { value: CriterionDraft; sources: SourceDocument[]; version: string; onChange: (value: CriterionDraft) => void; invalidField: string | null; errorId: string }) {
  const errorAttrs = (field: string) => invalidField === field ? { 'aria-invalid': true, 'aria-describedby': errorId } : {};
  const addSpan = useRef<HTMLButtonElement>(null);
  const lastSpan = useRef<HTMLFieldSetElement>(null);
  function spanChange(index: number, span: SourceSpan) { onChange({ ...value, sourceSpans: value.sourceSpans.map((s, i) => i === index ? span : s) }); }
  return <fieldset><legend>审核条件</legend><label>标题<input {...errorAttrs('title')} data-error-field="title" value={value.title} onChange={e => onChange({ ...value, title: e.target.value })} /></label><label>审核发现<select value={value.reviewFinding} onChange={e => onChange({ ...value, reviewFinding: e.target.value as CriterionDraft['reviewFinding'] })}><option value="none">无冲突</option><option value="conflict">来源冲突</option></select></label><ExpressionEditor invalidField={invalidField} errorId={errorId} value={value.expression} onChange={expression => onChange({ ...value, expression })} />
    {value.sourceSpans.map((span, index) => <fieldset key={index} ref={index === value.sourceSpans.length - 1 ? lastSpan : undefined}><legend>引用 {index + 1}</legend><label>来源<select {...errorAttrs(`span-${index}-source`)} data-error-field={`span-${index}-source`} value={span.sourceId} onChange={e => { const source = sources.find(item => item.id === e.target.value); if (source) spanChange(index, { ...span, sourceId: source.id, sourceUrl: source.sourceUrl, sourceVersion: version }); }}>{!sources.some(source => source.id === span.sourceId) && <option value={span.sourceId}>来源不可用：{span.sourceId}</option>}{sources.map(source => <option key={source.id} value={source.id}>{source.title}</option>)}</select></label><label>页码<input {...errorAttrs(`span-${index}-page`)} data-error-field={`span-${index}-page`} type="number" min="1" value={span.page ?? ''} onChange={e => spanChange(index, { ...span, page: e.target.value ? Number(e.target.value) : null })} /></label><label>章节<input {...errorAttrs(`span-${index}-section`)} data-error-field={`span-${index}-section`} value={span.section ?? ''} onChange={e => spanChange(index, { ...span, section: e.target.value || null })} /></label><label>引文<textarea {...errorAttrs(`span-${index}-quote`)} data-error-field={`span-${index}-quote`} value={span.quote} onChange={e => spanChange(index, { ...span, quote: e.target.value, textHash: '' })} /></label><button type="button" onClick={() => { if (window.confirm('删除此引用？')) { onChange({ ...value, sourceSpans: value.sourceSpans.filter((_, i) => i !== index) }); requestAnimationFrame(() => addSpan.current?.focus()); } }}>删除引用</button></fieldset>)}
    <button ref={addSpan} type="button" disabled={sources.length === 0} onClick={() => { const source = sources[0]; onChange({ ...value, sourceSpans: [...value.sourceSpans, { sourceId: source.id, sourceUrl: source.sourceUrl, sourceVersion: version, page: null, section: null, quote: '', textHash: '' }] }); requestAnimationFrame(() => lastSpan.current?.querySelector('select')?.focus()); }}>添加引用</button>
  </fieldset>;
}
export async function withQuoteHashes(criteria: CriterionDraft[]): Promise<CriterionDraft[]> {
  return Promise.all(criteria.map(async criterion => ({ ...criterion, sourceSpans: await Promise.all(criterion.sourceSpans.map(async span => ({ ...span, textHash: await hash(span.quote) }))) })));
}
