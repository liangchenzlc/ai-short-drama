import type { AgentEvent } from '@host/types/assistant/engine';
import type { DeltaChunk } from '@host/types/assistant/runtime/agent-events';
/** Preserve partial frames across arbitrary UTF-8 network chunks. */
export function takeSseFrames(buffer: string): {
  frames: string[];
  rest: string;
} {
  const normalized = buffer.replace(/\r\n/g, '\n');
  const parts = normalized.split('\n\n');
  return { frames: parts.slice(0, -1), rest: parts.at(-1) ?? '' };
}
export function parseAgentEvent(frame: string): AgentEvent | null {
  const data = frame
    .split('\n')
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.slice(5).trimStart())
    .join('\n');
  if (!data) return null; // Includes heartbeat comments.
  try {
    const event = JSON.parse(data) as AgentEvent;
    return Number.isSafeInteger(event.seq) &&
      event.seq > 0 &&
      typeof event.event_type === 'string' &&
      event.payload &&
      typeof event.payload === 'object' &&
      !Array.isArray(event.payload)
      ? event
      : null;
  } catch {
    return null;
  }
}
export function sseControl(frame: string): 'resync' | 'retry' | 'access-ended' | null {
  const kind = frame
    .split('\n')
    .filter((line) => line.startsWith('event:'))
    .at(-1)
    ?.slice(6)
    .trim();
  return kind === 'resync' || kind === 'retry' || kind === 'access-ended' ? kind : null;
}
export function addDelta(chunks: DeltaChunk[], event: AgentEvent): DeltaChunk[] {
  const { turn_id, offset, delta } = event.payload;
  if (
    typeof turn_id !== 'string' ||
    !Number.isSafeInteger(offset) ||
    (offset as number) < 0 ||
    typeof delta !== 'string'
  )
    return chunks;
  if (chunks.some((chunk) => chunk.turn === turn_id && chunk.offset === offset)) return chunks;
  return [...chunks, { turn: turn_id, offset: offset as number, text: delta }];
}
export function latestDelta(chunks: DeltaChunk[]): string {
  const turn = chunks.at(-1)?.turn;
  return chunks
    .filter((chunk) => chunk.turn === turn)
    .sort((a, b) => a.offset - b.offset)
    .map((chunk) => chunk.text)
    .join('');
}

export const modelCanCollaborate = (
  model:
    | {
        verified: boolean;
        tool_calling: boolean;
        tool_result_continuation: boolean;
      }
    | undefined,
) => !!model?.verified && model.tool_calling && model.tool_result_continuation;
