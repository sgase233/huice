import { useEffect, useRef, useState } from 'react';
import type { Enterprise, FactInput, FactKey } from '../api/types';
import { factLabels, factUnitLabels, factUnits } from '../demo';
import { validateEnterprise } from '../pages/EnterprisePage';

type Props = { enterprise: Enterprise; canSave: boolean; onSave: (fact: FactInput) => Promise<void>; onDelete: (fact: FactInput) => Promise<void>; onReload: () => Promise<Enterprise>; onDirtyChange: (dirty: boolean) => void };
type Row = { id: number; fact: FactInput; saved: boolean; dirty: boolean; ready: boolean };
const keys = Object.keys(factLabels) as FactKey[];
const trash = <svg viewBox="0 0 448 512" className="fact-delete-icon" aria-hidden="true"><path d="M135.2 17.7L128 32H32C14.3 32 0 46.3 0 64S14.3 96 32 96H416c17.7 0 32-14.3 32-32s-14.3-32-32-32H320l-7.2-14.3C307.4 6.8 296.3 0 284.2 0H163.8c-12.1 0-23.2 6.8-28.6 17.7zM416 128H32L53.2 467c1.6 25.3 22.6 45 47.9 45H346.9c25.3 0 46.3-19.7 47.9-45L416 128z" /></svg>;

export default function EnterpriseFacts({ enterprise, canSave, onSave, onDelete, onReload, onDirtyChange }: Props) {
  const nextId = useRef(0);
  const makeRows = (profile: Enterprise): Row[] => profile.facts.map(item => ({ id: nextId.current++, fact: { key: item.key, value: item.value, unit: item.unit, period: { ...item.period }, source: item.source }, saved: true, dirty: false, ready: true }));
  const [rows, setRows] = useState<Row[]>(() => makeRows(enterprise));
  const [pending, setPending] = useState(false);
  const [blocked, setBlocked] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [entering, setEntering] = useState<number | null>(null);
  const [removing, setRemoving] = useState<number | null>(null);
  const addButton = useRef<HTMLButtonElement>(null);
  const timer = useRef<number | undefined>(undefined);
  const dirty = rows.some(row => row.dirty || !row.saved);
  useEffect(() => { onDirtyChange(dirty || pending); return () => onDirtyChange(false); }, [dirty, pending, onDirtyChange]);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  useEffect(() => { setRows(makeRows(enterprise)); setBlocked(false); setError(''); }, [enterprise.id]);
  const errors = validateEnterprise({ nameSynthetic: enterprise.nameSynthetic, period: enterprise.period, facts: rows.map(row => row.fact) });
  useEffect(() => {
    const row = rows.find(item => item.dirty && (item.saved || item.ready));
    if (!row || pending || blocked || !canSave || errors.length || removing !== null) return;
    const timeout = window.setTimeout(() => {
      setPending(true); setError(''); setNotice('正在保存…');
      void onSave(row.fact).then(() => {
        setRows(current => current.map(item => item.id === row.id ? { ...item, saved: true, dirty: false } : item));
        setNotice('信息已保存，原有分析结果需要重新核对。');
      }).catch(() => { setBlocked(true); setError('保存未确认，请重新读取画像并核对输入。'); setNotice(''); }).finally(() => setPending(false));
    }, 800);
    return () => window.clearTimeout(timeout);
  }, [rows, pending, blocked, canSave, removing]);
  function edit(id: number, patch: Partial<FactInput>) {
    setRows(current => current.map(row => row.id === id ? { ...row, fact: { ...row.fact, ...patch }, dirty: true } : row));
    setNotice('');
  }
  function add() {
    const key = keys.find(candidate => !rows.some(row => row.fact.key === candidate)) ?? 'rdExpense';
    const id = nextId.current++;
    setRows(current => [...current, { id, fact: { key, value: null, unit: factUnits[key], period: { ...enterprise.period }, source: 'user_input' }, saved: false, dirty: false, ready: false }]);
    setEntering(id);
  }
  async function remove(row: Row) {
    if (pending || removing !== null) return;
    setPending(true); setError(''); setNotice('');
    try {
      if (row.saved) await onDelete(row.fact);
      setEntering(null); setRemoving(row.id); addButton.current?.focus();
      const finish = () => { setRows(current => current.filter(item => item.id !== row.id)); setRemoving(null); setPending(false); requestAnimationFrame(() => addButton.current?.focus()); };
      if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) finish(); else timer.current = window.setTimeout(finish, 400);
    } catch { setPending(false); setBlocked(true); setError('删除未确认，请重新读取画像并核对。'); }
  }
  async function reload() {
    if (dirty && !window.confirm('重新读取会丢弃尚未保存的输入，确定继续吗？')) return;
    setPending(true); setError('');
    try { const current = await onReload(); setRows(makeRows(current)); setBlocked(false); setNotice('已重新读取企业画像。'); }
    catch { setError('读取失败，请稍后重试。'); }
    finally { setPending(false); }
  }
  useEffect(() => { if (!dirty && !pending && !removing) setRows(makeRows(enterprise)); }, [enterprise]);
  return <section className="page-block" aria-labelledby="enterprise-title"><div className="page-heading"><div><span className="eyebrow">01 / 企业画像</span><h1 id="enterprise-title">先把企业情况说清楚</h1><p>已保存合成企业：{enterprise.nameSynthetic}</p></div><span className="phase-tag">示例数据 · 连接服务</span></div>
    <div className="panel form-panel"><div className="form-grid"><label>示例企业名称<input value={enterprise.nameSynthetic} disabled /></label><label>画像开始日期<input type="date" value={enterprise.period.startDate} disabled /></label><label>画像结束日期<input type="date" value={enterprise.period.endDate} disabled /></label></div>
      <h2>企业信息与来源</h2><p className="muted">修改后自动保存；新增信息在离开卡片后保存。不清楚时请选择“未知”。已保存信息的类型和期间不能直接更改，可删除后添加。</p>
      {!canSave && !pending && <p className="notice warning">画像版本不可用，请重新读取后核对输入。</p>}{error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}{errors.length > 0 && dirty && <div className="form-error" role="alert">{errors.map((item, i) => <p key={i}>{item}</p>)}</div>}
      <div className="facts-list">{rows.map((row, index) => { const fact = row.fact; const required = (fact.key === 'region' || fact.key === 'industry') && fact.period.startDate === enterprise.period.startDate && fact.period.endDate === enterprise.period.endDate; return <div className={`fact-slot${entering === row.id ? ' is-entering' : ''}${removing === row.id ? ' is-removing' : ''}`} key={row.id} onAnimationEnd={event => { if (event.target === event.currentTarget && entering === row.id) setEntering(null); }}><div><fieldset className="fact-card" disabled={pending || blocked} inert={removing === row.id} onFocusCapture={() => { if (!row.saved && row.ready) setRows(current => current.map(item => item.id === row.id ? { ...item, ready: false } : item)); }} onBlurCapture={event => { if (!row.saved && !event.currentTarget.contains(event.relatedTarget as Node | null)) setRows(current => current.map(item => item.id === row.id ? { ...item, ready: true, dirty: true } : item)); }}><legend>信息 {index + 1}</legend><div className="form-grid">
        <label>信息类型<select value={fact.key} disabled={row.saved} onChange={event => { const key = event.target.value as FactKey; edit(row.id, { key, unit: factUnits[key], value: null }); }}>{keys.map(key => <option value={key} key={key}>{factLabels[key]}</option>)}</select></label>
        <label>值{fact.value === null ? <input value="未知" disabled aria-label={`${factLabels[fact.key]}未知`} /> : fact.unit === 'boolean' ? <select value={String(fact.value)} onChange={event => edit(row.id, { value: event.target.value === 'true' })}><option value="true">是</option><option value="false">否</option></select> : <input type={fact.unit === 'date' ? 'date' : 'text'} inputMode={['CNY', 'person', 'ratio'].includes(fact.unit) ? 'decimal' : undefined} value={String(fact.value)} onChange={event => edit(row.id, { value: event.target.value })} />}</label>
        <label>单位或格式<input value={factUnitLabels[fact.unit]} disabled /></label><label>期间开始<input type="date" value={fact.period.startDate} disabled={row.saved} onChange={event => edit(row.id, { period: { ...fact.period, startDate: event.target.value } })} /></label><label>期间结束<input type="date" value={fact.period.endDate} disabled={row.saved} onChange={event => edit(row.id, { period: { ...fact.period, endDate: event.target.value } })} /></label><label>来源<select value={fact.source} onChange={event => edit(row.id, { source: event.target.value as FactInput['source'] })}><option value="user_input">用户输入</option><option value="synthetic_material">合成材料</option></select></label></div>
        <div className="fact-actions"><label className="inline-check"><input type="checkbox" checked={fact.value === null} onChange={event => edit(row.id, { value: event.target.checked ? null : fact.unit === 'boolean' ? false : fact.unit === 'date' ? fact.period.startDate : '' })} />未知</label>{!required && <button type="button" className="text-button fact-delete" aria-label="移除此项信息" onClick={() => void remove(row)}>{trash}<span className="fact-delete-label" aria-hidden="true">删除</span></button>}</div>
      </fieldset></div></div>; })}</div>
      <div className="form-actions"><button ref={addButton} type="button" className="button button-secondary" disabled={pending || blocked} onClick={add}>添加一项信息</button>{(blocked || !canSave) && <button type="button" className="button button-secondary" disabled={pending} onClick={() => void reload()}>重新读取画像</button>}</div>
    </div></section>;
}
