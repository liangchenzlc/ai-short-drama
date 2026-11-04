import type { AgentEvent } from '@host/types/assistant/engine';
import type { DeltaChunk, StreamingTextPart } from '@host/types/assistant/runtime/agent-events';
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
  return [...chunks, { turn: turn_id, offset: offset as number, seq: event.seq, text: delta }];
}
/** 缺口回放到水位前保留已显示正文，接上后用新回放替换，避免保留已失效片段。 */
export function recoverDeltaReplay(
  held: Record<string, DeltaChunk[]>, replay: Record<string, DeltaChunk[]>, cursor: number, watermark: number,
): Record<string, DeltaChunk[]> {
  if (cursor >= watermark) return replay;
  const merged = { ...held };
  for (const [runId, chunks] of Object.entries(replay)) {
    const old = held[runId] ?? [];
    const keys = new Set(old.map((chunk) => `${chunk.turn}:${chunk.offset}`));
    merged[runId] = [...old, ...chunks.filter((chunk) => !keys.has(`${chunk.turn}:${chunk.offset}`))];
  }
  return merged;
}
export function streamingParts(chunks: DeltaChunk[]): StreamingTextPart[] {
  const turns = new Map<string, DeltaChunk[]>();
  for (const chunk of chunks) {
    const items = turns.get(chunk.turn);
    if (items) items.push(chunk);
    else turns.set(chunk.turn, [chunk]);
  }
  return [...turns.entries()]
    .map(([turn, items]) => ({
      turn,
      seq: Math.min(...items.map((item) => item.seq)),
      text: [...items].sort((left, right) => left.offset - right.offset).map((item) => item.text).join(''),
    }))
    .sort((left, right) => left.seq - right.seq);
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
