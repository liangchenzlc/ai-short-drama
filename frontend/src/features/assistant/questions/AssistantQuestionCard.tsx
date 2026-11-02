import { useAssistantQuestion } from '@host/features/assistant/questions/useAssistantQuestion';
import type { AssistantQuestionCardProps } from '@host/types/assistant/questions/state';
import { Button, Input } from 'antd';
import { Check, ChevronLeft, ChevronRight, Pencil } from 'lucide-react';

export function AssistantQuestionCard(props: AssistantQuestionCardProps) {
  const state = useAssistantQuestion(props);
  const { question } = props;
  const fields = question.request.fields;
  const index = Math.min(state.draft.index, fields.length - 1);
  const field = fields[index];
  const multiple = field.type === 'multi_select';
  const value = state.draft.values[field.key];
  const other = state.draft.otherSelected.includes(field.key);
  if (!props.active || state.resolved !== 'pending') return null;

  function select(option: string) {
    const selected = Array.isArray(value) ? value : [];
    state.edit({
      values: { ...state.draft.values, [field.key]: multiple ? selected.includes(option) ? selected.filter(item => item !== option) : [...selected, option] : option },
      otherSelected: multiple ? state.draft.otherSelected : state.draft.otherSelected.filter(key => key !== field.key),
    });
  }
  function selectOther() {
    state.edit({
      otherSelected: other ? state.draft.otherSelected.filter(key => key !== field.key) : [...state.draft.otherSelected, field.key],
      values: multiple ? state.draft.values : { ...state.draft.values, [field.key]: null },
    });
  }
  const move = (next: number) => state.edit({ index: next });
  return <section className="assistant-question-card" role="region" aria-label={question.request.title}>
    <form onSubmit={event => { event.preventDefault(); if (index < fields.length - 1) move(index + 1); else void state.submit('answered'); }}>
      <div className="assistant-question-head">
        <strong>{fields.length === 1 ? question.request.question : field.label}</strong>
        {fields.length > 1 && <nav aria-label="问题切换">
          <Button type="text" size="small" aria-label="上一题" disabled={index === 0 || state.busy} icon={<ChevronLeft size={14} />} onClick={() => move(index - 1)} />
          <span aria-live="polite">{index + 1} / {fields.length}</span>
          <Button type="text" size="small" aria-label="下一题" disabled={index === fields.length - 1 || state.busy} icon={<ChevronRight size={14} />} onClick={() => move(index + 1)} />
        </nav>}
      </div>
      <div className="assistant-question-content">
        <fieldset className="assistant-question-options" disabled={!state.editable}>
          <legend className="assistant-question-mode">{multiple ? '可多选' : '单选'}</legend>
          {[...field.options].sort((a, b) => Number(b.recommended) - Number(a.recommended)).map((option, optionIndex) => {
            const checked = multiple ? Array.isArray(value) && value.includes(option.value) : !other && value === option.value;
            return <label className="assistant-question-choice" data-selected={checked} key={option.value}>
              <input type={multiple ? 'checkbox' : 'radio'} name={`${question.operation_id}:${field.key}`} aria-label={option.label} checked={checked} onChange={() => select(option.value)} />
              <span className="assistant-question-number" aria-hidden="true">{checked ? <Check size={13} /> : optionIndex + 1}</span>
              <span className="assistant-question-option-copy"><span>{option.label}{option.recommended && <span className="assistant-question-recommended">推荐</span>}</span>{option.description && <small>{option.description}</small>}</span>
            </label>;
          })}
          <label className="assistant-question-choice assistant-question-other" data-selected={other}>
            <input type={multiple ? 'checkbox' : 'radio'} name={`${question.operation_id}:${field.key}`} aria-label="其他" checked={other} onChange={selectOther} />
            <span className="assistant-question-number" aria-hidden="true">{other ? <Check size={13} /> : <Pencil size={13} />}</span>
            <span>其他…</span>
          </label>
          {other && <Input.TextArea aria-label={`${field.label}：其他回答`} autoSize={{ minRows: 2, maxRows: 3 }} maxLength={8000} value={state.draft.otherValues[field.key] ?? ''} placeholder={field.placeholder ?? '填写你的想法'} onChange={event => state.edit({ otherValues: { ...state.draft.otherValues, [field.key]: event.target.value } })} />}
        </fieldset>
      </div>
      {state.issue && <p className="assistant-question-issue" role="status">{state.issue}</p>}
      <div className="assistant-question-actions">
        {question.request.allow_skip && <Button size="small" type="text" disabled={!state.editable} onClick={() => void state.submit('skipped')}>跳过</Button>}
        {(state.draft.pending || state.blocked) && <Button size="small" disabled={state.busy} onClick={() => void state.check()}>核对回答状态</Button>}
        {state.draft.pending && !state.blocked && <Button size="small" disabled={state.busy || props.disabled || !question.can_answer} onClick={() => void state.submit(state.draft.pending!.body.outcome, true)}>重试原回答</Button>}
        <Button size="small" type="primary" htmlType="submit" loading={state.busy} disabled={!state.editable}>{index < fields.length - 1 ? '继续下一题' : '提交回答'}</Button>
      </div>
    </form>
  </section>;
}
