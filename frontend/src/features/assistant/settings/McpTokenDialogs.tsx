import { Dialog } from '@host/shared/ui/Dialog';
import { SettingRow } from '@host/shared/ui/SettingRow';
import { StudioFeedback, useStudioMessage } from '@host/shared/ui/StudioFeedback';
import type { McpAccessToken, McpTokenDialogsProps } from '@host/types/assistant/settings/mcp';
import { Button, Input, Select, Switch } from 'antd';
import { Copy } from 'lucide-react';
import { useRef } from 'react';

function TokenDetails({ token, tools }: { token: McpAccessToken; tools: McpTokenDialogsProps['options'] }) {
  return <dl className="mcp-detail">
    <dt>名称</dt><dd>{token.name}</dd>
    <dt>MCP 令牌</dt><dd className="mcp-token-hint">{token.token_hint}</dd>
    <dt>项目范围</dt><dd>账号有权访问的全部项目</dd>
    <dt>权限</dt><dd><ul>{token.tool_names.map(name => <li key={name}>{tools?.tools.find(item => item.name === name)?.description ?? name}</li>)}</ul></dd>
    <dt>有效期</dt><dd>{token.expires_at ? new Date(token.expires_at).toLocaleString('zh-CN') : '长期有效'}</dd>
    <dt>来源限制</dt><dd>{token.allowed_ips.join('\n') || '不限来源'}</dd>
    <dt>创建时间</dt><dd>{new Date(token.created_at).toLocaleString('zh-CN')}</dd>
  </dl>;
}

function TokenSecret({ secret, address, close }: { secret: string; address: string; close: () => void }) {
  const input = useRef<HTMLInputElement | null>(null);
  const message = useStudioMessage();
  const connection = `Streamable HTTP\n${address}\nAuthorization: Bearer <你的令牌>`;
  async function copy(value: string) {
    try { await navigator.clipboard.writeText(value); message.success('已复制。', 'mcp-secret-copy'); }
    catch { input.current?.focus(); input.current?.select(); message.error('复制失败，请手动选择并复制密钥。', 'mcp-secret-copy'); }
  }
  return <>
    <p>完整密钥仅显示一次，关闭后无法再次查看。</p>
    <div className="mcp-secret-line">
      <Input aria-label="完整 MCP 密钥" value={secret} readOnly ref={node => { input.current = node?.input ?? null; }} />
      <Button type="text" aria-label="复制密钥" icon={<Copy size={16} />} onClick={() => void copy(secret)} />
    </div>
    <pre className="mcp-connection-info">{connection}</pre>
    <div className="dialog-actions"><Button onClick={() => void copy(connection)}>复制接入信息</Button><Button type="primary" onClick={close}>完成</Button></div>
  </>;
}

export function McpTokenDialogs(props: McpTokenDialogsProps) {
  const { active, dialog, draft, options, busy, pending, problem, close, edit, create, recover, remove, openToken, openCreate } = props;
  if (!dialog) return null;
  const locked = busy || !!pending;
  const recovery = problem?.recovery;
  const recoveryLabel = recovery === 'retry-create' ? '沿原请求重试' : recovery === 'revoke' ? '核对撤销结果' : '核对创建结果';
  const notice = problem && <StudioFeedback type={recovery ? 'warning' : 'error'} persistent noticeKey="mcp-dialog-problem" message={problem.text} />;
  const title = dialog.kind === 'create' ? '创建 MCP 令牌' : dialog.kind === 'created' ? '令牌已创建' : dialog.kind === 'lost' ? '未取得完整密钥' : dialog.kind === 'revoke' ? '撤销令牌' : '令牌授权';
  return <Dialog title={title} onClose={close} canClose={!busy} open={active} className="mcp-dialog">
    {notice}
    {recovery && problem && <div className="mcp-recovery" role="status"><span>{problem.text}</span><Button disabled={busy} onClick={() => void (recovery === 'retry-create' ? create() : recover())}>{recoveryLabel}</Button></div>}
    {dialog.kind === 'create' ? <form className="mcp-token-form" onSubmit={event => { event.preventDefault(); void create(); }}>
      <label className="mcp-field"><span>名称</span><Input aria-label="令牌名称" maxLength={80} placeholder="例如：Cursor" value={draft.name} disabled={locked} onChange={event => edit({ name: event.target.value })} required /></label>
      <div className="mcp-permissions">
        <p>适用于账号有权访问的全部项目。</p>
        <SettingRow title="工作台操作"><Switch aria-label="工作台操作" checked={draft.workbench_enabled} disabled={locked} onChange={value => edit({ workbench_enabled: value })} /></SettingRow>
        {draft.workbench_enabled && <SettingRow title="允许生成图片、视频、音频" nested><Switch aria-label="允许生成图片、视频、音频" checked={draft.media_generation_enabled} disabled={locked} onChange={value => edit({ media_generation_enabled: value })} /></SettingRow>}
      </div>
      <label className="mcp-field mcp-expiry"><span>有效期</span><Select aria-label="有效期" value={draft.expiry} disabled={locked} onChange={value => edit({ expiry: value })} options={[
        { value: '30', label: '30 天' }, { value: '90', label: '90 天' }, { value: '180', label: '180 天' }, { value: 'never', label: '长期有效' },
      ]} /></label>
      <details className="mcp-source-limit"><summary>来源限制</summary><label className="mcp-field"><span>允许的 IP / CIDR</span><Input.TextArea aria-label="允许的 IP / CIDR" rows={3} placeholder="留空不限来源，每行一项" value={draft.ips} disabled={locked} onChange={event => edit({ ips: event.target.value })} /></label></details>
      <div className="dialog-actions"><Button disabled={busy} onClick={close}>取消</Button><Button type="primary" htmlType="submit" loading={busy} disabled={locked}>创建令牌</Button></div>
    </form> : dialog.kind === 'created' ? <TokenSecret secret={dialog.secret} address={options?.connection_url ?? ''} close={close} /> : dialog.kind === 'details' ? <>
      <TokenDetails token={dialog.token} tools={options} /><div className="dialog-actions"><Button onClick={close}>关闭</Button></div>
    </> : dialog.kind === 'lost' ? <>
      <p>{dialog.token.deleted_at ? '本次令牌已撤销，可重新创建。' : '本次令牌已创建，但完整密钥未取得。可撤销此令牌后重新创建。'}</p>
      <TokenDetails token={dialog.token} tools={options} />
      <div className="dialog-actions"><Button onClick={close}>关闭</Button><Button type="primary" onClick={() => dialog.token.deleted_at ? openCreate() : openToken('revoke', dialog.token)}>{dialog.token.deleted_at ? '创建新令牌' : '撤销此令牌'}</Button></div>
    </> : <>
      <p>撤销「{dialog.token.name}」后，使用此令牌的客户端将无法继续访问。</p>
      <p className="muted">已经外发的生成任务继续完成。</p>
      <div className="dialog-actions"><Button disabled={busy} onClick={close}>取消</Button><Button danger loading={busy} disabled={recovery === 'revoke'} onClick={() => void remove()}>撤销令牌</Button></div>
    </>}
  </Dialog>;
}
