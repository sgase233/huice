export default function EvidenceRow({ evidence, index }: { evidence: string; index: number }) {
  return <li className="evidence-row"><span className="evidence-index">{String(index + 1).padStart(2, '0')}</span><div><strong>合成条款引用 {index + 1}</strong><p>{evidence}</p><small>合成示例；原文位置和哈希待核实。</small></div></li>;
}
