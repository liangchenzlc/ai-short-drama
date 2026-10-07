import { useCallback, useEffect, useRef, useState } from 'react';
import { assistantApi } from '../../api/modules/assistant';
import { ApiError, errorMessage, http } from '../../api/http';
import type { AgentEvent, AgentMessage, AgentRun, AgentSendResult } from '../../api/types/agents';
import { addDelta, latestDelta, parseAgentEvent, takeSseFrames, type DeltaChunk } from '../agents/agent-events';

const mergeMessages = (old: AgentMessage[], next: AgentMessage[]) => {
  const byId = new Map(old.map(item => [item.id, item]));
  for (const item of next) if (item.role === 'user' || item.role === 'assistant') byId.set(item.id, item);
  return [...byId.values()].sort((a, b) => a.seq - b.seq);
};

export function useAssistantRuntime(conversationId: string) {
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
  const initialized = useRef(false);
  const refreshRequest = useRef(0);
  const readController = useRef<AbortController | null>(null);
  const streamController = useRef<AbortController | null>(null);
  const count = useRef(0); count.current = messages.length;
  useEffect(() => { alive.current = true; return () => { alive.current = false; readController.current?.abort(); }; }, []);
  const endAccess = useCallback((message: string) => {
    accessEndedRef.current = true; setAccessEnded(true);
    readController.current?.abort(); streamController.current?.abort();
    initialized.current = false; setStreamReady(false);
    setMessages([]); setTotal(0); setRun(null); setQueuedRuns([]); setDeltas({}); setLoading(false); setOlderLoading(false);
    setConnection('error'); setError(message);
  }, []);
  const reload = useCallback(async () => {
    if (accessEndedRef.current) return;
    const request = ++refreshRequest.current;
    readController.current?.abort();
    const controller = new AbortController(); readController.current = controller;
    try {
      const initial = !initialized.current;
      const [page, state] = initial ? await (async () => {
        const snapshot = await assistantApi.state(conversationId, controller.signal);
        return [await assistantApi.messages(conversationId, 0, controller.signal), snapshot] as const;
      })() : await Promise.all([assistantApi.messages(conversationId, 0, controller.signal), assistantApi.state(conversationId, controller.signal)]);
      const latestRun = !state.active_run && !state.queued_runs.length ? (await assistantApi.runs(conversationId, controller.signal)).items[0] ?? null : null;
      if (!alive.current || accessEndedRef.current || controller.signal.aborted || request !== refreshRequest.current) return;
      if (initial) {
        cursor.current = Number.isSafeInteger(state.resume_cursor) && state.resume_cursor >= 0 && state.resume_cursor <= state.cursor ? state.resume_cursor : 0;
        initialized.current = true; setStreamReady(true);
      }
      setMessages(old => mergeMessages(old, page.items)); setTotal(page.total); setRun(state.active_run ?? state.queued_runs[0] ?? latestRun); setError('');
      setQueuedRuns(state.queued_runs);
      if (!state.active_run || ['succeeded', 'failed', 'cancelled'].includes(state.active_run.status)) setDeltas({});
    } catch (cause) {
      if (!alive.current || controller.signal.aborted) return;
      if (cause instanceof ApiError && cause.status && [401, 403, 404].includes(cause.status)) endAccess('对话访问已结束，请核对登录与项目权限。');
      else { setError(errorMessage(cause)); if (!initialized.current) setConnection('error'); }
    } finally { if (alive.current && !controller.signal.aborted) setLoading(false); }
  }, [conversationId, endAccess]);
  useEffect(() => { void reload(); }, [reload]);
  useEffect(() => { const refresh = () => void reload(); window.addEventListener('agent-run-updated', refresh); return () => window.removeEventListener('agent-run-updated', refresh); }, [reload]);
  async function older() {
    if (olderLoading || accessEndedRef.current || count.current >= total) return;
    setOlderLoading(true);
    try {
      const page = await assistantApi.messages(conversationId, count.current, undefined);
      if (alive.current && !accessEndedRef.current) { setMessages(old => mergeMessages(old, page.items)); setTotal(page.total); }
    } catch (cause) { if (alive.current) {
      if (cause instanceof ApiError && cause.status && [401, 403, 404].includes(cause.status)) endAccess('对话访问已结束，请核对登录与项目权限。');
      else setError(errorMessage(cause));
    } }
    finally { if (alive.current) setOlderLoading(false); }
  }
  function accept(result: AgentSendResult) {
    if (!alive.current || accessEndedRef.current) return;
    setMessages(old => mergeMessages(old, [result.message]));
    if (result.run.status === 'queued' && run && !['succeeded', 'failed', 'cancelled'].includes(run.status)) setQueuedRuns(old => [...old.filter(item => item.id !== result.run.id), result.run]);
    else setRun(result.run);
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
      refreshTimer = setTimeout(() => { refreshTimer = undefined; if (!controller.signal.aborted) void reload(); }, 100);
    };
    function receive(event: AgentEvent) {
      if (controller.signal.aborted || !alive.current || accessEndedRef.current || event.seq <= cursor.current) return;
      cursor.current = event.seq;
      if (event.event_type === 'assistant.delta' && event.run_id) {
        setDeltas(old => ({ ...old, [event.run_id!]: addDelta(old[event.run_id!] ?? [], event) }));
      } else {
        if (event.event_type.startsWith('artifact')) window.dispatchEvent(new Event('agent-artifacts-updated'));
        if (event.event_type === 'run.finished' && event.run_id) setDeltas(old => { const next = { ...old }; delete next[event.run_id!]; return next; });
        if (event.event_type === 'message.created' && event.payload.role === 'assistant' && event.run_id) setDeltas(old => { const next = { ...old }; delete next[event.run_id!]; return next; });
        scheduleRefresh();
      }
    }
    async function connect(attempt = 0) {
      if (controller.signal.aborted || accessEndedRef.current) return;
      setConnection(attempt ? 'reconnecting' : 'connecting');
      const base = String(http.defaults.baseURL ?? '/api/v1').replace(/\/$/, '');
      try {
        const response = await fetch(`${base}/assistant/conversations/${encodeURIComponent(conversationId)}/events?${new URLSearchParams({ cursor: String(cursor.current) }).toString()}`,
          { signal: controller.signal, credentials: 'include', headers: { Accept: 'text/event-stream' } });
        if (!response.ok || !response.body) {
          if (response.status === 401) window.dispatchEvent(new Event('session-expired'));
          if (response.status === 401 || response.status === 403 || response.status === 404) {
            endAccess('对话连接不可用，请核对登录与项目权限。'); return;
          }
          throw new Error('stream_unavailable');
        }
        setConnection('live');
        const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = '';
        while (!controller.signal.aborted) {
          const chunk = await reader.read();
          buffer += decoder.decode(chunk.value, { stream: !chunk.done });
          const parsed = takeSseFrames(buffer); buffer = parsed.rest;
          for (const frame of parsed.frames) {
            if (frame.split('\n').some(line => /^event:\s*access-ended\s*$/.test(line))) {
              endAccess('对话访问已结束，请核对登录与项目权限。'); return;
            }
            const event = parseAgentEvent(frame); if (event) receive(event);
          }
          if (chunk.done) break;
        }
      } catch { /* The persistent cursor resumes after transport failures. */ }
      if (controller.signal.aborted || accessEndedRef.current) return;
      setConnection('reconnecting'); void reload();
      reconnectTimer = setTimeout(() => void connect(attempt + 1), Math.min(10_000, 1500 * (attempt + 1)));
    }
    void connect();
    return () => { controller.abort(); clearTimeout(refreshTimer); clearTimeout(reconnectTimer); };
  }, [conversationId, reload, streamRevision, streamReady, endAccess]);
  return { messages, total, run, queuedRuns, loading, olderLoading, error, connection, reload, older, accept,
    setRun: (next: AgentRun) => { if (alive.current && !accessEndedRef.current) setRun(next); }, accessEnded,
    reconnect: () => { accessEndedRef.current = false; setAccessEnded(false); initialized.current = false; setStreamReady(false); setStreamRevision(value => value + 1); void reload(); }, delta: run ? latestDelta(deltas[run.id] ?? []) : '' };
}
