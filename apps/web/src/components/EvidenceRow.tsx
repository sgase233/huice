export default function EvidenceRow({ evidence, index }: { evidence: string; index: number }) {
  return <li className="evidence-row"><span className="evidence-index">{String(index + 1).padStart(2, '0')}</span><div><strong>合成条款引用 {index + 1}</strong><p>{evidence}</p><small>仅用于布局和冲突展示；无已核实原文位置或哈希。</small></div></li>;
}
