import PreparationList from '../components/PreparationList';
import type { DemoPolicy } from '../demo';
import { demoPolicies, taxNotice } from '../demo';

export default function ReportPage({ policy }: { policy: DemoPolicy | null }) {
  if (!policy) return <div className="panel empty-state"><h1>暂无报告示例</h1><p>先从机会列表选择一项固定合成场景。此页面不创建真实判断报告。</p><a className="button" href="#opportunities">前往机会列表</a></div>;
  return <section className="page-block" aria-labelledby="report-title"><div className="page-heading"><div><span className="eyebrow">04 / 清单与报告</span><h1 id="report-title">准备下一步核对材料</h1><p>所见为固定合成报告预览。真实报告需在后端分析接口交付后生成。</p></div><span className="phase-tag">报告 · 静态预览</span></div>
    <div className="content-columns"><div className="stack"><div className="prep-grid"><PreparationList type="tax" items={demoPolicies[0].checklist} /><PreparationList type="grant" items={demoPolicies[1].checklist} /></div><section className="panel"><span className="eyebrow">已选场景报告预览</span><h2>{policy.title}</h2><dl className="report-facts"><dt>合成版本</dt><dd>{policy.version}</dd><dt>结果</dt><dd>{policy.reason}</dd><dt>有效性</dt><dd>{policy.validity === 'stale' ? '依据已失效，待复核' : '静态样例，不代表实时有效'}</dd></dl><p className="notice">{taxNotice}</p><p className="muted">下载 Markdown、历史 run、重新分析均在后续阶段接入；本预览不生成文件。</p></section></div>
      <aside className="panel side-panel"><span className="eyebrow">办理边界</span><h2>{policy.type === 'tax' ? '税收事项' : '资金事项'}</h2><p>{policy.type === 'tax' ? '请企业财税人员按已核实政策自行判断并留存凭证。' : '项目申请材料与入口应按正式通知核对。'}</p>{policy.status === 'closed' ? <p className="notice warning">申报窗口已关闭。仅查看历史信息。</p> : <p className="notice">此处无申报入口；合成样例不能用于办理。</p>}</aside></div>
  </section>;
}
