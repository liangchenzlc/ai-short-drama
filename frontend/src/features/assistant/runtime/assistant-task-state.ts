import type {
  AssistantOperation,
  AssistantSkillActivation,
  AssistantTask,
  AssistantTaskSnapshot,
  AssistantUndoReceipt,
} from '@host/types/assistant/api';
import type { AgentEvent, AgentMessage } from '@host/types/assistant/engine';
import { ToolTraceVisibility } from '@host/types/assistant/trace';
import type { OperationPagination, TaskProjection } from '@host/types/assistant/runtime/assistant-task-state';
import { applyTraceEvent, mergeTraceBlocks } from '@host/features/assistant/runtime/assistant-trace';
export const emptyTaskProjection = (conversationId: string): TaskProjection => ({
  conversationId,
  snapshotCursor: -1,
  operationSnapshotCursors: {},
  traceSnapshotCursors: {},
  deleted: false,
  tasks: {},
  operations: {},
  traceBlocks: {},
  traceDeltas: {},
});
const version = (value: string) => (/^\d+$/.test(value) ? BigInt(value) : -1n);
export function operationPageStart(snapshot: AssistantTaskSnapshot, taskId: string, cached?: OperationPagination): OperationPagination | null {
  if (!snapshot.has_more_operations) return null;
  const prefix = snapshot.operations.filter((item) => item.task_id === taskId).at(-1)?.id;
  const activeId = snapshot.tasks.find((task) => task.id === taskId)?.active_operation?.id;
  if (cached?.complete && (!activeId || version(activeId) <= version(cached.afterId ?? '0'))) return null;
  return { afterId: cached?.afterId ?? prefix, complete: false };
}
export function mergeAssistantMessages(old: AgentMessage[], items: AgentMessage[]) {
  const byId = new Map(old.map((item) => [item.id, item]));
  for (const item of items) {
    if (item.role !== 'user' && item.role !== 'assistant') continue;
    const previous = byId.get(item.id);
    if (!previous || version(previous.row_version ?? '1') <= version(item.row_version ?? '1'))
      byId.set(item.id, {
        ...item,
        run_id: item.run_id ?? previous?.run_id,
        event_seq: item.event_seq ?? previous?.event_seq,
      });
  }
  return [...byId.values()].sort((left, right) => left.seq - right.seq);
}
const terminal = (item: AssistantOperation) => ['succeeded', 'failed', 'conflict'].includes(item.outcome);
const skillActivation = (item: unknown): item is AssistantSkillActivation => {
  if (!item || typeof item !== 'object' || Array.isArray(item)) return false;
  const value = item as Record<string, unknown>;
  return (
    [
      'activation_id',
      'skill_id',
      'name',
      'content_version',
      'package_checksum',
      'purpose',
      'plan_revision',
      'created_at',
    ].every((key) => typeof value[key] === 'string') &&
    ['manual', 'auto'].includes(String(value.source)) &&
    typeof value.active === 'boolean' &&
    (value.issue == null || typeof value.issue === 'string')
  );
};

export function mergeOperations(old: Record<string, AssistantOperation>, items: AssistantOperation[]) {
  const next = { ...old };
  for (const item of items) {
    const existing = next[item.id];
    if (
      existing &&
      (version(existing.state_version) > version(item.state_version) || (terminal(existing) && !terminal(item)))
    )
      continue;
    const receipts = new Map(
      [...(existing?.undo_receipts ?? []), ...(item.undo_receipts ?? [])].map((receipt) => [receipt.id, receipt]),
    );
    next[item.id] = {
      ...item,
      event_seq: existing?.event_seq ?? item.event_seq,
      result_seq: item.result_seq ?? existing?.result_seq,
      undo_receipts: [...receipts.values()].sort((a, b) => (version(a.id) < version(b.id) ? -1 : 1)),
    };
  }
  return next;
}

export function mergeTaskEntities(
  state: TaskProjection,
  snapshot: Pick<AssistantTaskSnapshot, 'snapshot_cursor' | 'operations' | 'trace_blocks'>,
): TaskProjection {
  if (state.deleted) return state;
  const operationSnapshotCursors = { ...state.operationSnapshotCursors };
  const traceSnapshotCursors = { ...state.traceSnapshotCursors };
  // 限页快照只覆盖实际返回的实体；历史页也不能降低已读实体的覆盖水位。
  for (const item of snapshot.operations)
    operationSnapshotCursors[item.id] = Math.max(operationSnapshotCursors[item.id] ?? -1, snapshot.snapshot_cursor);
  for (const block of snapshot.trace_blocks)
    traceSnapshotCursors[block.block_id] = Math.max(traceSnapshotCursors[block.block_id] ?? -1, snapshot.snapshot_cursor);
  return mergeTraceBlocks({
    ...state,
    operationSnapshotCursors,
    traceSnapshotCursors,
    operations: mergeOperations(state.operations, snapshot.operations),
  }, snapshot.trace_blocks);
}

export function mergeTaskSnapshot(
  state: TaskProjection,
  snapshot: AssistantTaskSnapshot,
  options?: { historical?: boolean },
): TaskProjection {
  if (
    state.deleted ||
    snapshot.conversation_id !== state.conversationId ||
    (!options?.historical && snapshot.snapshot_cursor < state.snapshotCursor)
  )
    return state;
  const tasks = { ...state.tasks };
  for (const item of snapshot.tasks) {
    if (!tasks[item.id] || version(tasks[item.id].row_version) <= version(item.row_version)) tasks[item.id] = item;
  }
  return mergeTaskEntities({
    ...state,
    snapshotCursor: Math.max(state.snapshotCursor, snapshot.snapshot_cursor),
    tasks,
  }, {
    snapshot_cursor: snapshot.snapshot_cursor,
    operations: [...snapshot.operations, ...snapshot.tasks.flatMap((task) => task.active_operation ? [task.active_operation] : [])],
    trace_blocks: snapshot.trace_blocks ?? [],
  });
}

/** Replay fences follow actual entity coverage; versions still protect omitted entities from rewind. */
export function applyTaskEvent(state: TaskProjection, event: AgentEvent): TaskProjection {
  if (state.deleted) return state;
  const p = event.payload;
  const snapshotCursor = event.event_type === 'trace.block' || event.event_type === 'trace.delta'
    ? state.traceSnapshotCursors[String(p.block_id)] ?? -1
    : event.event_type.startsWith('step.') || event.event_type === 'operation.undo.completed'
      ? state.operationSnapshotCursors[String(p.operation_id)] ?? -1
      : state.snapshotCursor;
  if (event.seq <= snapshotCursor) return state;
  if (event.event_type === 'conversation.deleted') return { ...state, deleted: true, tasks: {}, operations: {}, traceBlocks: {}, traceDeltas: {}, operationSnapshotCursors: {}, traceSnapshotCursors: {} };
  if (event.event_type === 'trace.block' || event.event_type === 'trace.delta') return applyTraceEvent(state, event);
  if (event.event_type === 'operation.undo.completed') {
    const receipt = p.receipt as AssistantUndoReceipt | undefined;
    const old = typeof p.operation_id === 'string' ? state.operations[p.operation_id] : undefined;
    if (
      !old ||
      !receipt ||
      typeof receipt.id !== 'string' ||
      receipt.operation_id !== old.id ||
      !Array.isArray(receipt.selected_keys) ||
      !receipt.selected_keys.every((key) => typeof key === 'string') ||
      !Array.isArray(receipt.result_refs)
    )
      return state;
    return {
      ...state,
      operations: mergeOperations(state.operations, [
        { ...old, undo_receipts: [...(old.undo_receipts ?? []), receipt] },
      ]),
    };
  }
  if (
    !['step.started', 'step.waiting_media', 'step.waiting_input', 'step.settling_result', 'step.finished', 'step.reconciling'].includes(
      event.event_type,
    )
  )
    return state;
  if (
    typeof p.operation_id !== 'string' ||
    typeof p.task_id !== 'string' ||
    typeof p.call_id !== 'string' ||
    typeof p.tool_name !== 'string' ||
    typeof p.trace_version !== 'string' ||
    !event.run_id
  )
    return state;
  const old = state.operations[p.operation_id];
  const outcome =
    event.event_type === 'step.finished'
      ? p.outcome
      : event.event_type === 'step.waiting_media'
        ? 'waiting_media'
        : event.event_type === 'step.waiting_input'
          ? 'waiting_input'
        : event.event_type === 'step.reconciling'
          ? 'reconciling'
          : 'running';
  if (!['running', 'waiting_media', 'waiting_input', 'reconciling', 'succeeded', 'failed', 'conflict'].includes(String(outcome)))
    return state;
  const phase = ['tool_running', 'waiting_media', 'waiting_input', 'settling_result', 'reconciling', 'terminal'].includes(
    String(p.phase),
  )
    ? p.phase
    : 'tool_running';
  const trace_visibility =
    p.trace_visibility === ToolTraceVisibility.HIDDEN
      ? ToolTraceVisibility.HIDDEN
      : ToolTraceVisibility.VISIBLE;
  const item: AssistantOperation = {
    id: p.operation_id,
    task_id: p.task_id,
    run_id: event.run_id,
    call_id: p.call_id,
    turn_id: typeof p.turn_id === 'string' ? p.turn_id : (old?.turn_id ?? null),
    tool_name: p.tool_name,
    item_seq: typeof p.item_seq === 'number' ? p.item_seq : (old?.item_seq ?? null),
    trace_visibility,
    event_seq: old?.event_seq ?? event.seq,
    result_seq: event.event_type === 'step.finished' ? event.seq : (old?.result_seq ?? null),
    label: typeof p.label === 'string' ? p.label : (old?.label ?? ''),
    action: old?.action,
    target: old?.target,
    title: old?.title,
    tool_version: old?.tool_version ?? '1',
    plan_revision: typeof p.plan_revision === 'string' ? p.plan_revision : (old?.plan_revision ?? '1'),
    state_version: p.trace_version,
    phase: phase as AssistantOperation['phase'],
    outcome: outcome as AssistantOperation['outcome'],
    result_refs: Array.isArray(p.result_refs)
      ? p.result_refs.filter(
          (item): item is Record<string, unknown> => !!item && typeof item === 'object' && !Array.isArray(item),
        )
      : (old?.result_refs ?? []),
    skill_activations: Array.isArray(p.skill_activations)
      ? p.skill_activations.filter(skillActivation)
      : (old?.skill_activations ?? []),
    undo_receipts: old?.undo_receipts ?? [],
    error:
      p.error && typeof p.error === 'object' && 'code' in p.error && typeof p.error.code === 'string'
        ? { code: p.error.code }
        : null,
    created_at: old?.created_at ?? event.created_at,
    finished_at: event.event_type === 'step.finished' ? event.created_at : null,
  };
  return { ...state, operations: mergeOperations(state.operations, [item]) };
}

export function selectedTask(tasks: Record<string, AssistantTask>) {
  const items = Object.values(tasks).sort((a, b) => (version(a.id) < version(b.id) ? 1 : -1));
  return (
    items.find((task) => ['running', 'waiting_media', 'waiting_input'].includes(task.status)) ??
    items.find((task) => ['queued', 'paused_error', 'paused_budget', 'stopped'].includes(task.status)) ??
    null
  );
}
