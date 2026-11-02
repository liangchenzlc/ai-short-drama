import type { AssistantTraceBlock } from '@host/types/assistant/trace';
export const traceStatus = (value: string) => ({ running: '执行中', succeeded: '已完成', failed: '失败', conflict: '冲突', interrupted: '已中断', unknown: '结果待核对', reconciling: '核实中', cancelled: '已停止', waiting_input: '等待回答', waiting_media: '等待生成', waiting_generation: '等待生成' }[value] ?? value);
export const traceChildren = (block: AssistantTraceBlock, blocks: readonly AssistantTraceBlock[]) =>
  blocks.filter(item => item.run_id === block.run_id && item.parent_call_id === block.tool_call_id).sort((a, b) => a.started_seq - b.started_seq);
export const traceRoots = (blocks: readonly AssistantTraceBlock[]) => blocks.filter(block => block.parent_call_id === null).sort((a, b) => a.started_seq - b.started_seq);

export const traceTitle = (block: Pick<AssistantTraceBlock, 'action' | 'target'>) =>
  block.target.kind === 'command' ? block.action : `${block.action}《${block.target.display_name}》`;
