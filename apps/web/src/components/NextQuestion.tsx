import { useRef, useState } from 'react';
import type { NextQuestion as Question } from '../api/types';
import { factUnits, factUnitLabels } from '../demo';
import { validateEnterprise } from '../pages/EnterprisePage';
import type { Enterprise } from '../api/types';

type Props = {
  question: Question;
  pending: boolean;
  enterprise: Enterprise;
  onAnswer: (value: string | boolean) => void;
  onSkip: () => void;
};

export default function NextQuestion({ question, pending, enterprise, onAnswer, onSkip }: Props) {
  const [value, setValue] = useState('');
  const [error, setError] = useState('');
  const inputRef = useRef<HTMLInputElement | HTMLSelectElement>(null);
  const unit = factUnits[question.factRef.key];
  const current = enterprise.facts.find(item => item.key === question.factRef.key && item.period.startDate === question.factRef.period.startDate && item.period.endDate === question.factRef.period.endDate);
  return <section className="panel" aria-labelledby="next-question-title">
    <h2 id="next-question-title">优先补充问题</h2>
    <p>{question.prompt}</p>
    <p>信息所属期间：{question.factRef.period.startDate} 至 {question.factRef.period.endDate}</p>
    <p>当前填写的内容：{current?.value == null ? '未知' : typeof current.value === 'boolean' ? (current.value ? '是' : '否') : current.value}</p>
    <p>{question.reason}</p>
    <form className="question-form" onSubmit={event => {
      event.preventDefault();
      if (value === '') return;
      const answer = unit === 'boolean' ? value === 'true' : value;
      const ref = question.factRef;
      const fact = { key: ref.key, period: ref.period, value: answer, unit, source: 'user_input' as const };
      const facts = enterprise.facts.filter(item => !(item.key === ref.key && item.period.startDate === ref.period.startDate && item.period.endDate === ref.period.endDate));
      const errors = validateEnterprise({ nameSynthetic: enterprise.nameSynthetic, period: enterprise.period, facts: [...facts, fact] });
      if (errors.length) { setError(errors.join(' ')); inputRef.current?.focus(); return; }
      setError(''); onAnswer(answer);
    }}>
      {error && <p role="alert" id="next-question-error">{error}</p>}
      <label htmlFor="next-question-value">回答{unit !== 'boolean' && `（${factUnitLabels[unit]}）`}</label>
      {unit === 'boolean' ? <select ref={inputRef as React.RefObject<HTMLSelectElement>} id="next-question-value" aria-invalid={!!error} aria-describedby={error ? 'next-question-error' : undefined} value={value} onChange={event => setValue(event.target.value)} disabled={pending} required><option value="">请选择</option><option value="true">是</option><option value="false">否</option></select> : <input ref={inputRef as React.RefObject<HTMLInputElement>} id="next-question-value" aria-invalid={!!error} aria-describedby={error ? 'next-question-error' : undefined} type={unit === 'date' ? 'date' : 'text'} value={value} onChange={event => setValue(event.target.value)} disabled={pending} required />}
      <div className="form-actions"><button className="button" disabled={pending}>保存并重新分析</button>
      <button className="button button-secondary" type="button" disabled={pending} onClick={onSkip}>跳过（未知或不确定）</button></div>
    </form>
  </section>;
}
