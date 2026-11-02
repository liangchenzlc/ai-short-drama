import { ReasoningBlock } from '@host/features/assistant/messages/ReasoningBlock';
import { traceChildren, traceStatus, traceTitle } from '@host/features/assistant/messages/trace-presentation';
import type { TraceBlockProps } from '@host/types/assistant/messages/trace';
import type { AssistantTraceBlock as Block } from '@host/types/assistant/trace';

export function AssistantTraceBlock({ blocks, ...props }: TraceBlockProps & { blocks: readonly Block[] }) {
  const { block } = props;
  if (block.kind === 'reasoning') return <ReasoningBlock {...props} />;
  return <section className="assistant-trace-block" data-visibility={block.visibility}>
    <div className="assistant-trace-row"><span className="assistant-trace-title">{traceTitle(block)}</span><span className="assistant-trace-status">{traceStatus(block.status)}</span></div>
    {props.renderResults(block)}
    {block.kind === 'subtask' && <div className="assistant-subtask-children" aria-label="子任务轨迹">{traceChildren(block, blocks).map(child => <AssistantTraceBlock key={child.block_id} {...props} block={child} blocks={blocks} />)}</div>}
  </section>;
}
