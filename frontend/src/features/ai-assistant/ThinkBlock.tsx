import { useEffect, useState } from 'react';
import { Check, ChevronRight, CircleAlert, LoaderCircle } from 'lucide-react';
import type { AssistantOperation } from '../../api/types/assistant';
import { isFailedOperation, isFinishedOperation } from './agent-timeline';

const phaseNotes: Record<string, string> = {
  tool_running: '处理中',
  waiting_media: '生成中',
  settling_result: '回填中',
  reconciling: '核实中',
  terminal: '',
};

function noteOf(operation: AssistantOperation) {
  if (operation.outcome === 'failed' || operation.outcome === 'conflict') {
    return operation.error?.message || (operation.outcome === 'conflict' ? '内容有冲突，已保留成果' : '未完成');
  }
  return isFinishedOperation(operation) ? '' : phaseNotes[operation.phase] ?? '处理中';
}

type Row = { key: string; label: string; count: number; operation: AssistantOperation };

/** 同名连续步骤合并成一行，避免「读取创作方法」连续刷屏。 */
function toRows(operations: AssistantOperation[]): Row[] {
  return operations.reduce<Row[]>((rows, operation) => {
    const label = operation.label || '执行创作操作';
    const last = rows[rows.length - 1];
    if (last && last.label === label && last.operation.outcome === operation.outcome
      && last.operation.error?.code === operation.error?.code && noteOf(last.operation) === noteOf(operation)) {
      last.count += 1;
      return rows;
    }
    rows.push({ key: operation.id, label, count: 1, operation });
    return rows;
  }, []);
}

/**
 * Running and failed groups start open; successful groups fold themselves.
 * A manual toggle survives ordinary updates within the same group state.
 */
export function ThinkBlock({ operations }: { operations: AssistantOperation[] }) {
  const running = operations.some(operation => !isFinishedOperation(operation));
  const failed = operations.filter(isFailedOperation).length;
  const hasFailed = failed > 0;
  const [expanded, setExpanded] = useState(running || hasFailed);

  useEffect(() => {
    setExpanded(running || hasFailed);
  }, [running, hasFailed]);

  const title = running
    ? `正在处理 ${operations.length} 步`
    : failed
      ? `${failed} 步未完成`
      : `已完成 ${operations.length} 步`;

  return <section className="ai-assistant-think" data-state={running ? 'running' : failed ? 'error' : 'done'} aria-label="执行步骤">
    <button type="button" className="ai-assistant-think-head" aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>
      {running ? <LoaderCircle size={14} className="ai-assistant-think-spin" aria-hidden="true"/>
        : failed ? <CircleAlert size={14} aria-hidden="true"/> : <Check size={14} aria-hidden="true"/>}
      <span className="ai-assistant-think-title">{title}</span>
      <ChevronRight size={14} className="ai-assistant-think-chevron" data-expanded={expanded} aria-hidden="true"/>
    </button>
    {expanded && <ol className="ai-assistant-think-list">
      {toRows(operations).map(row => <li key={row.key} data-outcome={row.operation.outcome}>
        <span className="ai-assistant-think-dot" aria-hidden="true"/>
        <span className="ai-assistant-think-label">{row.label}{row.count > 1 ? ` ×${row.count}` : ''}</span>
        <span className="ai-assistant-think-note">{noteOf(row.operation)}</span>
      </li>)}
    </ol>}
  </section>;
}
