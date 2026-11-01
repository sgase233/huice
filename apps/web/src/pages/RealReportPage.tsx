import type { AssessmentRun, PolicyDetail } from '../api/types';
import StatusBadge from '../components/StatusBadge';
import { factLabels, taxNotice } from '../demo';

type Props = { run: AssessmentRun; policies: PolicyDetail[]; onDownload: () => void; busy: boolean };

export default function RealReportPage({ run, policies, onDownload, busy }: Props) {
  return <section className="page-block real-report"><h1>准备清单与报告</h1>
    <p>运行时间：{run.createdAt}；判断期间：{run.period.startDate} 至 {run.period.endDate}</p>
    <p>画像版本：{run.factVersion}；规则版本：{run.ruleVersion}</p>
    <button className="button" disabled={busy} onClick={onDownload}>下载 Markdown 报告</button>
    <p>下载文件不会自动更新；办理前请返回系统重新核验。</p>
    <div className="prep-grid">{(['tax', 'grant'] as const).map(type => <section className="panel" key={type}><h2>{type === 'tax' ? '税收优惠 · 自行判断' : '资金项目 · 申请材料'}</h2>
      {run.assessments.filter(item => item.preparation.type === type).map(item => {
        const policy = policies.find(p => p.id === item.policyId);
        return <article key={item.policyId}><h3>{policy?.title ?? item.policyId}</h3><p>政策版本：{run.policyVersionMap[item.policyId]}</p><StatusBadge result={item.result} validity={item.validity} policyStatus={policy?.status} />
          {policy?.status === 'closed' && <p>申报窗口已关闭，仅供查看历史信息。</p>}
          <p>{item.explanation}</p><p>解释模式：{item.explanationMode}</p>{item.warnings.map((warning, i) => <p key={i}>{warning}</p>)}{item.staleReasons.map((reason, i) => <p key={i}>{reason.message}</p>)}
          <p>办理方式：{item.preparation.handlingMode === 'tax_self_assessment' ? '自行判断' : '项目申请'}；截止时间：{item.preparation.deadline ?? '未提供'}</p>
          <ul>{item.preparation.items.map((entry, i) => <li key={i}><strong>{entry.title}</strong>：{entry.description}（{entry.missing ? '资料缺失' : '资料已提供'}）{entry.requiredFactRefs.map(ref => <span key={`${ref.key}-${ref.period.startDate}-${ref.period.endDate}`}> {factLabels[ref.key]}：{ref.period.startDate} 至 {ref.period.endDate}</span>)}</li>)}</ul>
          <a href={item.preparation.officialEntryUrl} target="_blank" rel="noreferrer">查看官方入口</a>
          {item.criteriaResults.map(criterion => <section key={criterion.criterionId}><p>{criterion.reason}</p>{criterion.evidenceRefs.map((evidence, i) => <blockquote key={i}>{evidence.quote} <a href={evidence.sourceUrl} target="_blank" rel="noreferrer">官方原文</a></blockquote>)}</section>)}
        </article>;
      })}</section>)}</div><p>{taxNotice}</p><p>资金项目不承诺补贴到账，不代企业提交申报。</p>
  </section>;
}
