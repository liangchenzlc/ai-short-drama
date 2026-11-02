import { mcpTokensApi } from '@host/api/assistant/settings/mcp-tokens';
import { errorMessage } from '@host/api/http';
import { useStudioMessage } from '@host/shared/ui/StudioFeedback';
import type { McpAccessToken, McpTokenOptions } from '@host/types/assistant/settings/editor';
import { useCallback, useEffect, useRef, useState } from 'react';

export function useMcpAccessTokens(active: boolean) {
  const [tokens, setTokens] = useState<McpAccessToken[]>([]);
  const [options, setOptions] = useState<McpTokenOptions | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [issue, setIssue] = useState('');
  const [formOpen, setFormOpen] = useState(false);
  const [name, setName] = useState(''), [expiry, setExpiry] = useState(''), [ips, setIps] = useState('');
  const [projects, setProjects] = useState<string[]>([]), [tools, setTools] = useState<string[]>([]);
  const [formIssue, setFormIssue] = useState('');
  const [secret, setSecret] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<McpAccessToken | null>(null);
  const live = useRef(true), lock = useRef(false), request = useRef(0), initialized = useRef(false);
  const message = useStudioMessage();
  useEffect(() => { live.current = true; return () => { live.current = false; request.current++; }; }, []);
  const read = useCallback(async (signal?: AbortSignal) => {
    const id = ++request.current; setLoading(true);
    const [list, choices] = await Promise.allSettled([mcpTokensApi.list(signal), mcpTokensApi.options(signal)]);
    if (!live.current || signal?.aborted || id !== request.current) return;
    if (list.status === 'fulfilled') { setTokens(list.value); setLoaded(true); }
    if (choices.status === 'fulfilled') setOptions(choices.value);
    setIssue([list, choices].flatMap(result => result.status === 'rejected' ? [errorMessage(result.reason)] : []).join('；'));
    setLoading(false);
  }, []);
  useEffect(() => {
    if (!active || initialized.current) return;
    initialized.current = true;
    const controller = new AbortController(); void read(controller.signal);
    return () => { initialized.current = false; controller.abort(); };
  }, [active, read]);
  async function create() {
    if (lock.current) return;
    if (!name.trim() || !projects.length || !tools.length) { setFormIssue('请填写名称，并选择授权项目和工具。'); return; }
    if (expiry && new Date(expiry).getTime() <= Date.now()) { setFormIssue('请选择未来的到期时间，或留空。'); return; }
    lock.current = true; setBusy(true); setFormIssue('');
    try {
      const created = await mcpTokensApi.create({ name: name.trim(), expires_at: expiry ? new Date(expiry).toISOString() : null,
        allowed_ips: ips.split(/[\s,，]+/).filter(Boolean), project_ids: projects, tool_names: tools });
      if (!live.current) return;
      // 完整密钥只存在本次弹层；列表与其他持久状态只保存公开字段。
      const { secret: once, ...token } = created;
      setTokens(old => [token, ...old.filter(item => item.id !== token.id)]);
      setSecret(once); setFormOpen(false);
    } catch (cause) {
      if (live.current) setFormIssue(`创建未确认：${errorMessage(cause)}。请读取列表核对；未取得密钥的令牌可删除后重建。`);
    } finally { lock.current = false; if (live.current) setBusy(false); }
  }
  async function remove() {
    if (!deleting || lock.current) return;
    lock.current = true; setBusy(true); setFormIssue('');
    try {
      await mcpTokensApi.remove(deleting.id);
      if (!live.current) return;
      setTokens(old => old.filter(item => item.id !== deleting.id)); setDeleting(null);
      message.success('MCP 令牌已删除。', 'mcp-token-removed');
    } catch (cause) { if (live.current) setFormIssue(`删除未确认，请重新读取列表核对：${errorMessage(cause)}`); }
    finally { lock.current = false; if (live.current) setBusy(false); }
  }
  return { tokens, options, loaded, loading, busy, issue, formOpen, setFormOpen, name, setName, expiry, setExpiry, ips, setIps, projects, setProjects, tools, setTools, formIssue, setFormIssue, secret, setSecret, deleting, setDeleting, read, create, remove };
}
