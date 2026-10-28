import type { AssistantOperation } from '@host/types/assistant/api';
import type { AgentMessage } from '@host/types/assistant/engine';
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
 * Silent operations never produce an entry, and consecutive steps merge into one group.
 */
export function buildTimeline(
  messages: readonly AgentMessage[],
  operations: readonly AssistantOperation[],
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
    const visibility = operation.visibility ?? 'step';
    if (visibility === 'silent') continue;
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
