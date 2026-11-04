import { mcpTokensApi } from '@host/api/assistant/settings/mcp-tokens';
import { ApiError, errorMessage } from '@host/api/http';
import { useStudioMessage } from '@host/shared/ui/StudioFeedback';
import type {
  McpAccessToken, McpCreationRequest, McpTokenDialog, McpTokenDraft,
  McpTokenOptions, McpTokenProblem,
} from '@host/types/assistant/settings/mcp';
import { useCallback, useEffect, useRef, useState } from 'react';

const emptyDraft = (): McpTokenDraft => ({
  name: '', workbench_enabled: false, media_generation_enabled: false, expiry: '90', ips: '',
});
const uncertain = (cause: unknown) => !(cause instanceof ApiError) || !cause.status || cause.status === 408 || cause.status >= 500;

export function useMcpAccessTokens(active = true) {
  const [tokens, setTokens] = useState<McpAccessToken[]>([]);
  const [options, setOptions] = useState<McpTokenOptions | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [issue, setIssue] = useState('');
  const [dialog, setDialog] = useState<McpTokenDialog>(null);
  const [draft, setDraft] = useState(emptyDraft);
  const [problem, setProblem] = useState<McpTokenProblem | null>(null);
  const [pending, setPending] = useState<McpCreationRequest | null>(null);
  const live = useRef(true), lock = useRef(false), sequence = useRef(0);
  const message = useStudioMessage();

  useEffect(() => {
    live.current = true;
    return () => { live.current = false; sequence.current++; };
  }, []);

  const read = useCallback(async (signal?: AbortSignal) => {
    const id = ++sequence.current;
    setLoading(true);
    const [list, choices] = await Promise.allSettled([mcpTokensApi.list(signal), mcpTokensApi.options(signal)]);
    if (!live.current || signal?.aborted || id !== sequence.current) return;
    if (list.status === 'fulfilled') { setTokens(list.value); setLoaded(true); }
    if (choices.status === 'fulfilled') setOptions(choices.value);
    setIssue([list, choices].flatMap(result => result.status === 'rejected' ? [errorMessage(result.reason)] : []).join('；'));
    setLoading(false);
  }, []);

  useEffect(() => {
    if (!active || loaded) return;
    const controller = new AbortController();
    void read(controller.signal);
    return () => controller.abort();
  }, [active, loaded, read]);

  useEffect(() => {
    if (!active) setDialog(current => current?.kind === 'created' ? null : current);
  }, [active, dialog?.kind]);

  useEffect(() => {
    if (!busy && !pending) return;
    const protect = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', protect);
    return () => window.removeEventListener('beforeunload', protect);
  }, [busy, pending]);

  function openCreate() {
    if (pending) setProblem({ text: '尚未确认创建结果，请先核对。', recovery: 'create' });
    else { setDraft(emptyDraft()); setProblem(null); }
    setDialog({ kind: 'create' });
  }

  function openToken(kind: 'details' | 'revoke', token: McpAccessToken) {
    setProblem(null);
    setDialog({ kind, token });
  }

  function close() { if (!lock.current) setDialog(null); }
  function edit(patch: Partial<McpTokenDraft>) { setDraft(current => ({ ...current, ...patch })); }

  async function create() {
    if (lock.current || problem?.recovery === 'create') return;
    if (!draft.name.trim()) { setProblem({ text: '请填写令牌名称。' }); return; }
    // 结果未知时保留第一次的参数与 key，核对后重试也不会创建第二个令牌。
    const submission = pending ?? {
      key: crypto.randomUUID(),
      body: {
        name: draft.name.trim(),
        workbench_enabled: draft.workbench_enabled,
        media_generation_enabled: draft.workbench_enabled && draft.media_generation_enabled,
        allowed_ips: draft.ips.split(/[\s,，]+/).filter(Boolean),
        ...(draft.expiry === '90' ? {} : {
          expires_at: draft.expiry === 'never' ? null : new Date(Date.now() + Number(draft.expiry) * 86400000).toISOString(),
        }),
      },
    };
    lock.current = true; setBusy(true); setProblem(null); setPending(submission);
    try {
      const { secret, ...token } = await mcpTokensApi.create(submission.body, submission.key);
      if (!live.current) return;
      setTokens(current => token.deleted_at ? current : [token, ...current.filter(item => item.id !== token.id)]);
      setPending(null);
      setDialog(secret ? { kind: 'created', secret } : { kind: 'lost', token });
    } catch (cause) {
      if (!live.current) return;
      if (uncertain(cause)) setProblem({ text: '尚未确认创建结果，请先核对。', recovery: 'create' });
      else { setPending(null); setProblem({ text: errorMessage(cause) }); }
    } finally { lock.current = false; if (live.current) setBusy(false); }
  }

  async function recover() {
    if (lock.current) return;
    lock.current = true; setBusy(true);
    try {
      if (pending) {
        const rows = await mcpTokensApi.list(undefined, pending.key);
        if (!live.current) return;
        const token = rows[0];
        if (token) {
          setPending(null); setProblem(null); setDialog({ kind: 'lost', token });
          if (!token.deleted_at) setTokens(current => [token, ...current.filter(item => item.id !== token.id)]);
        } else setProblem({ text: '尚未发现本次创建，可沿原请求重试。', recovery: 'retry-create' });
      } else if (dialog?.kind === 'revoke' || dialog?.kind === 'lost') {
        const rows = await mcpTokensApi.list(undefined, dialog.token.create_key);
        if (!live.current) return;
        if (rows[0]?.deleted_at) {
          setTokens(current => current.filter(item => item.id !== dialog.token.id));
          setDialog(null); setProblem(null);
        } else setProblem({ text: '令牌仍有效，可重试撤销。' });
      }
    } catch (cause) {
      if (live.current) setProblem(current => ({
        text: `核对失败：${errorMessage(cause)}`,
        recovery: current?.recovery ?? (pending ? 'create' : 'revoke'),
      }));
    } finally { lock.current = false; if (live.current) setBusy(false); }
  }

  async function remove() {
    if (!dialog || !('token' in dialog) || lock.current || problem?.recovery === 'revoke') return;
    const token = dialog.token;
    lock.current = true; setBusy(true); setProblem(null);
    try {
      await mcpTokensApi.remove(token.id);
      if (!live.current) return;
      setTokens(current => current.filter(item => item.id !== token.id)); setDialog(null);
      message.success('令牌已撤销。', 'mcp-token-revoked');
    } catch (cause) {
      if (live.current) setProblem({
        text: uncertain(cause) ? '尚未确认撤销结果，请先核对。' : errorMessage(cause),
        recovery: uncertain(cause) ? 'revoke' : undefined,
      });
    } finally { lock.current = false; if (live.current) setBusy(false); }
  }

  return { tokens, options, loaded, loading, busy, issue, dialog, draft, problem, pending,
    read, openCreate, openToken, close, edit, create, recover, remove };
}
