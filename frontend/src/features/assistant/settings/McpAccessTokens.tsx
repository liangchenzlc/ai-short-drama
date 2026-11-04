import { McpTokenDialogs } from '@host/features/assistant/settings/McpTokenDialogs';
import { useMcpAccessTokens } from '@host/features/assistant/settings/useMcpAccessTokens';
import { StudioFeedback, useStudioMessage } from '@host/shared/ui/StudioFeedback';
import type { McpAccessToken } from '@host/types/assistant/settings/mcp';
import { Button, Empty, Input, Skeleton, Tooltip } from 'antd';
import { Copy, Eye, Plus, RefreshCw, Trash2 } from 'lucide-react';
import { useRef } from 'react';
import './mcp-access-tokens.css';

const dateLabel = (value: string | null) => value
  ? new Date(value).toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' })
  : '长期有效';

function permissionLabel(token: McpAccessToken) {
  if (token.tool_names.includes('workbench__submit_generation_batch')) return '工作台操作、媒体生成';
  return token.tool_names.includes('workbench__apply_standard_patch') ? '工作台操作' : '只读访问';
}

function McpConnection({ value }: { value: string }) {
  const message = useStudioMessage();
  const input = useRef<HTMLInputElement | null>(null);
  return <label className="mcp-endpoint">
    <span>接入地址</span>
    <div className="mcp-endpoint-box">
      <Input aria-label="MCP 接入地址" value={value} readOnly ref={node => { input.current = node?.input ?? null; }} />
      <Tooltip title="复制接入地址"><Button type="text" aria-label="复制接入地址" icon={<Copy size={16} />} onClick={async () => {
        try { await navigator.clipboard.writeText(value); message.success('接入地址已复制。', 'mcp-address-copy'); }
        catch { input.current?.select(); message.error('复制失败，请选中接入地址后复制。', 'mcp-address-copy'); }
      }} /></Tooltip>
    </div>
  </label>;
}

export function McpAccessTokens({ active = true }: { active?: boolean }) {
  const state = useMcpAccessTokens(active);
  const { tokens, options, loaded, loading, busy, issue, pending, openCreate, openToken, read } = state;
  return <section className="mcp-settings" aria-label="MCP 接入">
    {options && <McpConnection value={options.connection_url} />}
    <div className="mcp-toolbar">
      <span>{loaded ? `共 ${tokens.length} 个令牌` : 'MCP 令牌'}</span>
      <div>
        <Tooltip title="刷新令牌"><Button type="text" aria-label="刷新令牌" loading={loading} disabled={busy} icon={<RefreshCw size={16} />} onClick={() => void read()} /></Tooltip>
        <Button type={pending ? 'default' : 'primary'} disabled={!options || busy || loading} icon={pending ? undefined : <Plus size={16} />} onClick={openCreate}>
          {pending ? '核对创建结果' : '创建令牌'}
        </Button>
      </div>
    </div>
    {issue && <StudioFeedback type="error" persistent title="MCP 令牌" message={issue} noticeKey="mcp-list-error" actions={<Button onClick={() => void read()}>重新读取</Button>} />}
    {loading && !loaded ? <Skeleton active paragraph={{ rows: 4 }} /> : !loaded ?
      <div className="mcp-empty" role="alert"><p>令牌读取失败</p><Button onClick={() => void read()}>重新读取</Button></div> :
      !tokens.length ? <Empty className="mcp-empty" image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有 MCP 令牌" /> :
      <div className="mcp-table-wrap"><table className="mcp-table">
        <caption className="sr-only">MCP 令牌列表</caption>
        <thead><tr><th>名称</th><th>MCP 令牌</th><th>授权范围</th><th>有效期</th><th>状态</th><th>操作</th></tr></thead>
        <tbody>{tokens.map(token => {
          const expired = !!token.expires_at && new Date(token.expires_at).getTime() <= Date.now();
          return <tr key={token.id}>
            <td><strong title={token.name}>{token.name}</strong></td>
            <td><span className="mcp-token-hint">{token.token_hint}</span></td>
            <td>{permissionLabel(token)}</td><td>{dateLabel(token.expires_at)}</td>
            <td><span className={`mcp-state${expired ? '' : ' active'}`}>{expired ? '已过期' : '有效'}</span></td>
            <td><div className="mcp-token-actions">
              <Tooltip title="查看授权"><Button type="text" size="small" disabled={busy || loading} icon={<Eye size={16} />} aria-label={`查看授权：${token.name}`} onClick={() => openToken('details', token)} /></Tooltip>
              <Tooltip title="撤销令牌"><Button type="text" size="small" disabled={busy || loading} icon={<Trash2 size={16} />} aria-label={`撤销令牌：${token.name}`} onClick={() => openToken('revoke', token)} /></Tooltip>
            </div></td>
          </tr>;
        })}</tbody>
      </table></div>}
    <McpTokenDialogs active={active} {...state} />
  </section>;
}
