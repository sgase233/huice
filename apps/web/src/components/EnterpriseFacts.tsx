import { useEffect, useState } from 'react';
import type { Enterprise, FactInput, FactKey } from '../api/types';
import { factLabels, factUnitLabels, factUnits } from '../demo';
import { validateEnterprise } from '../pages/EnterprisePage';

type Props = {
  enterprise: Enterprise;
  canSave: boolean;
  onSave: (fact: FactInput) => Promise<void>;
  onReload: () => Promise<void>;
  onDirtyChange: (dirty: boolean) => void;
};

export default function EnterpriseFacts({ enterprise, canSave, onSave, onReload, onDirtyChange }: Props) {
  const [fact, setFact] = useState<FactInput>(() => ({ key: 'region', value: null, unit: factUnits.region, period: { ...enterprise.period }, source: 'user_input' }));
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [dirty, setDirty] = useState(false);
  useEffect(() => { onDirtyChange(dirty); return () => onDirtyChange(false); }, [dirty, onDirtyChange]);
  const choose = (index: string) => {
    if (index === '') return;
    if (dirty && !window.confirm('当前事实尚未保存，确定切换吗？')) return;
    const selected = enterprise.facts[Number(index)];
    setFact({ key: selected.key, value: selected.value, unit: selected.unit, period: { ...selected.period }, source: 'user_input' });
    setError(''); setNotice(''); setDirty(false);
  };
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const facts = enterprise.facts.filter(item => !(item.key === fact.key && item.period.startDate === fact.period.startDate && item.period.endDate === fact.period.endDate));
    const errors = validateEnterprise({ nameSynthetic: enterprise.nameSynthetic, period: enterprise.period, facts: [...facts, fact] });
    if (errors.length) { setError(errors.join(' ')); return; }
    setPending(true); setError(''); setNotice('');
    try { await onSave(fact); setDirty(false); setNotice('该项信息已保存，原有分析结果需要重新核对。'); }
    catch (e) { setError(e instanceof Error ? e.message : '保存失败。'); }
    finally { setPending(false); }
  }
  return <section><h2>企业事实</h2>
    <ul>{enterprise.facts.map((item, index) => <li key={`${item.key}/${item.period.startDate}/${item.period.endDate}`}>
      {factLabels[item.key]}：{item.value === null ? '未知' : typeof item.value === 'boolean' ? (item.value ? '是' : '否') : item.value} {factUnitLabels[item.unit]}（{item.period.startDate} 至 {item.period.endDate}，{item.verified ? '已核实' : '未核实'}）
      <button type="button" disabled={pending} onClick={() => choose(String(index))}>编辑{factLabels[item.key]}</button>
    </li>)}</ul>
    <p>每次保存一项；相同类型和期间会更新原项，更改期间会新增一项，不会删除原项。</p>
    {!canSave && <p className="notice warning">画像版本不可用或已冲突，请重新读取后核对输入。</p>}
    <button type="button" disabled={pending} onClick={async () => { setPending(true); setError(''); try { await onReload(); setNotice('已读取最新画像，请核对下方输入后保存。'); } catch (e) { setError(e instanceof Error ? e.message : '读取失败。'); } finally { setPending(false); } }}>重新读取画像</button>
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    <form onSubmit={event => void submit(event)} onChange={() => { setDirty(true); setNotice(''); }} className="form-grid">
      <label>信息类型<select value={fact.key} onChange={e => { const key = e.target.value as FactKey; setFact({ ...fact, key, unit: factUnits[key], value: null }); }}>{(Object.keys(factLabels) as FactKey[]).map(key => <option key={key} value={key}>{factLabels[key]}</option>)}</select></label>
      <label>值{fact.value === null ? <input value="未知" disabled /> : fact.unit === 'boolean' ? <select value={String(fact.value)} onChange={e => setFact({ ...fact, value: e.target.value === 'true' })}><option value="true">是</option><option value="false">否</option></select> : <input type={fact.unit === 'date' ? 'date' : 'text'} value={String(fact.value)} onChange={e => setFact({ ...fact, value: e.target.value })} />}</label>
      <label className="inline-check"><input type="checkbox" checked={fact.value === null} onChange={e => setFact({ ...fact, value: e.target.checked ? null : fact.unit === 'boolean' ? false : fact.unit === 'date' ? fact.period.startDate : '' })} />未知</label>
      <label>期间开始<input type="date" value={fact.period.startDate} onChange={e => setFact({ ...fact, period: { ...fact.period, startDate: e.target.value } })} /></label>
      <label>期间结束<input type="date" value={fact.period.endDate} onChange={e => setFact({ ...fact, period: { ...fact.period, endDate: e.target.value } })} /></label>
      <button type="submit" disabled={pending || !canSave}>保存该项</button>
    </form>
  </section>;
}
