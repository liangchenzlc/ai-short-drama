import { useMcpAccessTokens } from '@host/features/assistant/settings/useMcpAccessTokens';
import { Dialog } from '@host/shared/ui/Dialog';
import { StudioFeedback, useStudioMessage } from '@host/shared/ui/StudioFeedback';
import type { McpAccessToken } from '@host/types/assistant/settings/editor';
import { Button, Input, Select, Skeleton } from 'antd';
import './assistant-settings.css';

export function McpAccessTokens({ active }: { active: boolean }) {
  const { tokens, options, loaded, loading, busy, issue, formOpen, setFormOpen, name, setName, expiry, setExpiry, ips, setIps, projects, setProjects, tools, setTools, formIssue, setFormIssue, secret, setSecret, deleting, setDeleting, read, create, remove } = useMcpAccessTokens(active);
  const message = useStudioMessage();
  const expired = (token: McpAccessToken) => !!token.expires_at && new Date(token.expires_at).getTime() <= Date.now();
  return <section className="assistant-mcp-tokens" aria-labelledby="assistant-mcp-title">
    <div className="assistant-settings-inline"><h3 id="assistant-mcp-title">MCP 账号令牌</h3><Button disabled={!options || loading || busy} onClick={() => { setName(''); setExpiry(''); setIps(''); setProjects([]); setTools([]); setFormIssue(''); setFormOpen(true); }}>创建令牌</Button><Button disabled={loading || busy} onClick={() => void read()}>读取令牌列表</Button></div>
    <p className="muted">仅授权工作台业务工具。完整密钥只显示一次；删除后立即失效。</p>
    {issue && <StudioFeedback type="error" persistent title="MCP 令牌" message={issue} />}
    {loading && !loaded ? <Skeleton active /> : !loaded ? <p>尚未读取令牌列表。</p> : !tokens.length ? <p>还没有 MCP 令牌。创建时选择可访问的项目和业务工具。</p> :
      <div className="assistant-token-list"><table><thead><tr><th>名称 / 密钥提示</th><th>状态 / 到期</th><th>授权范围</th><th>IP 白名单</th><th>创建时间</th><th>操作</th></tr></thead><tbody>
        {tokens.map(token => <tr key={token.id}><td>{token.name}<small>{token.token_hint}</small></td><td>{expired(token) ? '已过期' : '有效'}<small>{token.expires_at ? new Date(token.expires_at).toLocaleString() : '长期有效'}</small></td><td><small>{token.project_ids.map(id => options?.projects.find(item => item.id === id)?.name ?? '项目已不可访问').join('、')}</small><small>{token.tool_names.join('、')}</small></td><td><small>{token.allowed_ips.join('、') || '不限'}</small></td><td>{new Date(token.created_at).toLocaleString()}</td><td><Button danger size="small" disabled={busy || loading} onClick={() => { setDeleting(token); setFormIssue(''); }}>删除 {token.name}</Button></td></tr>)}
      </tbody></table></div>}
    {formOpen && options && <Dialog title="创建 MCP 令牌" onClose={() => setFormOpen(false)} canClose={!busy}>
      <form className="assistant-token-form" onSubmit={event => { event.preventDefault(); void create(); }}>
        <label><span>令牌名称</span><Input aria-label="令牌名称" maxLength={80} value={name} disabled={busy} onChange={event => setName(event.target.value)} required /></label>
        <label><span>到期时间（可选）</span><Input aria-label="到期时间（可选）" type="datetime-local" value={expiry} disabled={busy} onChange={event => setExpiry(event.target.value)} /></label>
        <label><span>IP / CIDR 白名单（可选）</span><Input.TextArea aria-label="IP / CIDR 白名单（可选）" value={ips} disabled={busy} rows={2} placeholder="每行一项，例如 192.0.2.1 或 192.0.2.0/24" onChange={event => setIps(event.target.value)} /></label>
        <label><span>授权项目</span><Select aria-label="授权项目" mode="multiple" value={projects} disabled={busy} options={options.projects.map(item => ({ value: item.id, label: item.name }))} onChange={setProjects} /></label>
        <label><span>授权工具</span><Select aria-label="授权工具" mode="multiple" value={tools} disabled={busy} optionFilterProp="label" options={options.tools.map(item => ({ value: item.name, label: `${item.name} · ${item.description}` }))} onChange={setTools} /></label>
        {formIssue && <p role="alert">{formIssue}</p>}
        <div className="dialog-actions"><Button disabled={busy} onClick={() => setFormOpen(false)}>取消</Button><Button htmlType="submit" type="primary" loading={busy}>创建</Button></div>
      </form>
    </Dialog>}
    {secret !== null && <Dialog title="保存 MCP 密钥" onClose={() => setSecret(null)}>
      <p>完整密钥仅显示这一次。关闭后无法再次读取。</p><pre className="assistant-token-secret" aria-label="一次性 MCP 密钥">{secret}</pre>
      <div className="dialog-actions"><Button onClick={async () => { try { await navigator.clipboard.writeText(secret); message.success('密钥已复制。'); } catch { message.error('复制失败，请手动选择并复制密钥。'); } }}>复制密钥</Button><Button type="primary" onClick={() => setSecret(null)}>已保存，关闭</Button></div>
    </Dialog>}
    {deleting && <Dialog title="删除 MCP 令牌" onClose={() => setDeleting(null)} canClose={!busy}>
      <p>删除「{deleting.name}」后，使用此密钥的请求立即失效。</p>{formIssue && <p role="alert">{formIssue}</p>}
      <div className="dialog-actions"><Button disabled={busy} onClick={() => setDeleting(null)}>取消</Button><Button danger loading={busy} onClick={() => void remove()}>确认删除</Button></div>
    </Dialog>}
  </section>;
}
