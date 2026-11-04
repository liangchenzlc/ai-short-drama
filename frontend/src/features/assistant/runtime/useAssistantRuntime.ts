import { assistantApi } from '@host/api/assistant/conversations/requests';
import { ApiError, errorMessage, http } from '@host/api/http';
import {
  addDelta,
  recoverDeltaReplay,
  parseAgentEvent,
  streamingParts,
  sseControl,
  takeSseFrames,
} from '@host/features/assistant/runtime/agent-events';
import { notifyAssistantBusinessChange } from '@host/features/assistant/runtime/assistant-business-events';
import {
  applyTaskEvent,
  applyRunRecoveryEvent,
  emptyTaskProjection,
  mergeAssistantRun,
  mergeAssistantMessages as mergeMessages,
  mergeTaskEntities,
  mergeTaskSnapshot,
  operationPageStart,
  selectedTask,
} from '@host/features/assistant/runtime/assistant-task-state';
import type {
  AssistantControlAccepted,
  AssistantTaskAccepted,
  AssistantTaskInputState,
  AssistantPendingQuestion,
  AssistantTaskSnapshot,
} from '@host/types/assistant/api';
import type { AgentEvent, AgentMessage, AgentRun } from '@host/types/assistant/engine';
import type { DeltaChunk, DeltaRecovery } from '@host/types/assistant/runtime/agent-events';
import type { OperationPagination, RunTracePagination } from '@host/types/assistant/runtime/assistant-task-state';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

function messageTextParts(value: unknown): NonNullable<AgentMessage['text_parts']> {
  if (!Array.isArray(value)) return [];
  return value.filter(
    (part): part is NonNullable<AgentMessage['text_parts']>[number] =>
      !!part &&
      typeof part === 'object' &&
      !Array.isArray(part) &&
      typeof (part as Record<string, unknown>).turn_id === 'string' &&
      Number.isSafeInteger((part as Record<string, unknown>).event_seq) &&
      Number((part as Record<string, unknown>).event_seq) > 0 &&
      typeof (part as Record<string, unknown>).content === 'string',
  );
}

export function useAssistantRuntime(conversationId: string) {
  const [projection, setProjection] = useState(() => emptyTaskProjection(conversationId));
  const [conversationTitle, setConversationTitle] = useState<{ title: string; row_version: string } | null>(null);
  const traceBlocks = useMemo(() => Object.values(projection.traceBlocks), [projection.traceBlocks]);
  const [inputs, setInputs] = useState<AssistantTaskInputState[]>([]);
  const [hasMoreTasks, setHasMoreTasks] = useState(false);
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const [total, setTotal] = useState(0);
  const [queuedRuns, setQueuedRuns] = useState<AgentRun[]>([]);
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [pendingQuestions, setPendingQuestions] = useState<AssistantPendingQuestion[]>([]);
  const [hasMoreTrace, setHasMoreTrace] = useState(false);
  const tracePages = useRef<Record<string, RunTracePagination>>({});
  const operationPages = useRef<Record<string, OperationPagination>>({});
  const taskBeforeId = useRef<string | null>(null);
  const [run, setRun] = useState<AgentRun | null>(null);
  const [loading, setLoading] = useState(true);
  const [olderLoading, setOlderLoading] = useState(false);
  const [error, setError] = useState('');
  const [connection, setConnection] = useState<'connecting' | 'live' | 'reconnecting' | 'error'>('connecting');
  const [deltas, updateDeltas] = useState<Record<string, DeltaChunk[]>>({});
  const deltaState = useRef(deltas);
  const deltaRecovery = useRef<DeltaRecovery | null>(null);
  const setDeltas = (next: Record<string, DeltaChunk[]> | ((old: Record<string, DeltaChunk[]>) => Record<string, DeltaChunk[]>)) => {
    deltaState.current = typeof next === 'function' ? next(deltaState.current) : next;
    updateDeltas(deltaState.current);
  };
  function retirePersistedDeltas(items: AgentMessage[]) {
    const prune = (state: Record<string, DeltaChunk[]>) => {
      const next = { ...state };
      for (const message of items) {
        if (message.role !== 'assistant' || !message.run_id || !message.text_parts?.length) continue;
        const turns = new Set(message.text_parts.map((part) => part.turn_id));
        next[message.run_id] = (next[message.run_id] ?? []).filter((chunk) => !turns.has(chunk.turn));
      }
      return next;
    };
    setDeltas(prune);
    if (deltaRecovery.current) {
      deltaRecovery.current.held = prune(deltaRecovery.current.held);
      deltaRecovery.current.replay = prune(deltaRecovery.current.replay);
    }
  }
  const [transportError, setTransportError] = useState('');
  const currentDeltas = useMemo(() => run ? streamingParts(deltas[run.id] ?? []) : [], [run?.id, deltas]);
  const [streamRevision, setStreamRevision] = useState(0);
  const [streamReady, setStreamReady] = useState(false);
  const [accessEnded, setAccessEnded] = useState(false);
  const accessEndedRef = useRef(false);
  const alive = useRef(true);
  const cursor = useRef(0);
  const snapshotCursor = useRef(-1);
  const runRecoveryEvents = useRef<Record<string, AgentEvent>>({});
  const initialized = useRef(false);
  const refreshRequest = useRef(0);
  const readController = useRef<AbortController | null>(null);
  const streamController = useRef<AbortController | null>(null);
  const count = useRef(0);
  count.current = messages.length;
  const businessReceipts = useRef(new Map<string, string>());
  useEffect(() => {
    const projects = new Map<string, Set<string>>();
    for (const operation of Object.values(projection.operations)) {
      if (
        operation.outcome !== 'succeeded' ||
        !['workbench__apply_standard_patch', 'workbench__submit_generation_batch', 'workbench__set_image_group_primary'].includes(operation.tool_name)
      )
        continue;
      const target = projection.tasks[operation.task_id]?.execution_target;
      if (target?.mode !== 'standard') continue;
      const signature = JSON.stringify([operation.state_version, operation.result_refs, operation.undo_receipts]);
      if (businessReceipts.current.get(operation.id) === signature) continue;
      businessReceipts.current.set(operation.id, signature);
      const kinds = projects.get(target.project_id) ?? new Set<string>();
      operation.result_refs.forEach((ref) => {
        if (typeof ref.kind === 'string') kinds.add(ref.kind);
      });
      projects.set(target.project_id, kinds);
    }
    projects.forEach((kinds, projectId) => notifyAssistantBusinessChange(projectId, [...kinds]));
  }, [projection.operations, projection.tasks]);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      readController.current?.abort();
    };
  }, []);
  const endAccess = useCallback(
    (message: string) => {
      accessEndedRef.current = true;
      setAccessEnded(true);
      readController.current?.abort();
      streamController.current?.abort();
      initialized.current = false;
      snapshotCursor.current = -1;
      runRecoveryEvents.current = {};
      setStreamReady(false);
      setMessages([]);
      setTotal(0);
      setRun(null);
      setQueuedRuns([]);
      setRuns([]);
      setPendingQuestions([]);
      setHasMoreTrace(false);
      setHasMoreTasks(false);
      tracePages.current = {};
      operationPages.current = {};
      deltaRecovery.current = null;
      taskBeforeId.current = null;
      setDeltas({});
      setLoading(false);
      setOlderLoading(false);
      setProjection(emptyTaskProjection(conversationId));
      setInputs([]);
      setConnection('error');
      setError(message);
      setTransportError('');
    },
    [conversationId],
  );
  const rememberTraceHistory = useCallback((snapshot: AssistantTaskSnapshot) => {
    const ids = new Set([...snapshot.runs.map(item => item.id), ...snapshot.trace_blocks.map(item => item.run_id)]);
    for (const id of ids) {
      tracePages.current[id] ??= { has_more: snapshot.has_more_trace, next_before_seq: snapshot.trace_before_seq };
    }
    setHasMoreTrace(Object.values(tracePages.current).some(page => page.has_more));
  }, []);
  async function fillOperationPages(snapshot: AssistantTaskSnapshot, signal?: AbortSignal, valid = () => true) {
      if (snapshot.has_more_operations) {
        for (const task of snapshot.tasks) {
          const missing = operationPageStart(snapshot, task.id, operationPages.current[task.id]);
          if (!missing) continue;
          let after = missing.afterId;
          let more = true;
          while (more && !signal?.aborted && valid() && !accessEndedRef.current) {
            const page = await assistantApi.operations(task.id, after, signal);
            if (!alive.current || signal?.aborted || !valid()) return;
            setProjection((old) => mergeTaskEntities(old, {
              snapshot_cursor: page.snapshot_cursor, operations: page.items, trace_blocks: [],
            }));
            more = page.has_more;
            after = page.next_after_id ?? page.items.at(-1)?.id ?? after;
            operationPages.current[task.id] = { afterId: after ?? missing.afterId, complete: !more };
          }
        }
      } else {
        for (const task of snapshot.tasks) operationPages.current[task.id] = {
          afterId: snapshot.operations.filter((item) => item.task_id === task.id).at(-1)?.id,
          complete: true,
        };
      }
  }
  const reload = useCallback(async () => {
    if (accessEndedRef.current) return;
    const request = ++refreshRequest.current;
    readController.current?.abort();
    const controller = new AbortController();
    readController.current = controller;
    try {
      const initial = !initialized.current;
      const snapshot = await assistantApi.taskState(conversationId, controller.signal);
      if (!alive.current || accessEndedRef.current || controller.signal.aborted || request !== refreshRequest.current)
        return;
      if (
        snapshot.conversation_id !== conversationId ||
        !Number.isSafeInteger(snapshot.snapshot_cursor) ||
        snapshot.snapshot_cursor < 0
      )
        throw new Error('对话状态不完整，请重新读取。');
      if (snapshot.snapshot_cursor < snapshotCursor.current) return;
      if (initial) {
        const conversation = await assistantApi.globalConversation(conversationId, controller.signal);
        if (!alive.current || accessEndedRef.current || controller.signal.aborted || request !== refreshRequest.current)
          return;
        setConversationTitle({ title: conversation.title, row_version: conversation.row_version });
      }
      snapshotCursor.current = snapshot.snapshot_cursor;
      retirePersistedDeltas(snapshot.messages.items);
      if (initial) {
        cursor.current =
          Number.isSafeInteger(snapshot.resume_cursor) &&
          snapshot.resume_cursor >= 0 &&
          snapshot.resume_cursor <= snapshot.snapshot_cursor
            ? snapshot.resume_cursor
            : 0;
        initialized.current = true;
        const recovery = deltaRecovery.current;
        if (recovery) {
          recovery.watermark = Math.max(recovery.watermark, snapshot.snapshot_cursor);
          setDeltas(recoverDeltaReplay(recovery.held, recovery.replay, cursor.current, recovery.watermark));
          if (cursor.current >= recovery.watermark) deltaRecovery.current = null;
        }
        setStreamReady(true);
      }
      setProjection((old) => mergeTaskSnapshot(old, snapshot));
      setInputs(snapshot.inputs);
      setRuns(old => {
        const byId = new Map(old.map(item => [item.id, item]));
        for (const item of snapshot.runs)
          byId.set(item.id, mergeAssistantRun(byId.get(item.id) ?? null, item, snapshot.snapshot_cursor, runRecoveryEvents.current[item.id]));
        return [...byId.values()];
      });
      setPendingQuestions([...new Map((snapshot.pending_questions ?? []).map(item => [item.operation_id, item])).values()]);
      // 普通刷新保留进度；流缺口重建页边界，补回断流期间旧 Task 的新 Run，已读块继续保留。
      if (initial) {
        tracePages.current = {};
        taskBeforeId.current = oldestTaskId(snapshot.tasks);
        setHasMoreTasks(snapshot.has_more_tasks);
      }
      rememberTraceHistory(snapshot);
      const activeRun = snapshot.runs.find((item) => ['running', 'waiting_generation', 'waiting_input'].includes(item.status));
      const queued = snapshot.runs.filter((item) => item.status === 'queued');
      setMessages((old) => mergeMessages(old, snapshot.messages.items));
      setTotal(snapshot.messages.total);
      const selectedRun = activeRun ?? queued[0] ?? snapshot.runs.at(-1);
      setRun(old => selectedRun ? mergeAssistantRun(old, selectedRun, snapshot.snapshot_cursor, runRecoveryEvents.current[selectedRun.id]) : null);
      setError('');
      setQueuedRuns(queued);
      if (!activeRun && !deltaRecovery.current) setDeltas({});
      await fillOperationPages(snapshot, controller.signal, () => request === refreshRequest.current);
      return snapshot;
    } catch (cause) {
      if (!alive.current || controller.signal.aborted) return;
      if (cause instanceof ApiError && cause.status && [401, 403, 404].includes(cause.status))
        endAccess('对话访问已结束，请核对登录与项目权限。');
      else {
        setError(errorMessage(cause));
        if (!initialized.current) setConnection('error');
      }
    } finally {
      if (alive.current && !controller.signal.aborted) setLoading(false);
    }
  }, [conversationId, endAccess, rememberTraceHistory]);
  useEffect(() => {
    void reload();
  }, [reload]);
  useEffect(() => {
    const refresh = () => void reload();
    window.addEventListener('agent-run-updated', refresh);
    return () => window.removeEventListener('agent-run-updated', refresh);
  }, [reload]);
  async function older() {
    if (olderLoading || accessEndedRef.current || count.current >= total) return;
    setOlderLoading(true);
    try {
      const page = await assistantApi.taskMessages(conversationId, count.current, undefined);
      if (alive.current && !accessEndedRef.current) {
        setMessages((old) => mergeMessages(old, page.items));
        setTotal(page.total);
      }
    } catch (cause) {
      if (alive.current) {
        if (cause instanceof ApiError && cause.status && [401, 403, 404].includes(cause.status))
          endAccess('对话访问已结束，请核对登录与项目权限。');
        else setError(errorMessage(cause));
      }
    } finally {
      if (alive.current) setOlderLoading(false);
    }
  }
  function accept(result: AssistantTaskAccepted) {
    if (!alive.current || accessEndedRef.current) return;
    setMessages((old) => mergeMessages(old, [result.message]));
    setProjection((old) =>
      mergeTaskSnapshot(
        old,
        {
          conversation_id: conversationId,
          snapshot_cursor: result.cursor,
          tasks: [result.task],
          operations: result.task.active_operation ? [result.task.active_operation] : [],
        } as Parameters<typeof mergeTaskSnapshot>[1],
        { historical: true },
      ),
    );
    if (result.cursor >= snapshotCursor.current) {
      snapshotCursor.current = result.cursor;
      if (result.run.status === 'queued' && run && !['succeeded', 'failed', 'cancelled'].includes(run.status))
        setQueuedRuns((old) => [...old.filter((item) => item.id !== result.run.id), result.run]);
      else setRun(old => mergeAssistantRun(old, result.run, result.cursor, runRecoveryEvents.current[result.run.id]));
    }
    void reload();
  }
  function acceptControl(result: AssistantControlAccepted) {
    if (!alive.current || accessEndedRef.current) return;
    setProjection((old) =>
      mergeTaskSnapshot(old, {
        conversation_id: conversationId,
        snapshot_cursor: result.cursor,
        tasks: [result.task],
        operations: result.task.active_operation ? [result.task.active_operation] : [],
      } as Parameters<typeof mergeTaskSnapshot>[1]),
    );
    if (result.cursor >= snapshotCursor.current) {
      snapshotCursor.current = result.cursor;
      setRun(old => mergeAssistantRun(old, result.run, result.cursor, runRecoveryEvents.current[result.run.id]));
    }
    void reload();
  }

  useEffect(() => {
    if (!streamReady) return;
    const controller = new AbortController();
    streamController.current = controller;
    let refreshTimer: ReturnType<typeof setTimeout> | undefined;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
    const scheduleRefresh = () => {
      if (refreshTimer) return;
      refreshTimer = setTimeout(() => {
        refreshTimer = undefined;
        if (!controller.signal.aborted) void reload();
      }, 100);
    };
    function resync() {
      if (controller.signal.aborted || accessEndedRef.current) return;
      controller.abort();
      clearTimeout(refreshTimer);
      clearTimeout(reconnectTimer);
      const held = deltaState.current;
      deltaRecovery.current = { held, replay: {}, watermark: Math.max(cursor.current, ...Object.values(held).flatMap((chunks) => chunks.map((chunk) => chunk.seq))) };
      operationPages.current = {};
      initialized.current = false;
      cursor.current = 0;
      setStreamReady(false);
      setConnection('reconnecting');
      setStreamRevision((value) => value + 1);
      void reload();
    }
    function receive(event: AgentEvent) {
      if (controller.signal.aborted || !alive.current || accessEndedRef.current || event.seq <= cursor.current) return;
      if (event.seq > cursor.current + 1) {
        resync();
        return;
      }
      if (event.event_type === 'conversation.deleted') {
        endAccess('对话访问已结束，请核对登录与项目权限。');
        return;
      }
      cursor.current = event.seq;
      setProjection((old) => applyTaskEvent(old, event));
      if (event.run_id && ['run.recovery', 'run.finished'].includes(event.event_type) && event.seq > snapshotCursor.current) {
        runRecoveryEvents.current[event.run_id] = event;
        setRun(old => old ? applyRunRecoveryEvent(old, event) : null);
        setRuns(old => old.map(item => applyRunRecoveryEvent(item, event)));
      }
      if (
        event.event_type === 'conversation.title.updated' &&
        typeof event.payload.title === 'string' &&
        typeof event.payload.row_version === 'string'
      ) {
        setConversationTitle({ title: event.payload.title, row_version: event.payload.row_version });
      }
      if (event.event_type === 'trace.block' || event.event_type === 'trace.delta') {
        finishDeltaRecovery();
        return;
      }
      if (event.event_type === 'assistant.delta' && event.run_id) {
        const recovery = deltaRecovery.current;
        if (recovery) {
          recovery.replay[event.run_id] = addDelta(recovery.replay[event.run_id] ?? [], event);
          setDeltas(recoverDeltaReplay(recovery.held, recovery.replay, cursor.current, recovery.watermark));
        } else setDeltas((old) => ({ ...old, [event.run_id!]: addDelta(old[event.run_id!] ?? [], event) }));
      } else {
        const completedTextParts = messageTextParts(event.payload.text_parts);
        if (
          ['message.created', 'message.updated'].includes(event.event_type) &&
          event.payload.role === 'assistant' &&
          event.run_id &&
          typeof event.payload.id === 'string' &&
          Number.isSafeInteger(event.payload.seq) &&
          typeof event.payload.content === 'string'
        ) {
          const payload = event.payload;
          setMessages((old) =>
            mergeMessages(old, [
              {
                id: payload.id as string,
                run_id: event.run_id,
                seq: payload.seq as number,
                item_seq: typeof payload.item_seq === 'number' ? payload.item_seq : null,
                event_seq: event.seq,
                text_parts: completedTextParts,
                row_version: typeof payload.row_version === 'string' ? payload.row_version : undefined,
                kind: ['reply', 'narration', 'notice', 'tool_answer'].includes(String(payload.kind))
                  ? (payload.kind as AgentMessage['kind'])
                  : 'reply',
                role: 'assistant',
                content: payload.content as string,
                interrupted: payload.interrupted === true,
                references: Array.isArray(payload.references) ? payload.references : [],
                artifacts: Array.isArray(payload.artifacts) ? payload.artifacts : [],
                created_at: event.created_at,
              },
            ]),
          );
        }
        if (event.event_type === 'artifact.script.applied' && typeof event.payload.project_id === 'string')
          notifyAssistantBusinessChange(event.payload.project_id, ['script']);
        if (event.event_type.startsWith('artifact')) window.dispatchEvent(new Event('agent-artifacts-updated'));
        if (['message.created', 'message.updated'].includes(event.event_type) && event.payload.role === 'assistant' && event.run_id && completedTextParts.length > 0)
          retirePersistedDeltas([{ role: 'assistant', run_id: event.run_id, text_parts: completedTextParts } as AgentMessage]);
        scheduleRefresh();
      }
      finishDeltaRecovery();
    }
    function finishDeltaRecovery() {
      const recovery = deltaRecovery.current;
      if (recovery && cursor.current >= recovery.watermark) {
        setDeltas(recovery.replay);
        deltaRecovery.current = null;
      }
    }
    async function connect(attempt = 0) {
      if (controller.signal.aborted || accessEndedRef.current) return;
      setConnection(attempt ? 'reconnecting' : 'connecting');
      const base = String(http.defaults.baseURL ?? '/api/v1').replace(/\/$/, '');
      try {
        const response = await fetch(
          `${base}/assistant/global/conversations/${encodeURIComponent(conversationId)}/events?${new URLSearchParams({ cursor: String(cursor.current) }).toString()}`,
          {
            signal: controller.signal,
            credentials: 'include',
            headers: { Accept: 'text/event-stream' },
          },
        );
        if (controller.signal.aborted || !alive.current || accessEndedRef.current) return;
        if (response.status === 409) {
          const problem = await response.json().catch(() => null);
          if (problem?.error?.code === 'agent_event_resync_required') {
            resync();
            return;
          }
        }
        if (!response.ok || !response.body) {
          if (response.status === 401) window.dispatchEvent(new Event('session-expired'));
          if (response.status === 401 || response.status === 403 || response.status === 404) {
            endAccess('对话连接不可用，请核对登录与项目权限。');
            return;
          }
          throw new Error(`stream_http_${response.status}`);
        }
        setConnection('live');
        setTransportError('');
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        while (!controller.signal.aborted) {
          const chunk = await reader.read();
          buffer += decoder.decode(chunk.value, { stream: !chunk.done });
          const parsed = takeSseFrames(buffer);
          buffer = parsed.rest;
          for (const frame of parsed.frames) {
            const control = sseControl(frame);
            if (control === 'access-ended') {
              endAccess('对话访问已结束，请核对登录与项目权限。');
              return;
            }
            if (control === 'resync') {
              resync();
              return;
            }
            if (control === 'retry') throw new Error('stream_retry');
            const event = parseAgentEvent(frame);
            if (event) receive(event);
          }
          if (chunk.done) break;
        }
        if (!controller.signal.aborted) setTransportError('对话连接已断开，正在按已读位置重新连接。');
      } catch (cause) {
        if (!controller.signal.aborted && !accessEndedRef.current) {
          const status = cause instanceof Error ? /^stream_http_(\d{3})$/.exec(cause.message)?.[1] : undefined;
          setTransportError(status ? `对话连接暂不可用（HTTP ${status}），正在重试。`
            : cause instanceof Error && cause.message === 'stream_retry' ? '服务端暂时无法推送，正在按已读位置重试。'
            : '对话连接发生网络异常，正在按已读位置重试。');
        }
      }
      if (controller.signal.aborted || accessEndedRef.current) return;
      setConnection('reconnecting');
      void reload();
      reconnectTimer = setTimeout(() => void connect(attempt + 1), Math.min(10_000, 1500 * (attempt + 1)));
    }
    void connect();
    return () => {
      controller.abort();
      clearTimeout(refreshTimer);
      clearTimeout(reconnectTimer);
    };
  }, [conversationId, reload, streamRevision, streamReady, endAccess]);
  async function olderTasks() {
    const beforeTaskId = taskBeforeId.current;
    if (!hasMoreTasks || !beforeTaskId || olderLoading || accessEndedRef.current) return;
    const history = tracePages.current;
    setOlderLoading(true);
    try {
      const snapshot = await assistantApi.taskState(conversationId, undefined, beforeTaskId);
      if (alive.current && !accessEndedRef.current && history === tracePages.current) {
        setProjection((old) => mergeTaskSnapshot(old, snapshot, { historical: true }));
        await fillOperationPages(snapshot, undefined, () => history === tracePages.current && !accessEndedRef.current);
        if (history !== tracePages.current || accessEndedRef.current) return;
        setRuns(old => {
          const byId = new Map(old.map(item => [item.id, item]));
          for (const item of snapshot.runs)
            byId.set(item.id, mergeAssistantRun(byId.get(item.id) ?? null, item, snapshot.snapshot_cursor, runRecoveryEvents.current[item.id]));
          return [...byId.values()];
        });
        rememberTraceHistory(snapshot);
        taskBeforeId.current = oldestTaskId(snapshot.tasks);
        setHasMoreTasks(snapshot.has_more_tasks);
      }
    } catch (cause) {
      if (alive.current && !accessEndedRef.current && history === tracePages.current) {
        if (cause instanceof ApiError && cause.status && [401, 403, 404].includes(cause.status))
          endAccess('对话访问已结束，请核对登录与项目权限。');
        else setError(errorMessage(cause));
      }
    } finally {
      if (alive.current) setOlderLoading(false);
    }
  }
  async function olderTrace() {
    if (olderLoading || accessEndedRef.current || !hasMoreTrace) return;
    const history = tracePages.current;
    setOlderLoading(true);
    try {
      const pending = Object.entries(history).filter(([, page]) => page.has_more);
      const pages = await Promise.all(pending.map(([id, page]) => assistantApi.tracePage(conversationId, id, page.next_before_seq ?? undefined)));
      // resync 已换过页边界时，旧请求不得推进新一轮历史游标。
      if (!alive.current || accessEndedRef.current || history !== tracePages.current) return;
      setProjection(old => pages.reduce((state, page) => mergeTaskEntities(state, {
        snapshot_cursor: page.snapshot_cursor, operations: [], trace_blocks: page.items,
      }), old));
      pages.forEach((page, index) => { tracePages.current[pending[index][0]] = { has_more: page.has_more, next_before_seq: page.next_before_seq }; });
      setHasMoreTrace(Object.values(tracePages.current).some(page => page.has_more));
    } catch (cause) {
      if (alive.current && !accessEndedRef.current && history === tracePages.current) {
        if (cause instanceof ApiError && cause.status && [401, 403, 404].includes(cause.status))
          endAccess('对话访问已结束，请核对登录与项目权限。');
        else setError(`读取早期轨迹失败：${errorMessage(cause)}`);
      }
    }
    finally { if (alive.current) setOlderLoading(false); }
  }
  async function refreshQuestion(operationId: string) {
    const snapshot = await reload();
    if (!snapshot) return null;
    return snapshot.pending_questions?.find(item => item.operation_id === operationId) ?? null;
  }
  return {
    messages,
    conversationTitle,
    total,
    run,
    queuedRuns,
    loading,
    olderLoading,
    error: error || (run && !['succeeded', 'failed', 'cancelled'].includes(run.status) ? transportError : ''),
    connection,
    reload,
    older,
    accept,
    acceptControl,
    task: selectedTask(projection.tasks),
    tasks: Object.values(projection.tasks),
    operations: Object.values(projection.operations),
    traceBlocks,
    pendingQuestions,
    refreshQuestion,
    runs,
    hasMoreTrace,
    olderTrace,
    inputs,
    hasMoreTasks,
    olderTasks,
    setRun: (next: AgentRun) => {
      if (alive.current && !accessEndedRef.current) setRun(next);
    },
    accessEnded,
    reconnect: () => {
      streamController.current?.abort();
      const held = deltaState.current;
      deltaRecovery.current = { held, replay: {}, watermark: Math.max(cursor.current, ...Object.values(held).flatMap((chunks) => chunks.map((chunk) => chunk.seq))) };
      operationPages.current = {};
      accessEndedRef.current = false;
      setAccessEnded(false);
      initialized.current = false;
      cursor.current = 0;
      setStreamReady(false);
      setStreamRevision((value) => value + 1);
      void reload();
    },
    streamingParts: currentDeltas,
  };
}

function oldestTaskId(tasks: AssistantTaskSnapshot['tasks']) {
  return tasks.map(task => task.id).sort((a, b) => (BigInt(a) < BigInt(b) ? -1 : 1))[0] ?? null;
}
