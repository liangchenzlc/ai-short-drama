import { useEffect, useState } from 'react';
import { Alert, Button, Input, Skeleton } from 'antd';
import { useNavigate } from 'react-router-dom';
import { errorMessage, http } from '../../api/http';
import { useAuth } from '../auth/AuthSession';
import { confirmAction } from '../../components/ui/confirm';
import { Dialog } from '../../components/ui/Dialog';

interface Member { user_id: string; username: string; display_name: string; role: 'owner' | 'collaborator' }
interface Recipient { id: string; username: string; display_name: string }
interface Invitation { id: string; target_user_id: string | null; target_email: string | null; status: string }
export function ProjectCollaboration({ projectId, canManage }: { projectId: string; canManage: boolean }) {
  const auth = useAuth(); const navigate = useNavigate();
  const [members, setMembers] = useState<Member[]>([]); const [invitations, setInvitations] = useState<Invitation[]>([]);
  const [loading, setLoading] = useState(true); const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [loadError, setLoadError] = useState('');
  const [inviting, setInviting] = useState(false); const [query, setQuery] = useState(''); const [results, setResults] = useState<Recipient[]>([]); const [searched, setSearched] = useState(false); const [selected, setSelected] = useState<Recipient | null>(null); const [email, setEmail] = useState(''); const [url, setUrl] = useState(''); const [copied, setCopied] = useState(false); const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!auth.enabled) { setLoading(false); return; }
    const controller = new AbortController(); setLoading(true); setLoadError('');
    void Promise.all([http.get<{ items: Member[] }>(`/projects/${projectId}/members`, { signal: controller.signal }), canManage ? http.get<{ items: Invitation[] }>(`/projects/${projectId}/invitations`, { signal: controller.signal }) : Promise.resolve(null)])
      .then(([people, invites]) => { if (!controller.signal.aborted) { setMembers(people.data.items); setInvitations(invites?.data.items ?? []); } })
      .catch(cause => { if (!controller.signal.aborted) setLoadError(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [projectId, canManage, revision, auth.enabled]);
  if (!auth.enabled) return null;
  async function action(work: () => Promise<unknown>, reload = true) { if (busy) return; setBusy(true); setError(''); try { await work(); if (reload) setRevision(v => v + 1); } catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); } }
  return <section className="overview-card project-collaboration" aria-labelledby="collaboration-title">
    <div className="collaboration-heading"><h2 id="collaboration-title">共同创作</h2>{canManage ? <Button type="primary" disabled={busy} onClick={() => { setError(''); setInviting(true); }}>邀请协作者</Button> : <Button danger disabled={busy} onClick={async () => { if (await confirmAction('退出后将无法访问项目。已经生成的项目成果会保留。', { title: '退出项目', confirmText: '退出项目', danger: true })) await action(async () => { await http.post(`/projects/${projectId}/leave`); navigate('/projects', { replace: true }); }); }}>退出项目</Button>}</div>
    <p className="muted">成员可共同编辑内容，使用各自的模型配置生成。个人素材导入后成为独立的项目副本。</p>
    {error && !inviting && <Alert type="error" showIcon message={error} />}
    {loadError ? <Alert type="error" showIcon message={loadError} action={<Button onClick={() => setRevision(v => v + 1)}>重试</Button>} /> : loading ? <div role="status" aria-label="正在加载成员"><Skeleton active paragraph={{ rows: 2 }} /></div> : members.length ? members.map(member => <div className="collaboration-person" key={member.user_id}><span><strong>{member.display_name}</strong> <small>@{member.username}</small></span><span>{member.role === 'owner' ? '项目主人' : '协作者'}{canManage && member.role !== 'owner' && <Button type="text" danger disabled={busy} onClick={async () => { if (await confirmAction(`移除 ${member.username} 后，对方将失去访问权限。`, { title: '移除协作者', confirmText: '移除协作者', danger: true })) await action(() => http.delete(`/projects/${projectId}/members/${member.user_id}`)); }}>移除</Button>}</span></div>) : <p className="studio-empty">暂无成员。</p>}
    {inviting && canManage && <Dialog title="邀请协作者" className="project-invitation-dialog" canClose={!busy} onClose={() => setInviting(false)}><div className="collaboration-search">
      <form onSubmit={e => { e.preventDefault(); void action(async () => { setResults((await http.get('/users/search', { params: { q: query.trim() } })).data.items); setSearched(true); setSelected(null); setUrl(''); }, false); }}><label htmlFor="recipient-search">搜索人名、账号名、账号 ID 或邮箱<Input id="recipient-search" name="recipient-search" required minLength={2} maxLength={254} disabled={busy} value={query} onChange={e => { setQuery(e.target.value); setSelected(null); setResults([]); setSearched(false); setUrl(''); }} /></label><Button htmlType="submit" loading={busy}>搜索账号</Button></form>
      {results.map(person => <button type="button" className="collaboration-search-result" disabled={busy} key={person.id} onClick={() => { setSelected(person); setEmail(''); setUrl(''); }} aria-pressed={selected?.id === person.id}><span>{person.display_name} · @{person.username}</span><small>账号 ID {person.id}</small></button>)}
      {searched && !results.length ? <p role="status" className="muted">未找到账号，可以直接邀请邮箱。</p> : null}
      <label htmlFor="recipient-email">或邀请尚未注册的邮箱<Input id="recipient-email" name="recipient-email" type="email" maxLength={254} disabled={busy} value={email} onChange={e => { setEmail(e.target.value); setSelected(null); setUrl(''); }} /></label>
      {selected && <p role="status">已指定：{selected.display_name} · @{selected.username}（{selected.id}）</p>}
      <Button type="primary" disabled={!selected && !email.trim()} loading={busy} onClick={() => void action(async () => { const result = await http.post(`/projects/${projectId}/invitations`, selected ? { target_user_id: selected.id } : { target_email: email.trim() }); setUrl(result.data.url); setCopied(false); })}>为此受邀人生成链接</Button>
      {url && <div className="collaboration-link"><label htmlFor="invitation-url">邀请链接<Input id="invitation-url" readOnly value={url} /></label><p className="muted">七天内有效。请自行发送给指定受邀人，对方需要重新验证邮箱才能加入。</p><Button onClick={async () => { try { await navigator.clipboard.writeText(url); setCopied(true); } catch { setError('无法复制，请选中上方链接手动复制。'); } }}>{copied ? '已复制链接' : '复制邀请链接'}</Button></div>}
      {error && <Alert type="error" showIcon message={error} />}
    </div><div className="dialog-actions"><Button disabled={busy} onClick={() => setInviting(false)}>完成</Button></div></Dialog>}
    {canManage && invitations.length > 0 && <details><summary>邀请记录</summary>{invitations.map(invite => <div className="collaboration-person" key={invite.id}><span>{invite.target_email ?? `账号 ${invite.target_user_id}`} <small>{({ pending: '等待接受', accepted: '已加入', expired: '已过期', revoked: '已撤销' } as Record<string, string>)[invite.status]}</small></span>{invite.status === 'pending' && <Button size="small" danger disabled={busy} onClick={() => void action(() => http.delete(`/projects/${projectId}/invitations/${invite.id}`))}>撤销链接</Button>}</div>)}</details>}
  </section>;
}
