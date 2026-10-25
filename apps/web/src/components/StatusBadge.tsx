import type { AssessmentResult, PolicyStatus, Validity } from '../api/types';

const labels: Record<AssessmentResult, string> = {
  satisfied: '条件初步满足', not_satisfied: '不满足条件', insufficient_evidence: '还缺资料', conflict: '条款待人工核对',
};

export default function StatusBadge({ result, validity, policyStatus }: { result?: AssessmentResult; validity?: Validity; policyStatus?: PolicyStatus }) {
  return <span className="badges"><span className={`badge badge-${result || 'pending'}`}>{result ? labels[result] : '尚未分析'}</span>{validity === 'stale' && <span className="badge badge-stale">依据已失效，待复核</span>}{policyStatus === 'closed' && <span className="badge badge-closed">申报窗口已关闭</span>}</span>;
}
