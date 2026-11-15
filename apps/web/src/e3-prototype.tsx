import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import ThemeToggle from './components/ThemeToggle';
import './styles/tokens.css';
import './styles/app.css';
import './styles/theme.css';
import './styles/e3-prototype.css';

type View = 'completeness' | 'review' | 'runs' | 'timeline' | 'trace';
type State = 'sample' | 'empty' | 'partial' | 'error';
const views: { id: View; label: string; group: string }[] = [
  { id: 'completeness', label: '画像完整度', group: '企业工作台' },
  { id: 'timeline', label: '申报时间线', group: '企业工作台' },
  { id: 'trace', label: '本人分析轨迹', group: '企业工作台' },
  { id: 'review', label: '审核工作量', group: '审核工作台' },
  { id: 'runs', label: '分析运行趋势', group: '审核工作台' },
];
const states: { id: State; label: string }[] = [{ id: 'sample', label: '正常样例' }, { id: 'empty', label: '暂无数据' }, { id: 'partial', label: '部分 / 未知' }, { id: 'error', label: '读取失败' }];
const series = [
  { day: '11/08', review: 2, completed: 1, runs: 4, success: 3, failure: 1 },
  { day: '11/09', review: 4, completed: 3, runs: 6, success: 5, failure: 1 },
  { day: '11/10', review: 3, completed: 2, runs: 5, success: 4, failure: 1 },
  { day: '11/11', review: 5, completed: 4, runs: 8, success: 6, failure: 2 },
  { day: '11/12', review: 1, completed: 1, runs: 3, success: 3, failure: 0 },
  { day: '11/13', review: 4, completed: 2, runs: 7, success: 6, failure: 1 },
  { day: '11/14', review: 2, completed: 1, runs: 4, success: 3, failure: 1 },
];
function Trend({ view, partial }: { view: 'review' | 'runs'; partial: boolean }) {
  const review = view === 'review';
  const rows = partial ? series.slice(3) : series;
  const success = rows.reduce((sum, row) => sum + row.success, 0);
  const failure = rows.reduce((sum, row) => sum + row.failure, 0);
  const completed = success + failure;
  return <>
    <div className="e3-metrics"><div><span>{review ? '新增草稿' : '运行总量'}</span><strong>{rows.reduce((sum, row) => sum + (review ? row.review : row.runs), 0)}</strong><small>次 · {partial ? '仅已覆盖时段' : '样例期间'}</small></div><div><span>{review ? '完成审核' : '成功运行'}</span><strong>{rows.reduce((sum, row) => sum + (review ? row.completed : row.success), 0)}</strong><small>{review ? '次 · 按审核事件统计' : '次 · 样例期间'}</small></div>{review && <div><span>当日日终待办</span><strong>未结算</strong><small>当日结束后才可确认</small></div>}</div>
    {partial && <p className="e3-notice">部分覆盖：11/08—11/10 未采集；下方展示 11/11 起的样例记录。</p>}
    {review ? <div className="e3-chart" aria-hidden="true">{rows.map(row => <div className="e3-column" key={row.day}><span>{row.review}</span><div className="e3-bar-track"><div className="e3-bar" style={{ height: `${row.review * 11}%` }} /></div><small>{row.day}</small></div>)}</div> : <div className="e3-donut-layout"><div className="e3-donut" role="img" aria-label={`已结束运行 ${completed} 次，成功 ${success} 次，失败 ${failure} 次`} style={{ background: completed ? `conic-gradient(var(--e3-success) 0 ${success / completed * 100}%, var(--e3-failure) ${success / completed * 100}% 100%)` : 'var(--line)' }}><div className="e3-donut-center"><strong>{completed}</strong><span>已结束运行</span></div></div><div className="e3-donut-legend"><div><span className="e3-dot e3-dot-success" aria-hidden="true" /><span>成功</span><strong>{success} 次</strong></div><div><span className="e3-dot e3-dot-failure" aria-hidden="true" /><span>失败</span><strong>{failure} 次</strong></div><p>统计当前展示时段内的成功与失败；未结束运行不计入此环。</p></div></div>}
    <details className="e3-details"><summary>查看逐日数据表</summary><div className="e3-table-wrap"><table><caption>{review ? '审核工作量' : '分析运行'}合成样例，按北京时间统计</caption><thead><tr><th>日期</th><th>{review ? '新增草稿' : '运行总量'}</th><th>{review ? '完成审核' : '成功'}</th><th>{review ? '日终待办' : '失败'}</th></tr></thead><tbody>{rows.map((row, i) => <tr key={row.day}><td>{row.day}</td><td>{review ? row.review : row.runs}</td><td>{review ? row.completed : row.success}</td><td>{review ? i === rows.length - 1 ? '未结算' : '未提供' : row.failure}</td></tr>)}</tbody></table></div></details>
    <p className="e3-footnote">北京时间 · 统计口径及覆盖范围待确认。</p>
  </>;
}
function Completeness({ partial }: { partial: boolean }) {
  return <div className="e3-profile">
    <div className="e3-profile-summary"><span className="e3-kicker">合成企业 A · 2026 年度样例</span><h2>{partial ? '部分项目适用性待确认' : '信息已填写 5 / 6 项'}</h2><p>{partial ? '已知适用项目已填 5 / 6，另有 2 项适用性未知；暂不展示总体百分比。' : '还有 1 项信息待补充，已填写信息待核实。'}</p>{!partial && <div className="e3-progress" role="progressbar" aria-label="已知适用项填写进度" aria-valuemin={0} aria-valuemax={6} aria-valuenow={5}><span /></div>}<small>展示信息填写情况；资格需另行分析。</small></div>
    <div className="e3-list"><h3>信息明细</h3><div><span>企业基础信息<small>地区、行业及成立日期</small></span><strong>已填写</strong></div><div><span>年度经营信息<small>营业收入、应纳税所得额</small></span><strong>已填写 · 未核实</strong></div><div><span>季度从业人数<small>样例期间：2026 年第 1 季度</small></span><strong>待填写</strong></div><div><span>其他适用信息<small>{partial ? '需确认是否适用' : '本样例不适用'}</small></span><strong>{partial ? '适用性未知' : '不适用'}</strong></div></div>
  </div>;
}
function Timeline({ partial }: { partial: boolean }) {
  return <><p className="e3-note">分别查看申报窗口和政策有效期；实际日期以核实后的政策原文为准。</p><ol className="e3-timeline"><li><span className="e3-date">2026.03.01<small>开始</small></span><div><h3>第一批申报窗口</h3><p>网上申报：3 月 1 日至 3 月 31 日</p><small>样例来源：年度申报通知 · 待核实</small></div><span className="e3-tag">待核实</span></li><li><span className="e3-date">2026.06.01<small>开始</small></span><div><h3>第二批申报窗口</h3><p>网上申报：6 月 1 日至 6 月 30 日</p><small>样例来源：年度申报通知 · 待核实</small></div><span className="e3-tag">待核实</span></li><li><span className="e3-date">{partial ? '未知' : '待确认'}<small>政策有效期</small></span><div><h3>政策有效期</h3><p>{partial ? '有效期依据待核实，截止日期未知。' : '政策有效期待核实。'}</p><small>来源和版本待业务复核</small></div><span className="e3-tag">未知</span></li></ol></>;
}
function Trace({ partial }: { partial: boolean }) {
  return <><p className="e3-notice">本人轨迹的可见范围待确认。</p><ol className="e3-trace">{[['信息准备', '整理本次填写的信息', '已完成'], ['政策匹配', '查找并核对适用条件', '已完成'], ['结果说明', partial ? '部分内容不可展示' : '生成结果与待补充项', partial ? '内容受限' : '已完成']].map(([title, description, status], index) => <li key={title}><span className="e3-step">{index + 1}</span><div><h3>{title}</h3><p>{description}</p></div><span className="e3-tag">{status}</span></li>)}</ol></>;
}
function App() {
  const [view, setView] = useState<View>('completeness');
  const [state, setState] = useState<State>('sample');
  const current = views.find(item => item.id === view)!;
  const subtitle: Record<View, string> = { completeness: '了解企业信息的填写情况，找到下一步需要补充的内容。', review: '按日期查看审核工作量，理解数据覆盖范围。', runs: '查看分析运行情况，区分实际为零与尚未采集。', timeline: '把申报窗口和政策有效期分开查看。', trace: '了解本人分析的主要步骤与可查看范围。' };
  function choose(id: View) { setView(id); setState('sample'); }
  return <div className="app-shell e3-shell"><a className="skip-link" href="#e3-main">跳到主要内容</a><header className="site-header"><div className="header-inner"><div className="brand"><span className="brand-mark">惠</span><span><strong>惠策协同</strong><small>企业政策核对工作台</small></span></div><div className="header-actions"><span className="header-caption">E3 交互原型</span><ThemeToggle /></div></div></header>
    <div className="shell-layout e3-layout"><nav className="side-nav e3-nav" aria-label="原型页面"><p className="nav-caption">企业工作台</p>{views.filter(x => x.group === '企业工作台').map(item => <button type="button" key={item.id} aria-current={view === item.id ? 'page' : undefined} onClick={() => choose(item.id)}>{item.label}</button>)}<p className="nav-caption e3-nav-group">审核工作台</p>{views.filter(x => x.group === '审核工作台').map(item => <button type="button" key={item.id} aria-current={view === item.id ? 'page' : undefined} onClick={() => choose(item.id)}>{item.label}</button>)}<p className="nav-foot">界面与状态预览</p></nav>
    <main id="e3-main" className="main-content"><div className="demo-banner"><span className="banner-dot" />交互原型 · 所有数据均为虚构样例</div><div className="page-heading"><div><span className="e3-kicker">{current.group}</span><h1>{current.label}</h1><p>{subtitle[view]}</p></div><span className="phase-tag">样例预览</span></div>
      <section className="e3-state-panel" aria-label="原型状态切换"><div><strong>查看不同状态</strong><p>切换下方选项，检查页面如何解释不同情况。</p></div><div className="e3-state-buttons">{states.map(item => <button type="button" key={item.id} className={state === item.id ? 'e3-selected' : ''} aria-pressed={state === item.id} onClick={() => setState(item.id)}>{item.label}</button>)}</div></section>
      <section className="panel e3-content" aria-live="polite"><div className="e3-section-head"><div><span className="e3-kicker">{view === 'review' || view === 'runs' ? '最近 7 日 · 北京时间' : '页面展示'}</span><h2>{current.label}</h2></div><span className="e3-tag">{states.find(x => x.id === state)?.label}</span></div>
      {state === 'error' ? <div className="e3-empty"><h3>暂时无法读取信息</h3><p>请检查连接后重试。</p><button className="button button-secondary" onClick={() => setState('sample')}>重试（恢复样例）</button></div> : state === 'empty' ? <div className="e3-empty"><h3>{view === 'trace' ? '暂无可展示的轨迹' : view === 'timeline' ? '暂无可展示的时间信息' : view === 'completeness' ? '暂无完整度数据' : '尚无可展示的统计数据'}</h3><p>{view === 'review' || view === 'runs' ? '数据尚未采集或提供。' : '数据尚未提供，请在信息就绪后查看。'}</p></div> : view === 'completeness' ? <Completeness partial={state === 'partial'} /> : view === 'timeline' ? <Timeline partial={state === 'partial'} /> : view === 'trace' ? <Trace partial={state === 'partial'} /> : <Trend view={view} partial={state === 'partial'} />}
      </section><p className="e3-bottom">字段、权限、统计口径和日期来源待确认。</p></main></div></div>;
}
createRoot(document.getElementById('root')!).render(<App />);
