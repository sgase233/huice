import type { AssessmentRun, AssessmentResult } from '../api/types';

const states: Array<[AssessmentResult, string]> = [
  ['satisfied', '条件初步满足'], ['not_satisfied', '不满足条件'],
  ['insufficient_evidence', '还缺资料'], ['conflict', '条款待人工核对'],
];

export default function ResultDistribution({ run }: { run: AssessmentRun }) {
  const total = run.assessments.length;
  const stale = run.assessments.filter(item => item.validity === 'stale').length;
  return <section className="panel result-distribution" aria-labelledby="distribution-title">
    <h2 id="distribution-title">本次分析结果分布</h2>
    <p>统计本次分析的 {total} 项政策，不含未分析候选；不随下方列表筛选变化。</p>
    {total === 0 ? <p>本次分析没有结果。</p> : <ul>{states.map(([state, label]) => {
      const count = run.assessments.filter(item => item.result === state).length;
      return <li key={state}><span>{label}：{count} 项</span><span className="distribution-track" aria-hidden="true"><span style={{ width: `${count / total * 100}%` }} /></span></li>;
    })}</ul>}
    <p>{stale ? `其中 ${stale} 项依据已失效，待复核；失效与上述结果状态可同时存在。` : '本次结果没有标记为依据失效的政策。'}</p>
  </section>;
}
