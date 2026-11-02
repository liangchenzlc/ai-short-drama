import { AssistantQuestionCard } from '@host/features/assistant/questions/AssistantQuestionCard';
import type { AssistantAnswerableQuestion, AssistantQuestionDockProps } from '@host/types/assistant/questions/state';
import { Button } from 'antd';
import { useCallback, useState } from 'react';

export function AssistantQuestionDock({ accountId, conversationId, questions, runs, tasks, disabled, refresh }: AssistantQuestionDockProps) {
  const [selected, setSelected] = useState('');
  const [resolved, setResolved] = useState<ReadonlySet<string>>(() => new Set());
  const remove = useCallback((id: string) => setResolved(previous => new Set([...previous, id])), []);
  const pending = questions.filter((question): question is AssistantAnswerableQuestion => question.status === 'pending' && question.request !== null && !resolved.has(question.operation_id));
  const active = pending.find(question => question.operation_id === selected) ?? pending[0];
  if (!active) return null;
  return <aside className="assistant-question-dock" aria-label="待答问题">
    {pending.length > 1 && <div className="assistant-question-groups" role="group" aria-label="切换问题组">
      {pending.map((question, index) => <Button size="small" type="text" key={question.operation_id} aria-pressed={active.operation_id === question.operation_id} onClick={() => setSelected(question.operation_id)}>问题组 {index + 1}</Button>)}
    </div>}
    {pending.map(question => {
      const run = runs.find(item => item.id === question.run_id);
      const task = tasks.find(item => item.id === run?.task_id);
      return <AssistantQuestionCard key={question.operation_id} accountId={accountId} conversationId={conversationId} question={question}
        active={question.operation_id === active.operation_id} onResolved={remove}
        disabled={disabled || ['cancelled', 'failed', 'succeeded'].includes(run?.status ?? '') || task?.status === 'stopped' || task?.control_state === 'stop_pending'}
        refresh={() => refresh(question.operation_id)} />;
    })}
  </aside>;
}
