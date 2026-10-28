import { assistantApi } from '@host/api/assistant/conversations/requests';
import { ApiError, errorMessage, http } from '@host/api/http';
import {
  addDelta,
  latestDelta,
  parseAgentEvent,
  sseControl,
  takeSseFrames,
} from '@host/features/assistant/runtime/agent-events';
import { notifyAssistantBusinessChange } from '@host/features/assistant/runtime/assistant-business-events';
import {
  applyTaskEvent,
  emptyTaskProjection,
  mergeAssistantMessages as mergeMessages,
  mergeOperations,
  mergeTaskSnapshot,
  selectedTask,
} from '@host/features/assistant/runtime/assistant-task-state';
import type {
  AssistantControlAccepted,
  AssistantTaskAccepted,
  AssistantTaskInputState,
} from '@host/types/assistant/api';
import type { AgentEvent, AgentMessage, AgentRun } from '@host/types/assistant/engine';
import type { DeltaChunk } from '@host/types/assistant/runtime/agent-events';
import { useCallback, useEffect, useRef, useState } from 'react';
export function useAssistantRuntime(conversationId: string) {
  const [projection, setProjection] = useState(() => emptyTaskProjection(conversationId));
  const [inputs, setInputs] = useState<AssistantTaskInputState[]>([]);
  const [hasMoreTasks, setHasMoreTasks] = useState(false);
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const [total, setTotal] = useState(0);
  const [queuedRuns, setQueuedRuns] = useState<AgentRun[]>([]);
  const [run, setRun] = useState<AgentRun | null>(null);
  const [loading, setLoading] = useState(true);
  const [olderLoading, setOlderLoading] = useState(false);
  const [error, setError] = useState('');
  const [connection, setConnection] = useState<'connecting' | 'live' | 'reconnecting' | 'error'>('connecting');
  const [deltas, setDeltas] = useState<Record<string, DeltaChunk[]>>({});
  const [streamRevision, setStreamRevision] = useState(0);
  const [streamReady, setStreamReady] = useState(false);
  const [accessEnded, setAccessEnded] = useState(false);
  const accessEndedRef = useRef(false);
  const alive = useRef(true);
  const cursor = useRef(0);
  const snapshotCursor = useRef(-1);
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
        !['apply_standard_patch', 'submit_generation_batch', 'set_image_group_primary'].includes(operation.tool_name)
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
      setStreamReady(false);
      setMessages([]);
      setTotal(0);
      setRun(null);
      setQueuedRuns([]);
      setDeltas({});
      setLoading(false);
      setOlderLoading(false);
      setProjection(emptyTaskProjection(conversationId));
      setInputs([]);
      setConnection('error');
      setError(message);
    },
    [conversationId],
  );
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
      snapshotCursor.current = snapshot.snapshot_cursor;
      if (initial) {
        cursor.current =
          Number.isSafeInteger(snapshot.resume_cursor) &&
          snapshot.resume_cursor >= 0 &&
          snapshot.resume_cursor <= snapshot.snapshot_cursor
            ? snapshot.resume_cursor
            : 0;
        initialized.current = true;
        setStreamReady(true);
      }
      setProjection((old) => mergeTaskSnapshot(old, snapshot));
      setInputs(snapshot.inputs);
      setHasMoreTasks(snapshot.has_more_tasks);
      const activeRun = snapshot.runs.find((item) => ['running', 'waiting_generation'].includes(item.status));
      const queued = snapshot.runs.filter((item) => item.status === 'queued');
      setMessages((old) => mergeMessages(old, snapshot.messages.items));
      setTotal(snapshot.messages.total);
      setRun(activeRun ?? queued[0] ?? snapshot.runs.at(-1) ?? null);
      setError('');
      setQueuedRuns(queued);
      if (!activeRun) setDeltas({});
      if (snapshot.has_more_operations) {
        for (const task of snapshot.tasks) {
          let after = snapshot.operations.filter((item) => item.task_id === task.id).at(-1)?.id;
          let more = true;
          while (more && !controller.signal.aborted) {
            const page = await assistantApi.operations(task.id, after, controller.signal);
            if (!alive.current || controller.signal.aborted || request !== refreshRequest.current) return;
            setProjection((old) => ({
              ...old,
              operations: mergeOperations(old.operations, page.items),
            }));
            more = page.has_more;
            after = page.next_after_id ?? undefined;
          }
        }
      }
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
  }, [conversationId, endAccess]);
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
      else setRun(result.run);
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
      setRun(result.run);
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
      initialized.current = false;
      cursor.current = 0;
      setStreamReady(false);
      setDeltas({});
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
      if (event.event_type === 'assistant.delta' && event.run_id) {
        setDeltas((old) => ({
          ...old,
          [event.run_id!]: addDelta(old[event.run_id!] ?? [], event),
        }));
      } else {
        if (event.event_type === 'artifact.script.applied' && typeof event.payload.project_id === 'string')
          notifyAssistantBusinessChange(event.payload.project_id, ['script']);
        if (event.event_type.startsWith('artifact')) window.dispatchEvent(new Event('agent-artifacts-updated'));
        if (event.event_type === 'run.finished' && event.run_id)
          setDeltas((old) => {
            const next = { ...old };
            delete next[event.run_id!];
            return next;
          });
        if (event.event_type === 'message.created' && event.payload.role === 'assistant' && event.run_id)
          setDeltas((old) => {
            const next = { ...old };
            delete next[event.run_id!];
            return next;
          });
        scheduleRefresh();
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
          throw new Error('stream_unavailable');
        }
        setConnection('live');
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
      } catch {
        /* The persistent cursor resumes after transport failures. */
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
    const ids = Object.keys(projection.tasks).sort((a, b) => (BigInt(a) < BigInt(b) ? -1 : 1));
    if (!hasMoreTasks || !ids.length || olderLoading) return;
    setOlderLoading(true);
    try {
      const snapshot = await assistantApi.taskState(conversationId, undefined, ids[0]);
      if (alive.current && !accessEndedRef.current) {
        setProjection((old) => mergeTaskSnapshot(old, snapshot, { historical: true }));
        setHasMoreTasks(snapshot.has_more_tasks);
      }
    } catch (cause) {
      if (alive.current && !accessEndedRef.current) {
        if (cause instanceof ApiError && cause.status && [401, 403, 404].includes(cause.status))
          endAccess('对话访问已结束，请核对登录与项目权限。');
        else setError(errorMessage(cause));
      }
    } finally {
      if (alive.current) setOlderLoading(false);
    }
  }
  return {
    messages,
    total,
    run,
    queuedRuns,
    loading,
    olderLoading,
    error,
    connection,
    reload,
    older,
    accept,
    acceptControl,
    task: selectedTask(projection.tasks),
    tasks: Object.values(projection.tasks),
    operations: Object.values(projection.operations),
    inputs,
    hasMoreTasks,
    olderTasks,
    setRun: (next: AgentRun) => {
      if (alive.current && !accessEndedRef.current) setRun(next);
    },
    accessEnded,
    reconnect: () => {
      accessEndedRef.current = false;
      setAccessEnded(false);
      initialized.current = false;
      setStreamReady(false);
      setStreamRevision((value) => value + 1);
      void reload();
    },
    delta: run ? latestDelta(deltas[run.id] ?? []) : '',
  };
}
