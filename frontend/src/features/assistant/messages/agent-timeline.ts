import type { AssistantOperation } from '@host/types/assistant/api';
import type { AgentMessage, AgentRun } from '@host/types/assistant/engine';
import { ToolTraceVisibility, type AssistantTraceBlock } from '@host/types/assistant/trace';
import type { StreamingTextPart } from '@host/types/assistant/runtime/agent-events';
import type { AssistantTurnPart, TimelineEntry } from '@host/types/assistant/messages/agent-timeline';

const FINISHED_RUNS = new Set(['succeeded', 'failed', 'cancelled']);
const FINISHED_OPERATIONS = new Set(['succeeded', 'failed', 'conflict']);

export const isFinishedOperation = (operation: AssistantOperation) =>
  FINISHED_OPERATIONS.has(operation.outcome);

export const isFailedOperation = (operation: AssistantOperation) =>
  operation.outcome === 'failed' || operation.outcome === 'conflict';

function orderedParts(parts: AssistantTurnPart[]) {
  return parts
    .map((part, index) => ({ part, index }))
    .sort((left, right) => {
      if (left.part.seq === null) return right.part.seq === null ? left.index - right.index : 1;
      if (right.part.seq === null) return -1;
      return left.part.seq - right.part.seq || left.index - right.index;
    })
    .map(({ part }) => part);
}

function addMessagePart(bucket: AssistantTurnPart[], message: AgentMessage) {
  const textParts = message.text_parts ?? [];
  const hasExactTextParts =
    textParts.length === 1 && textParts[0].content === message.content;
  bucket.push({
    kind: 'text',
    key: `message:${message.id}`,
    seq: hasExactTextParts ? textParts[0].event_seq : (message.event_seq ?? null),
    text: hasExactTextParts ? textParts[0].content : message.content,
    message,
  });
}

/** Build one assistant turn from the same conversation event sequence used by SSE. */
export function buildTimeline(
  messages: readonly AgentMessage[],
  operations: readonly AssistantOperation[],
  traceBlocks: readonly AssistantTraceBlock[] = [],
  runs: readonly AgentRun[] = [],
  streamingParts: readonly StreamingTextPart[] = [],
  streamingRunId?: string,
): TimelineEntry[] {
  const buckets = new Map<string, { parts: AssistantTurnPart[]; results: AssistantOperation[] }>();
  const runById = new Map(runs.map((run) => [run.id, run]));
  const operationById = new Map(operations.map((operation) => [operation.id, operation]));
  const tracedOperations = new Set(
    traceBlocks.flatMap((block) => (block.operation_id ? [block.operation_id] : [])),
  );
  const entries: TimelineEntry[] = [];
  const bucketFor = (runId: string) => {
    const existing = buckets.get(runId);
    if (existing) return existing;
    const bucket = { parts: [] as AssistantTurnPart[], results: [] as AssistantOperation[] };
    buckets.set(runId, bucket);
    return bucket;
  };

  for (const message of messages) {
    if (message.role !== 'user' && message.role !== 'assistant') continue;
    if (message.role === 'user') {
      entries.push({
        kind: 'message',
        key: `message:${message.id}`,
        seq: message.event_seq ?? null,
        message,
      });
    } else if (message.run_id) {
      addMessagePart(bucketFor(message.run_id).parts, message);
    } else {
      entries.push({
        kind: 'message',
        key: `message:${message.id}`,
        seq: message.event_seq ?? null,
        message,
      });
    }
  }

  for (const block of traceBlocks) {
    if (block.parent_call_id !== null) continue;
    const operation = block.operation_id ? operationById.get(block.operation_id) : undefined;
    const bucket = bucketFor(block.run_id);
    if (block.kind === 'reasoning' || block.trace_visibility !== ToolTraceVisibility.HIDDEN) {
      bucket.parts.push({
        kind: 'activity',
        key: `activity:${block.operation_id ?? block.block_id}`,
        seq: block.started_seq,
        block,
        operation,
      });
    }
    if (operation && operation.result_refs.length > 0) bucket.results.push(operation);
  }

  for (const operation of operations) {
    const bucket = bucketFor(operation.run_id);
    if (operation.result_refs.length > 0 && !bucket.results.some((item) => item.id === operation.id))
      bucket.results.push(operation);
    if (tracedOperations.has(operation.id) || operation.trace_visibility === ToolTraceVisibility.HIDDEN)
      continue;
    bucket.parts.push({
      kind: 'activity',
      key: `activity:${operation.id}`,
      seq: operation.event_seq ?? null,
      operation,
    });
  }

  if (streamingRunId && streamingParts.length) {
    const bucket = bucketFor(streamingRunId);
    const persistedTurns = new Set(bucket.parts.flatMap((part) =>
      part.kind === 'text' ? (part.message?.text_parts ?? []).map((text) => text.turn_id) : []));
    for (const part of streamingParts) {
      if (!persistedTurns.has(part.turn)) {
        bucket.parts.push({
          kind: 'text',
          key: `stream:${streamingRunId}:${part.turn}`,
          seq: part.seq,
          text: part.text,
        });
      }
    }
  }

  for (const run of runs) {
    if (!FINISHED_RUNS.has(run.status)) bucketFor(run.id);
  }

  const userBoundaries = entries.flatMap((entry) => entry.kind === 'message' && entry.message.role === 'user' && entry.seq !== null
    ? [{ seq: entry.seq, id: entry.message.id }] : []).sort((a, b) => a.seq - b.seq);
  const segmentAt = (seq: number | null) => seq === null ? userBoundaries.length : userBoundaries.filter((boundary) => boundary.seq < seq).length;
  for (const [runId, bucket] of buckets) {
    const parts = orderedParts(bucket.parts);
    const seqs = parts.flatMap((part) => (part.seq === null ? [] : [part.seq]));
    const results = [...new Map(bucket.results.map((item) => [item.id, item])).values()].sort(
      (left, right) => (left.result_seq ?? left.event_seq ?? Number.MAX_SAFE_INTEGER) -
        (right.result_seq ?? right.event_seq ?? Number.MAX_SAFE_INTEGER),
    );
    const status = runById.get(runId)?.status ?? null;
    if (!parts.length && !results.length && status && FINISHED_RUNS.has(status)) continue;
    // 用户插话是同一 Run 的显示分界；trace 本身身份及子 scope 仍由原块维护。
    const segments = new Map<number, { parts: AssistantTurnPart[]; results: AssistantOperation[] }>();
    const segmentFor = (index: number) => {
      if (!segments.has(index)) segments.set(index, { parts: [], results: [] });
      return segments.get(index)!;
    };
    for (const part of parts) segmentFor(segmentAt(part.seq)).parts.push(part);
    for (const result of results) segmentFor(segmentAt(result.result_seq ?? result.event_seq ?? null)).results.push(result);
    if (!segments.size) segmentFor(userBoundaries.length);
    const lastSegment = Math.max(...segments.keys());
    for (const [segment, content] of segments) entries.push({
      kind: 'assistant_turn',
      key: `turn:${runId}:${segment ? `user:${userBoundaries[segment - 1].id}` : 'start'}`,
      seq: content.parts.find((part) => part.seq !== null)?.seq ?? content.results[0]?.result_seq ?? (seqs.length ? Math.min(...seqs) : null),
      run_id: runId,
      status: segment === lastSegment ? status : null,
      parts: content.parts,
      results: content.results,
    });
  }

  return entries
    .map((entry, index) => ({ entry, index }))
    .sort((left, right) => {
      if (left.entry.seq === null) return right.entry.seq === null ? left.index - right.index : 1;
      if (right.entry.seq === null) return -1;
      return left.entry.seq - right.entry.seq || left.index - right.index;
    })
    .map(({ entry }) => entry);
}
