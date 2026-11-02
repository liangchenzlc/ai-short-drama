import type { AssistantOperation } from '@host/types/assistant/api';
import type { AgentMessage, AgentRun } from '@host/types/assistant/engine';
import type { AssistantTraceBlock } from '@host/types/assistant/trace';
import type { TimelineEntry } from '@host/types/assistant/messages/agent-timeline';
const FINISHED = new Set(['succeeded', 'failed', 'conflict']);

export const isFinishedOperation = (operation: AssistantOperation) => FINISHED.has(operation.outcome);

export const isFailedOperation = (operation: AssistantOperation) =>
  operation.outcome === 'failed' || operation.outcome === 'conflict';

/** Messages and steps share the conversation item_seq; null falls back to the message seq. */
function sortKey(value: number | null | undefined, fallback: number) {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : fallback;
}

/**
 * One ordered list of what the user may see: narration, step groups and artifacts.
 * Silent operations stay inside folded detail, and consecutive legacy steps share a group.
 */
export function buildTimeline(
  messages: readonly AgentMessage[],
  operations: readonly AssistantOperation[],
  traceBlocks: readonly AssistantTraceBlock[] = [],
  runs: readonly AgentRun[] = [],
): TimelineEntry[] {
  const rows: { order: number; sequence: number; entry: TimelineEntry }[] = [];
  let sequence = 0;
  for (const message of messages) {
    if (message.role !== 'user' && message.role !== 'assistant') continue;
    rows.push({
      order: sortKey(message.item_seq, message.seq * 1_000_000),
      sequence: sequence++,
      entry: { kind: 'message', key: `message:${message.id}`, message },
    });
  }
  for (const operation of operations) {
    if (traceBlocks.some(block => block.operation_id === operation.id)) continue;
    const visibility = operation.visibility ?? 'step';
    rows.push({
      order: sortKey(operation.item_seq, Number.MAX_SAFE_INTEGER),
      sequence: sequence++,
      entry:
        visibility === 'artifact'
          ? { kind: 'artifact', key: `artifact:${operation.id}`, operation }
          : {
              kind: 'steps',
              key: `steps:${operation.id}`,
              operations: [operation],
            },
    });
  }
  const maxSeq = Math.max(1, ...traceBlocks.map(block => block.started_seq));
  for (const block of traceBlocks) {
    if (block.parent_call_id !== null) continue;
    const taskId = runs.find(run => run.id === block.run_id)?.task_id ?? operations.find(operation => operation.run_id === block.run_id)?.task_id;
    const message = messages.find(item => item.task_id === taskId && item.role === 'user');
    rows.push({ order: message ? sortKey(message.item_seq, message.seq * 1_000_000) + 0.5 + block.started_seq / (maxSeq + 1) * 0.4 : Number.MAX_SAFE_INTEGER / 2 + block.started_seq,
      sequence: sequence++, entry: { kind: 'trace', key: `trace:${block.block_id}`, block } });
  }
  rows.sort((left, right) => left.order - right.order || left.sequence - right.sequence);

  const timeline: TimelineEntry[] = [];
  for (const { entry } of rows) {
    const previous = timeline[timeline.length - 1];
    if (entry.kind === 'steps' && previous?.kind === 'steps') {
      previous.operations.push(...entry.operations);
      continue;
    }
    timeline.push(entry);
  }
  return timeline;
}
