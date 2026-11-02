import { traceStatus } from '@host/features/assistant/messages/trace-presentation';
import type { AssistantOperation } from '@host/types/assistant/api';

/** 旧业务记录保留可读摘要；新执行统一使用持久化 trace.block 的具体目标。 */
export function ToolExecutionBlock({ operations }: { operations: AssistantOperation[] }) {
  return <section className="assistant-trace-block" aria-label="执行步骤">
    <ol className="assistant-trace-list">{operations.map(operation => <li className="assistant-trace-row" key={operation.id}>
      <span className="assistant-trace-title">{operation.action || operation.label || '执行创作操作'}{operation.target?.display_name ? `《${operation.target.display_name}》` : ''}</span>
      <span className="assistant-trace-status">{traceStatus(operation.outcome)}</span>
    </li>)}</ol>
  </section>;
}
