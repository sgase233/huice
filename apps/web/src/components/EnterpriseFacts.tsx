import { useEffect, useState } from 'react';
import type { Enterprise, FactInput, FactKey } from '../api/types';
import { factLabels, factUnitLabels, factUnits } from '../demo';
import { validateEnterprise } from '../pages/EnterprisePage';

type Props = { enterprise: Enterprise; canSave: boolean; onSave: (fact: FactInput) => Promise<void>; onReload: () => Promise<void>; onDirtyChange: (dirty: boolean) => void };

export default function EnterpriseFacts({ enterprise, canSave, onSave, onReload, onDirtyChange }: Props) {
  const [fact, setFact] = useState<FactInput | null>(null);
  const [selected, setSelected] = useState<number | 'new' | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [dirty, setDirty] = useState(false);
  useEffect(() => { onDirtyChange(dirty); return () => onDirtyChange(false); }, [dirty, onDirtyChange]);
  function choose(index: number | 'new') {
    if (dirty && !window.confirm('当前信息尚未保存，确定切换吗？')) return;
    const item = index === 'new' ? null : enterprise.facts[index];
    const key = (item?.key ?? Object.keys(factLabels).find(key => !enterprise.facts.some(f => f.key === key)) ?? 'rdExpense') as FactKey;
    setFact(item ? { key: item.key, value: item.value, unit: item.unit, period: { ...item.period }, source: item.source } : { key, value: null, unit: factUnits[key], period: { ...enterprise.period }, source: 'user_input' });
    setSelected(index); setDirty(false); setError(''); setNotice('');
  }
  async function submit(event: React.FormEvent) {
    event.preventDefault(); if (!fact) return;
    const facts = enterprise.facts.filter(item => !(item.key === fact.key && item.period.startDate === fact.period.startDate && item.period.endDate === fact.period.endDate));
    const errors = validateEnterprise({ nameSynthetic: enterprise.nameSynthetic, period: enterprise.period, facts: [...facts, fact] });
    if (errors.length) { setError(errors.join(' ')); return; }
    setPending(true); setError(''); setNotice('');
    try { await onSave(fact); setDirty(false); setSelected(null); setFact(null); setNotice('该项信息已保存，原有分析结果需要重新核对。'); }
    catch (e) { setError(e instanceof Error ? e.message : '保存失败。'); }
    finally { setPending(false); }
  }
  function editor() {
    if (!fact) return null;
    return <form onSubmit={event => void submit(event)} onChange={() => { setDirty(true); setNotice(''); }}>
      <div className="form-grid">
        <label>信息类型<select value={fact.key} disabled={selected !== 'new'} onChange={e => { const key = e.target.value as FactKey; setFact({ ...fact, key, unit: factUnits[key], value: null }); }}>{(Object.keys(factLabels) as FactKey[]).map(key => <option key={key} value={key}>{factLabels[key]}</option>)}</select></label>
        <label>值{fact.value === null ? <input value="未知" disabled /> : fact.unit === 'boolean' ? <select value={String(fact.value)} onChange={e => setFact({ ...fact, value: e.target.value === 'true' })}><option value="true">是</option><option value="false">否</option></select> : <input type={fact.unit === 'date' ? 'date' : 'text'} value={String(fact.value)} onChange={e => setFact({ ...fact, value: e.target.value })} />}</label>
        <label>单位或格式<input value={factUnitLabels[fact.unit]} disabled /></label>
        <label>期间开始<input type="date" value={fact.period.startDate} onChange={e => setFact({ ...fact, period: { ...fact.period, startDate: e.target.value } })} /></label>
        <label>期间结束<input type="date" value={fact.period.endDate} onChange={e => setFact({ ...fact, period: { ...fact.period, endDate: e.target.value } })} /></label>
        <label>来源<select value={fact.source} onChange={e => setFact({ ...fact, source: e.target.value as FactInput['source'] })}><option value="user_input">用户输入</option><option value="synthetic_material">合成材料</option></select></label>
      </div>
      <div className="fact-actions"><label className="inline-check"><input type="checkbox" checked={fact.value === null} onChange={e => setFact({ ...fact, value: e.target.checked ? null : fact.unit === 'boolean' ? false : fact.unit === 'date' ? fact.period.startDate : '' })} />未知</label><button className="button" type="submit" disabled={pending || !canSave}>保存该项</button></div>
    </form>;
  }
  return <section className="page-block" aria-labelledby="enterprise-title"><div className="page-heading"><div><span className="eyebrow">01 / 企业画像</span><h1 id="enterprise-title">先把企业情况说清楚</h1><p>已保存合成企业：{enterprise.nameSynthetic}</p></div><span className="phase-tag">示例数据 · 连接服务</span></div>
    <div className="panel form-panel"><div className="form-grid"><label>示例企业名称<input value={enterprise.nameSynthetic} disabled /></label><label>画像开始日期<input type="date" value={enterprise.period.startDate} disabled /></label><label>画像结束日期<input type="date" value={enterprise.period.endDate} disabled /></label></div>
      <p className="muted">企业名称和画像期间在注册时确定；下方可更新企业事实。</p><details><summary>查看企业信息的版本详情</summary><p>信息版本：{enterprise.factVersion}</p></details><h2>企业信息与来源</h2><p className="muted">不清楚时请选择“未知”（区别于 0）。每项信息可填写所属期间和来源。</p>
      {!canSave && <p className="notice warning">画像版本不可用或已冲突，请重新读取后核对输入。</p>}
      {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
      <div className="facts-list">{enterprise.facts.map((item, index) => <div className="fact-slot" key={`${item.key}/${item.period.startDate}/${item.period.endDate}`}><div><fieldset className="fact-card"><legend>信息 {index + 1}</legend>{selected === index && fact ? editor() : <><div className="form-grid"><label>信息类型<input value={factLabels[item.key]} disabled /></label><label>值<input value={item.value === null ? '未知' : typeof item.value === 'boolean' ? item.value ? '是' : '否' : item.value} disabled /></label><label>单位或格式<input value={factUnitLabels[item.unit]} disabled /></label><label>期间开始<input type="date" value={item.period.startDate} disabled /></label><label>期间结束<input type="date" value={item.period.endDate} disabled /></label><label>来源<input value={item.source === 'user_input' ? '用户输入' : '合成材料'} disabled /></label></div><button type="button" className="button button-secondary" disabled={pending} onClick={() => choose(index)}>编辑{factLabels[item.key]}</button></>}</fieldset></div></div>)}
      {selected === 'new' && <div className="fact-slot"><div><fieldset className="fact-card"><legend>新信息</legend>{editor()}</fieldset></div></div>}</div>
      <p className="muted">每次保存一项；相同类型和期间会更新原项，更改期间会新增一项，不会删除原项。</p><div className="form-actions"><button type="button" className="button button-secondary" disabled={pending} onClick={() => choose('new')}>添加一项信息</button><button type="button" className="button button-secondary" disabled={pending} onClick={async () => { setPending(true); setError(''); try { await onReload(); setNotice('已读取最新画像，请核对输入后保存。'); } catch (e) { setError(e instanceof Error ? e.message : '读取失败。'); } finally { setPending(false); } }}>重新读取画像</button></div>
    </div></section>;
}
