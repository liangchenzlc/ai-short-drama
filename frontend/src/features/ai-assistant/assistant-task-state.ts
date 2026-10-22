import type { AgentEvent, AgentMessage } from '../../api/types/agents';
import type { AssistantOperation, AssistantSkillActivation, AssistantTask, AssistantTaskSnapshot, AssistantUndoReceipt } from '../../api/types/assistant';

export interface TaskProjection {
  conversationId: string; snapshotCursor: number; deleted: boolean;
  tasks: Record<string, AssistantTask>; operations: Record<string, AssistantOperation>;
}
export const emptyTaskProjection = (conversationId: string): TaskProjection => ({
  conversationId, snapshotCursor: -1, deleted: false, tasks: {}, operations: {},
});
const version = (value: string) => /^\d+$/.test(value) ? BigInt(value) : -1n;
export function mergeAssistantMessages(old: AgentMessage[], items: AgentMessage[]) {
  const byId = new Map(old.map(item => [item.id, item]));
  for (const item of items) {
    if (item.role !== 'user' && item.role !== 'assistant') continue;
    const previous = byId.get(item.id);
    if (!previous || version(previous.row_version ?? '1') <= version(item.row_version ?? '1')) byId.set(item.id, item);
  }
  return [...byId.values()].sort((left, right) => left.seq - right.seq);
}
const terminal = (item: AssistantOperation) => ['succeeded', 'failed', 'conflict'].includes(item.outcome);
const skillActivation = (item: unknown): item is AssistantSkillActivation => {
  if (!item || typeof item !== 'object' || Array.isArray(item)) return false;
  const value = item as Record<string, unknown>;
  return ['activation_id', 'skill_id', 'name', 'content_version', 'package_checksum', 'purpose', 'plan_revision', 'created_at'].every(key => typeof value[key] === 'string')
    && ['manual', 'auto'].includes(String(value.source)) && typeof value.active === 'boolean'
    && (value.issue == null || typeof value.issue === 'string');
};

export function mergeOperations(old: Record<string, AssistantOperation>, items: AssistantOperation[]) {
  const next = { ...old };
  for (const item of items) {
    const existing = next[item.id];
    if (existing && (version(existing.state_version) > version(item.state_version)
      || terminal(existing) && !terminal(item))) continue;
    const receipts = new Map([...(existing?.undo_receipts ?? []), ...(item.undo_receipts ?? [])].map(receipt => [receipt.id, receipt]));
    next[item.id] = { ...item, undo_receipts: [...receipts.values()].sort((a, b) => version(a.id) < version(b.id) ? -1 : 1) };
  }
  return next;
}

export function mergeTaskSnapshot(state: TaskProjection, snapshot: AssistantTaskSnapshot, options?: { historical?: boolean }): TaskProjection {
  if (state.deleted || snapshot.conversation_id !== state.conversationId || !options?.historical && snapshot.snapshot_cursor < state.snapshotCursor) return state;
  const tasks = { ...state.tasks };
  for (const item of snapshot.tasks) {
    if (!tasks[item.id] || version(tasks[item.id].row_version) <= version(item.row_version)) tasks[item.id] = item;
  }
  return { ...state, snapshotCursor: Math.max(state.snapshotCursor, snapshot.snapshot_cursor), tasks, operations: mergeOperations(state.operations, snapshot.operations) };
}

/** Older replay for text may precede the snapshot; it must not rewind trace state. */
export function applyTaskEvent(state: TaskProjection, event: AgentEvent): TaskProjection {
  if (state.deleted || event.seq <= state.snapshotCursor) return state;
  if (event.event_type === 'conversation.deleted') return { ...state, deleted: true, tasks: {}, operations: {} };
  if (event.event_type === 'operation.undo.completed') {
    const p = event.payload, receipt = p.receipt as AssistantUndoReceipt | undefined;
    const old = typeof p.operation_id === 'string' ? state.operations[p.operation_id] : undefined;
    if (!old || !receipt || typeof receipt.id !== 'string' || receipt.operation_id !== old.id
      || !Array.isArray(receipt.selected_keys) || !receipt.selected_keys.every(key => typeof key === 'string') || !Array.isArray(receipt.result_refs)) return state;
    return { ...state, operations: mergeOperations(state.operations, [{ ...old, undo_receipts: [...(old.undo_receipts ?? []), receipt] }]) };
  }
  if (!['step.started', 'step.waiting_media', 'step.settling_result', 'step.finished', 'step.reconciling'].includes(event.event_type)) return state;
  const p = event.payload;
  if (typeof p.operation_id !== 'string' || typeof p.task_id !== 'string' || typeof p.call_id !== 'string'
    || typeof p.tool_name !== 'string' || typeof p.trace_version !== 'string' || !event.run_id) return state;
  const old = state.operations[p.operation_id];
  const outcome = event.event_type === 'step.finished' ? p.outcome : event.event_type === 'step.waiting_media' ? 'waiting_media'
    : event.event_type === 'step.reconciling' ? 'reconciling' : 'running';
  if (!['running', 'waiting_media', 'reconciling', 'succeeded', 'failed', 'conflict'].includes(String(outcome))) return state;
  const phase = ['tool_running', 'waiting_media', 'settling_result', 'reconciling', 'terminal'].includes(String(p.phase)) ? p.phase : 'tool_running';
  const item: AssistantOperation = {
    id: p.operation_id, task_id: p.task_id, run_id: event.run_id, call_id: p.call_id, tool_name: p.tool_name,
    tool_version: old?.tool_version ?? '1', plan_revision: typeof p.plan_revision === 'string' ? p.plan_revision : old?.plan_revision ?? '1',
    state_version: p.trace_version, phase: phase as AssistantOperation['phase'], outcome: outcome as AssistantOperation['outcome'],
    result_refs: Array.isArray(p.result_refs) ? p.result_refs.filter((item): item is Record<string, unknown> => !!item && typeof item === 'object' && !Array.isArray(item)) : old?.result_refs ?? [],
    skill_activations: Array.isArray(p.skill_activations) ? p.skill_activations.filter(skillActivation) : old?.skill_activations ?? [],
    undo_receipts: old?.undo_receipts ?? [],
    error: p.error && typeof p.error === 'object' && 'code' in p.error && typeof p.error.code === 'string' ? { code: p.error.code } : null,
    created_at: old?.created_at ?? event.created_at, finished_at: event.event_type === 'step.finished' ? event.created_at : null,
  };
  return { ...state, operations: mergeOperations(state.operations, [item]) };
}

export function selectedTask(tasks: Record<string, AssistantTask>) {
  const items = Object.values(tasks).sort((a, b) => version(a.id) < version(b.id) ? 1 : -1);
  return items.find(task => ['running', 'waiting_media'].includes(task.status))
    ?? items.find(task => ['queued', 'stopped', 'paused_error', 'paused_budget'].includes(task.status)) ?? null;
}
