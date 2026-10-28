import { CircleAlert, Check, LoaderCircle } from 'lucide-react';
import type { AssistantOperation } from '../../api/types/assistant';
import { OperationResults } from './AssistantOperationResults';
import { isFailedOperation, isFinishedOperation } from './agent-timeline';

const stateText: Record<string, string> = {
  running: '处理中', waiting_media: '生成中', reconciling: '核实中',
  succeeded: '已保存', failed: '未完成', conflict: '有冲突',
};

/** Produced work stays visible on its own; only receipts and arguments stay internal. */
export function AgentArtifactCard({ operation, layout, onUndo, undoDisabled }: {
  operation: AssistantOperation; layout?: 'standard'; onUndo?: (id: string) => void; undoDisabled?: boolean;
}) {
  const failed = isFailedOperation(operation);
  const finished = isFinishedOperation(operation);
  return <section className="ai-assistant-artifact" data-state={failed ? 'error' : finished ? 'done' : 'running'}>
    <header className="ai-assistant-artifact-head">
      {failed ? <CircleAlert size={14} aria-hidden="true"/> : finished ? <Check size={14} aria-hidden="true"/>
        : <LoaderCircle size={14} className="ai-assistant-artifact-spin" aria-hidden="true"/>}
      <span className="ai-assistant-artifact-title">{operation.label || '查看成果'}</span>
      <span className="ai-assistant-artifact-state">{operation.tool_name === 'propose_script_patch' && operation.outcome === 'succeeded' ? '可审阅' : stateText[operation.outcome] ?? '处理中'}</span>
    </header>
    {failed && operation.error?.message ? <p className="ai-assistant-artifact-error" role="status">{operation.error.message}</p> : null}
    <OperationResults operation={operation} layout={layout} onUndo={onUndo} undoDisabled={undoDisabled}/>
  </section>;
}
