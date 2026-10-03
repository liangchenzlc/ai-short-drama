import { useCallback, useEffect, useRef, useState } from 'react';
import { agentsApi } from '../../api/modules/agents';
import { ApiError, errorMessage, http } from '../../api/http';
import type { AgentEvent, AgentMessage, AgentRun, AgentSendResult } from '../../api/types/agents';
import { addDelta, latestDelta, parseAgentEvent, takeSseFrames, type DeltaChunk } from './agent-events';

const mergeMessages = (old: AgentMessage[], next: AgentMessage[]) => {
  const byId = new Map(old.map(item => [item.id, item]));
  for (const item of next) if (item.role === 'user' || item.role === 'assistant') byId.set(item.id, item);
  return [...byId.values()].sort((a, b) => a.seq - b.seq);
};

export function useAgentRuntime(conversationId: string) {
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const [total, setTotal] = useState(0);
  const [run, setRun] = useState<AgentRun | null>(null);
  const [loading, setLoading] = useState(true);
  const [olderLoading, setOlderLoading] = useState(false);
  const [error, setError] = useState('');
  const [connection, setConnection] = useState<'connecting' | 'live' | 'reconnecting' | 'error'>('connecting');
  const [deltas, setDeltas] = useState<Record<string, DeltaChunk[]>>({});
  const [streamRevision, setStreamRevision] = useState(0);
  const [accessEnded, setAccessEnded] = useState(false);
  const accessEndedRef = useRef(false);
  const alive = useRef(true);
  const cursor = useRef(0);
  const refreshRequest = useRef(0);
  const readController = useRef<AbortController | null>(null);
  const streamController = useRef<AbortController | null>(null);
  const count = useRef(0); count.current = messages.length;
  useEffect(() => { alive.current = true; return () => { alive.current = false; readController.current?.abort(); }; }, []);
  const endAccess = useCallback((message: string) => {
    accessEndedRef.current = true; setAccessEnded(true);
    readController.current?.abort(); streamController.current?.abort();
    setMessages([]); setTotal(0); setRun(null); setDeltas({}); setLoading(false); setOlderLoading(false);
    setConnection('error'); setError(message);
  }, []);
  const reload = useCallback(async () => {
    if (accessEndedRef.current) return;
    const request = ++refreshRequest.current;
    readController.current?.abort();
    const controller = new AbortController(); readController.current = controller;
    try {
      const [page, runs] = await Promise.all([agentsApi.messages(conversationId, 0, controller.signal), agentsApi.runs(conversationId, controller.signal)]);
      if (!alive.current || accessEndedRef.current || controller.signal.aborted || request !== refreshRequest.current) return;
      setMessages(old => mergeMessages(old, page.items)); setTotal(page.total); setRun(runs.items[0] ?? null); setError('');
      if (!runs.items[0] || ['succeeded', 'failed', 'cancelled'].includes(runs.items[0].status)) setDeltas({});
    } catch (cause) {
      if (!alive.current || controller.signal.aborted) return;
      if (cause instanceof ApiError && cause.status && [401, 403, 404].includes(cause.status)) endAccess('对话访问已结束，请核对登录与项目权限。');
      else setError(errorMessage(cause));
    } finally { if (alive.current && !controller.signal.aborted) setLoading(false); }
  }, [conversationId, endAccess]);
  useEffect(() => { void reload(); }, [reload]);
  useEffect(() => { const refresh = () => void reload(); window.addEventListener('agent-run-updated', refresh); return () => window.removeEventListener('agent-run-updated', refresh); }, [reload]);
  async function older() {
    if (olderLoading || accessEndedRef.current || count.current >= total) return;
    setOlderLoading(true);
    try {
      const page = await agentsApi.messages(conversationId, count.current);
      if (alive.current && !accessEndedRef.current) { setMessages(old => mergeMessages(old, page.items)); setTotal(page.total); }
    } catch (cause) { if (alive.current) {
      if (cause instanceof ApiError && cause.status && [401, 403, 404].includes(cause.status)) endAccess('对话访问已结束，请核对登录与项目权限。');
      else setError(errorMessage(cause));
    } }
    finally { if (alive.current) setOlderLoading(false); }
  }
  function accept(result: AgentSendResult) {
    if (!alive.current || accessEndedRef.current) return;
    setMessages(old => mergeMessages(old, [result.message])); setRun(result.run); void reload();
  }

  useEffect(() => {
    const controller = new AbortController();
    streamController.current = controller;
    let refreshTimer: ReturnType<typeof setTimeout> | undefined;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
    const scheduleRefresh = () => {
      if (refreshTimer) return;
      refreshTimer = setTimeout(() => { refreshTimer = undefined; if (!controller.signal.aborted) void reload(); }, 100);
    };
    function receive(event: AgentEvent) {
      if (accessEndedRef.current || event.seq <= cursor.current) return;
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
        const response = await fetch(`${base}/agent/conversations/${encodeURIComponent(conversationId)}/events?cursor=${cursor.current}`,
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
  }, [conversationId, reload, streamRevision, endAccess]);
  return { messages, total, run, loading, olderLoading, error, connection, reload, older, accept,
    setRun: (next: AgentRun) => { if (alive.current && !accessEndedRef.current) setRun(next); }, accessEnded,
    reconnect: () => { accessEndedRef.current = false; setAccessEnded(false); setStreamRevision(value => value + 1); void reload(); }, delta: run ? latestDelta(deltas[run.id] ?? []) : '' };
}
