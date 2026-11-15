import PreparationList from '../components/PreparationList';
import type { DemoPolicy } from '../demo';
import { demoPolicies, taxNotice } from '../demo';

export default function ReportPage({ policy }: { policy: DemoPolicy | null }) {
  if (!policy) return <div className="panel empty-state"><h1>暂无报告示例</h1><p>请先从机会列表选择一项合成场景。</p><a className="button" href="#opportunities">前往机会列表</a></div>;
  return <section className="page-block" aria-labelledby="report-title"><div className="page-heading"><div><span className="eyebrow">04 / 清单与报告</span><h1 id="report-title">准备下一步核对材料</h1><p>查看合成场景的清单与报告示例。</p></div><span className="phase-tag">报告 · 静态预览</span></div>
    <div className="content-columns"><div className="stack"><div className="prep-grid"><PreparationList type="tax" items={demoPolicies[0].checklist} /><PreparationList type="grant" items={demoPolicies[1].checklist} /></div><section className="panel"><span className="eyebrow">已选场景报告预览</span><h2>{policy.title}</h2><dl className="report-facts"><dt>示例版本</dt><dd>{policy.version}</dd><dt>结果</dt><dd>{policy.reason}</dd><dt>有效性</dt><dd>{policy.validity === 'stale' ? '依据已失效，待复核' : '静态样例，实际有效性待核对'}</dd></dl><p className="notice">{taxNotice}</p><p className="muted">当前显示固定示例；生成报告请使用真实接口模式。</p></section></div>
      <aside className="panel side-panel"><span className="eyebrow">办理边界</span><h2>{policy.type === 'tax' ? '税收事项' : '资金事项'}</h2><p>{policy.type === 'tax' ? '请企业财税人员按已核实政策自行判断并留存凭证。' : '项目申请材料与入口应按正式通知核对。'}</p>{policy.status === 'closed' ? <p className="notice warning">申报窗口已关闭，当前展示历史信息。</p> : <p className="notice">办理入口和材料请以官方通知为准。</p>}</aside></div>
  </section>;
}
