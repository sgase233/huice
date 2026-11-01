import { useEffect, useRef, useState } from 'react';
import type { EnterpriseInput, FactInput, FactKey } from '../api/types';
import { factLabels, factUnits } from '../demo';

const keys = Object.keys(factLabels) as FactKey[];
const decimal = /^(?:0|[1-9]\d*)(?:\.\d{1,6})?$/;
const signedDecimal = /^-?(?:0|[1-9]\d*)(?:\.\d{1,6})?$/;
const validDate = (value: string) => /^\d{4}-\d{2}-\d{2}$/.test(value) && !Number.isNaN(Date.parse(`${value}T00:00:00Z`)) && new Date(`${value}T00:00:00Z`).toISOString().slice(0, 10) === value;

export function validateEnterprise(input: EnterpriseInput): string[] {
  const errors: string[] = [];
  if (!input.nameSynthetic.trim()) errors.push('请填写合成企业名称。');
  if (!validDate(input.period.startDate) || !validDate(input.period.endDate) || input.period.startDate > input.period.endDate) errors.push('画像期间日期无效或开始日期晚于结束日期。');
  const unique = new Set<string>();
  for (const fact of input.facts) {
    const identity = `${fact.key}/${fact.period.startDate}/${fact.period.endDate}`;
    if (unique.has(identity)) errors.push(`${factLabels[fact.key]}的期间重复。`);
    unique.add(identity);
    if (!validDate(fact.period.startDate) || !validDate(fact.period.endDate) || fact.period.startDate > fact.period.endDate) errors.push(`${factLabels[fact.key]}的期间无效。`);
    if (fact.unit !== factUnits[fact.key]) errors.push(`${factLabels[fact.key]}的单位不正确。`);
    if (['region', 'industry', 'establishedDate', 'specificMarketSince'].includes(fact.key) && (fact.period.startDate !== input.period.startDate || fact.period.endDate !== input.period.endDate)) errors.push(`${factLabels[fact.key]}的期间必须与画像期间一致。`);
    if (fact.value === null) continue;
    if (fact.unit === 'boolean') {
      if (typeof fact.value !== 'boolean') errors.push(`${factLabels[fact.key]}必须为“是”“否”或未知。`);
    } else if (typeof fact.value !== 'string' || !fact.value.trim()) {
      errors.push(`${factLabels[fact.key]}不能是空字符串。`);
    } else if (fact.unit === 'CNY' || fact.unit === 'person' || fact.unit === 'ratio') {
      const signed = fact.key === 'taxableIncome' || fact.key === 'rdExpenseIncrease';
      if (!(signed ? signedDecimal : decimal).test(fact.value) || (fact.unit === 'CNY' && fact.value.split('.')[1]?.length > 2)) errors.push(`${factLabels[fact.key]}需要${signed ? '可为负的' : '非负'}十进制字符串；金额最多两位小数。`);
    } else if (fact.unit === 'date' && !validDate(fact.value)) {
      errors.push(`${factLabels[fact.key]}应为有效的 YYYY-MM-DD 日期。`);
    }
  }
  for (const key of ['region', 'industry'] as const) {
    if (!input.facts.some(fact => fact.key === key && fact.value !== null && fact.value !== '' && fact.period.startDate === input.period.startDate && fact.period.endDate === input.period.endDate)) errors.push(`请填写画像期间的${factLabels[key]}。`);
  }
  return errors;
}

type Props = { value: EnterpriseInput; onSave: (value: EnterpriseInput) => void | Promise<void>; real?: boolean };

export default function EnterprisePage({ value, onSave, real = false }: Props) {
  const [draft, setDraft] = useState<EnterpriseInput>(() => structuredClone(value));
  const [errors, setErrors] = useState<string[]>([]);
  const [saved, setSaved] = useState(false);
  const [pending, setPending] = useState(false);
  const errorSummary = useRef<HTMLDivElement>(null);
  useEffect(() => { if (errors.length) errorSummary.current?.focus(); }, [errors]);
  const factErrors = (fact: FactInput) => errors.filter(error => error.includes(factLabels[fact.key]) && !keys.some(key => key !== fact.key && factLabels[key].length > factLabels[fact.key].length && error.includes(factLabels[key])));
  const errorTarget = (error: string) => {
    if (error.includes('名称')) return 'enterprise-name';
    const index = draft.facts.findIndex(fact => factErrors(fact).includes(error));
    if (index >= 0) return error.includes('期间') && !error.startsWith('请填写') ? `fact-start-${index}` : draft.facts[index].value === null ? `fact-unknown-${index}` : `fact-value-${index}`;
    if (error.includes('画像期间')) return 'profile-start';
    return undefined;
  };

  function editFact(index: number, patch: Partial<FactInput>) {
    setSaved(false);
    setDraft(current => ({ ...current, facts: current.facts.map((fact, i) => i === index ? { ...fact, ...patch } : fact) }));
  }

  function addFact() {
    const key = keys.find(candidate => !draft.facts.some(fact => fact.key === candidate)) ?? 'rdExpense';
    setDraft(current => ({ ...current, facts: [...current.facts, { key, value: null, unit: factUnits[key], period: { ...current.period }, source: 'user_input' }] }));
    setSaved(false);
  }

  return <section className="page-block" aria-labelledby="enterprise-title">
    <div className="page-heading"><div><span className="eyebrow">01 / 企业画像</span><h1 id="enterprise-title">先把企业事实说清楚</h1><p>{real ? '填写合成企业事实，保存会创建一份新画像。' : '填写合成样本，用来检查页面与数据结构。保存只作用于当前浏览器会话。'}</p></div><span className="phase-tag">{real ? '真实接口' : '输入 · 静态演示'}</span></div>
    <form className="panel form-panel" onSubmit={async event => {
      event.preventDefault();
      const findings = validateEnterprise(draft);
      setErrors(findings);
      if (!findings.length && !pending) {
        setPending(true); setSaved(false);
        try { await onSave(structuredClone(draft)); setSaved(true); }
        catch (error) { setErrors([error instanceof Error ? error.message : '保存失败。']); }
        finally { setPending(false); }
      }
    }}>
      {errors.length > 0 && <div className="form-error" role="alert" tabIndex={-1} ref={errorSummary}><strong>请检查以下输入：</strong><ul>{errors.map((error, i) => <li key={i}>{errorTarget(error) ? <a href={`#${errorTarget(error)}`} onClick={event => { event.preventDefault(); document.getElementById(errorTarget(error)!)?.focus(); }}>{error}</a> : error}</li>)}</ul></div>}
      <div inert={pending}>
      <div className="form-grid">
        <label>合成企业名称<input id="enterprise-name" aria-invalid={errors.some(e => e.includes('名称')) || undefined} aria-describedby={errors.some(e => e.includes('名称')) ? 'name-error' : undefined} value={draft.nameSynthetic} onChange={event => { setSaved(false); setDraft({ ...draft, nameSynthetic: event.target.value }); }} />{errors.some(e => e.includes('名称')) && <small id="name-error">请填写合成企业名称。</small>}</label>
        <label>画像开始日期<input id="profile-start" type="date" aria-describedby={errors.some(e => e.startsWith('画像期间日期')) ? 'period-error' : undefined} value={draft.period.startDate} onChange={event => { setSaved(false); setDraft({ ...draft, period: { ...draft.period, startDate: event.target.value } }); }} />{errors.some(e => e.startsWith('画像期间日期')) && <small id="period-error">画像期间日期无效或开始日期晚于结束日期。</small>}</label>
        <label>画像结束日期<input type="date" value={draft.period.endDate} onChange={event => { setSaved(false); setDraft({ ...draft, period: { ...draft.period, endDate: event.target.value } }); }} /></label>
      </div>
      <h2>事实与来源</h2>
      <p className="muted">“未知”与数值 0 不同。事实期间可逐条指定；来源不会被前端标记为已核实。</p>
      <div className="facts-list">
        {draft.facts.map((fact, index) => <fieldset className="fact-card" key={index} aria-describedby={factErrors(fact).length ? `fact-error-${index}` : undefined}>
          <legend>事实 {index + 1}</legend>
          <div className="form-grid">
            <label>字段<select id={`fact-field-${index}`} value={fact.key} onChange={event => {
              const key = event.target.value as FactKey;
              editFact(index, { key, unit: factUnits[key], value: null });
            }}>{keys.map(key => <option key={key} value={key}>{factLabels[key]}</option>)}</select></label>
            <label>值{fact.value === null ? <input value="未知" disabled aria-label={`${factLabels[fact.key]}未知`} /> : fact.unit === 'boolean' ?
              <select id={`fact-value-${index}`} aria-describedby={factErrors(fact).length ? `fact-error-${index}` : undefined} value={String(fact.value)} onChange={event => editFact(index, { value: event.target.value === 'true' })}><option value="true">是</option><option value="false">否</option></select> :
              <input id={`fact-value-${index}`} aria-describedby={factErrors(fact).length ? `fact-error-${index}` : undefined} aria-invalid={factErrors(fact).length > 0 || undefined} type={fact.unit === 'date' ? 'date' : 'text'} inputMode={fact.unit === 'CNY' || fact.unit === 'person' || fact.unit === 'ratio' ? 'decimal' : undefined} value={String(fact.value)} onChange={event => editFact(index, { value: event.target.value })} />}{fact.key === 'rdExpenseRevenueRatio' && <small>例如 3% 填 0.03。</small>}{fact.key === 'rdExpenseIncrease' && <small>投入下降时填写负值。</small>}{fact.key === 'creditRecord' && <small>“是”表示诚信记录良好。</small>}</label>
            <label>单位<input value={fact.unit} disabled /></label>
            <label>期间开始<input id={`fact-start-${index}`} aria-describedby={factErrors(fact).length ? `fact-error-${index}` : undefined} type="date" value={fact.period.startDate} onChange={event => editFact(index, { period: { ...fact.period, startDate: event.target.value } })} /></label>
            <label>期间结束<input type="date" value={fact.period.endDate} onChange={event => editFact(index, { period: { ...fact.period, endDate: event.target.value } })} /></label>
            <label>来源<select value={fact.source} onChange={event => editFact(index, { source: event.target.value as FactInput['source'] })}><option value="user_input">用户输入</option><option value="synthetic_material">合成材料</option></select></label>
          </div>
          {factErrors(fact).length > 0 && <p id={`fact-error-${index}`} className="form-error">{factErrors(fact).join(' ')}</p>}
          <div className="fact-actions"><label className="inline-check"><input id={`fact-unknown-${index}`} aria-describedby={factErrors(fact).length ? `fact-error-${index}` : undefined} type="checkbox" checked={fact.value === null} onChange={event => editFact(index, { value: event.target.checked ? null : fact.unit === 'boolean' ? false : fact.unit === 'date' ? draft.period.startDate : '' })} />未知</label>
            {fact.key !== 'region' && fact.key !== 'industry' && <button type="button" className="text-button" onClick={() => setDraft(current => ({ ...current, facts: current.facts.filter((_, i) => i !== index) }))}>移除此事实</button>}</div>
        </fieldset>)}
      </div>
      <div className="form-actions"><button type="button" className="button button-secondary" onClick={addFact}>添加事实</button><button className="button" type="submit" disabled={pending}>{pending ? '保存中…' : '保存当前样本'}</button></div>
      </div>
      {saved && <p className="success-message" role="status">已保存当前合成样本。机会列表已恢复为“尚未分析”。</p>}
    </form>
  </section>;
}
