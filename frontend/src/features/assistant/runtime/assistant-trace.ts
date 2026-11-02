import type { AgentEvent } from '@host/types/assistant/engine';
import type {
  AssistantTraceBlock,
  AssistantTraceDelta,
  AssistantTraceProjection,
} from '@host/types/assistant/trace';

const version = (value: string) => BigInt(value);
const terminal = (block: AssistantTraceBlock) =>
  ['succeeded', 'failed', 'interrupted', 'unknown', 'cancelled'].includes(block.status);
const encoder = new TextEncoder();
const preview = (text: string) =>
  new TextDecoder().decode(encoder.encode(text).subarray(0, 16 * 1024), { stream: true });

function drainTraceDeltas(old: AssistantTraceBlock, pending: AssistantTraceDelta[]): [AssistantTraceBlock, AssistantTraceDelta[]] {
  const block = { ...old, channels: { ...old.channels } };
  const remaining: AssistantTraceDelta[] = [];
  for (const delta of [...pending].sort((a, b) => a.channel.localeCompare(b.channel) || a.offset - b.offset)) {
    const channel = block.channels[delta.channel] ?? { preview: '', offset: 0, total_bytes: 0, truncated: false };
    if (delta.offset < channel.total_bytes) continue;
    if (delta.offset !== channel.total_bytes) { remaining.push(delta); continue; }
    // 一旦前缀被截断，后续字符不能填入被截掉的 UTF-8 字符位置。
    const text = channel.truncated ? channel.preview : preview(channel.preview + delta.delta);
    const total = delta.offset + encoder.encode(delta.delta).byteLength;
    block.channels[delta.channel] = { ...channel, preview: text, total_bytes: total, truncated: total > encoder.encode(text).byteLength };
    if (version(delta.block_version) > version(block.block_version)) block.block_version = delta.block_version;
    block.updated_at = delta.created_at;
  }
  return [block, remaining];
}

export function mergeTraceBlocks<T extends AssistantTraceProjection>(state: T, items: AssistantTraceBlock[]): T {
  const traceBlocks = { ...state.traceBlocks }, traceDeltas = { ...state.traceDeltas };
  for (const block of items) {
    const old = traceBlocks[block.block_id];
    if (old && (version(old.block_version) > version(block.block_version) || (terminal(old) && !terminal(block)))) continue;
    const pending = (traceDeltas[block.block_id] ?? []).filter(
      delta => !terminal(block) && version(delta.block_version) > version(block.block_version),
    );
    [traceBlocks[block.block_id], traceDeltas[block.block_id]] = drainTraceDeltas(block, pending);
  }
  return { ...state, traceBlocks, traceDeltas };
}

export function applyTraceEvent<T extends AssistantTraceProjection>(state: T, event: AgentEvent): T {
  const p = event.payload;
  if (typeof p.block_id !== 'string' || typeof p.block_version !== 'string' || !/^\d+$/.test(p.block_version)) return state;
  if (event.event_type === 'trace.block') {
    if (p.run_id !== event.run_id || typeof p.execution_scope_id !== 'string' || typeof p.title !== 'string') return state;
    return mergeTraceBlocks(state, [p as unknown as AssistantTraceBlock]);
  }
  const old = state.traceBlocks[p.block_id];
  if (!old || terminal(old) || old.run_id !== event.run_id || old.execution_scope_id !== p.execution_scope_id ||
      typeof p.channel !== 'string' || typeof p.delta !== 'string' || typeof p.offset !== 'number' || p.offset < 0) return state;
  const incoming = p as unknown as AssistantTraceDelta;
  const current = old.channels[incoming.channel];
  if (incoming.offset < (current?.total_bytes ?? 0)) return state;
  // 同一 flush 可更新多个通道；按服务端字节位置去重，版本不由 JS 字符串长度推算。
  const pending = [...(state.traceDeltas[old.block_id] ?? [])];
  if (pending.some(delta => delta.channel === incoming.channel && delta.offset === incoming.offset)) return state;
  pending.push(incoming);
  const [block, remaining] = drainTraceDeltas(old, pending);
  let bytes = 0;
  const bounded = remaining.filter((delta, index) => {
    bytes += encoder.encode(delta.delta).byteLength;
    return index < 128 && bytes <= 16 * 1024;
  });
  return {
    ...state,
    traceBlocks: { ...state.traceBlocks, [block.block_id]: block },
    traceDeltas: { ...state.traceDeltas, [block.block_id]: bounded },
  };
}
