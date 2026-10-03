import { useEffect, useState, type FormEvent } from 'react';
import { Alert, Button, Input, Pagination, Select, Skeleton, Tooltip } from 'antd';
import { useSearchParams } from 'react-router-dom';
import type { ProjectSession } from '../../types/projects';
import { Dialog } from '../../components/ui/Dialog';
import { projectsApi, projectError, type ProjectFields, type RemoteProject, type RemoteEpisode } from '../../api/modules/projects';
import { ProjectCollaboration } from './ProjectCollaboration';
import { ProjectResourceLibrary } from './ProjectResourceLibrary';
import { ApiError } from '../../api/http';
import { useAuth } from '../auth/AuthSession';
import { Icon, type IconName } from '../../components/ui/Icon';
import { EpisodeCard } from './EpisodeCard';

const aspects = [{ value: '16:9', label: '横屏 16:9' }, { value: '9:16', label: '竖屏 9:16' }];
const sections: { key: string; label: string; icon: IconName }[] = [
  { key: 'overview', label: '剧集信息', icon: 'film' },
  { key: 'resources', label: '项目资源库', icon: 'library' },
  { key: 'collaboration', label: '共同创作', icon: 'person' },
];
export function ProjectOverview({ session, project, onProjectUpdated, onDeleted }: {
  session: ProjectSession; project: RemoteProject; onProjectUpdated: (project: RemoteProject) => void;
  onDeleted: () => void;
}) {
  const auth = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedSection = searchParams.get('section');
  const section = sections.some(item => item.key === requestedSection) ? requestedSection : 'overview';
  const [conflict, setConflict] = useState(false);
  const [latestProject, setLatestProject] = useState<RemoteProject | null>(null);
  const [episodeConflict, setEpisodeConflict] = useState(false);
  const [details, setDetails] = useState<ProjectFields>({ name: project.name, synopsis: project.synopsis, style: project.style, aspect: project.aspect });
  const [saveError, setSaveError] = useState('');
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);
  const [episodes, setEpisodes] = useState<RemoteEpisode[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [editing, setEditing] = useState<RemoteEpisode | 'new' | null>(null);
  const [draft, setDraft] = useState({ title: '', synopsis: '', aspect: project.aspect, style: project.style });
  const [deleting, setDeleting] = useState<RemoteEpisode | 'project' | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const dirty = details.name !== project.name || details.synopsis !== project.synopsis || details.style !== project.style || details.aspect !== project.aspect;
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setLoadError('');
    void projectsApi.listEpisodes(project.projectId, offset, 20, controller.signal).then((page) => {
      if (controller.signal.aborted) return;
      if (offset > 0 && page.total <= offset) { setOffset(Math.max(0, Math.ceil(page.total / 20) - 1) * 20); return; }
      setEpisodes(page.items); setTotal(page.total);
    }).catch((cause) => { if (!controller.signal.aborted) setLoadError(projectError(cause)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [project.projectId, offset, revision]);
  function changeDetails(patch: Partial<ProjectFields>) { setDetails((v) => ({ ...v, ...patch })); setSaved(false); }
  async function saveProject(event: FormEvent) {
    event.preventDefault();
    if (saving) return;
    if (!details.name.trim()) { setSaveError('请输入项目名称。'); return; }
    setSaving(true); setSaveError(''); setSaved(false);
    try {
      const result = await projectsApi.update(project.projectId, { ...details, name: details.name.trim(), row_version: project.rowVersion });
      onProjectUpdated(result); setDetails({ name: result.name, synopsis: result.synopsis, style: result.style, aspect: result.aspect }); setSaved(true);
    } catch (cause) { setSaveError(projectError(cause)); setConflict(cause instanceof ApiError && cause.status === 409); }
    finally { setSaving(false); }
  }
  function editEpisode(item: RemoteEpisode | 'new') {
    setEditing(item); setError(''); setEpisodeConflict(false);
    setDraft(item === 'new' ? { title: '', synopsis: '', aspect: project.aspect, style: project.style } : { title: item.title, synopsis: item.synopsis, aspect: item.aspect, style: item.style });
  }
  async function saveEpisode(event: FormEvent) {
    event.preventDefault();
    if (!editing || busy) return;
    if (!draft.title.trim()) { setError('请输入分集标题。'); return; }
    setBusy(true); setError('');
    try {
      if (editing === 'new') {
        await projectsApi.createEpisode(project.projectId, { title: draft.title.trim(), synopsis: draft.synopsis });
        setOffset(Math.floor(total / 20) * 20);
      } else await projectsApi.updateEpisode(project.projectId, editing.id, { ...draft, title: draft.title.trim(), row_version: editing.rowVersion });
      setEditing(null); setRevision((v) => v + 1);
    } catch (cause) { setError(projectError(cause, editing === 'new' ? 'create' : undefined)); setEpisodeConflict(cause instanceof ApiError && cause.status === 409); }
    finally { setBusy(false); }
  }
  async function remove() {
    if (!deleting || busy) return;
    setBusy(true); setError('');
    try {
      if (deleting === 'project') { await projectsApi.remove(project.projectId); onDeleted(); }
      else { await projectsApi.removeEpisode(project.projectId, deleting.id); setDeleting(null); setRevision((v) => v + 1); }
    } catch (cause) { setError(projectError(cause, 'delete')); }
    finally { setBusy(false); }
  }
  return <div className="project-detail-workspace">
    <nav className="project-detail-nav" aria-label="项目详情导航">{sections.map(item => <Tooltip key={item.key} title={item.label} placement="right"><Button type="text" icon={<Icon name={item.icon} size={20} />} aria-label={item.label} aria-current={section === item.key ? 'page' : undefined} onClick={() => setSearchParams(previous => { const next = new URLSearchParams(previous); if (item.key === 'overview') next.delete('section'); else next.set('section', item.key); return next; })} /></Tooltip>)}</nav>
    <div className="project-detail-panel">
    <div className="project-overview" hidden={section !== 'overview'}>
    <section className="overview-card overview-info" aria-labelledby="project-info-title">
      <div className="overview-heading"><h2 id="project-info-title">剧集信息</h2><Button type="text" danger disabled={saving || busy || project.capabilities?.delete === false} onClick={() => { setDeleting('project'); setError(''); }}>{auth.enabled ? '归档项目' : '删除项目'}</Button></div>
      <form className="project-settings-form" onSubmit={saveProject}>
        <label htmlFor="edit-project-name">项目名称<Input id="edit-project-name" required maxLength={120} value={details.name} disabled={saving} onChange={(e) => changeDetails({ name: e.target.value })} /></label>
        <div className="form-row"><label htmlFor="project-style">默认视觉风格<Input id="project-style" maxLength={255} placeholder="例如：清透水彩、都市写实" value={details.style} disabled={saving} onChange={(e) => changeDetails({ style: e.target.value })} /></label>
          <label htmlFor="project-aspect">默认画幅<Select id="project-aspect" value={details.aspect} disabled={saving} onChange={(aspect: '16:9' | '9:16') => changeDetails({ aspect })} options={aspects} /></label></div>
        <p className="muted">默认设置仅用于新建分集，已有分集保留各自的设置。</p>
        <label htmlFor="project-synopsis">故事梗概<Input.TextArea id="project-synopsis" maxLength={2000} rows={3} value={details.synopsis} disabled={saving} onChange={(e) => changeDetails({ synopsis: e.target.value })} /></label>
        {saveError && <p role="alert" className="notice">{saveError}</p>}
        {conflict && <Button disabled={saving} onClick={async () => { try { setLatestProject(await projectsApi.get(project.projectId)); } catch (cause) { setSaveError(projectError(cause)); } }}>查看最新设置并手动合并</Button>}
        <div className="project-form-actions"><span role="status">{saving ? '正在保存…' : dirty ? '有未保存的修改' : saved ? '已保存至服务端' : ''}</span><Button type="primary" htmlType="submit" loading={saving} disabled={!dirty}>保存项目信息</Button></div>
      </form>
    </section>
    <section className="overview-card" aria-labelledby="episodes-title">
      <div className="overview-heading"><h2 id="episodes-title">分集列表 <small>共 {total} 集</small></h2><Button type="primary" disabled={saving || busy} onClick={() => editEpisode('new')}>新增一集</Button></div>
      {loadError ? <Alert type="error" message={loadError} action={<Button onClick={() => setRevision((v) => v + 1)}>重试</Button>} /> : loading ? <div className="episode-cover-grid" role="status" aria-label="正在加载分集">{[0, 1, 2].map(key => <Skeleton.Node key={key} active className="episode-cover-skeleton" />)}</div> : episodes.length ? <>
        <div className="episode-cover-grid">{episodes.map((item, index) => <EpisodeCard key={item.id} episode={item} number={offset + index + 1} disabled={saving || busy} onEdit={editEpisode} onDelete={item => { setDeleting(item); setError(''); }} />)}</div>
        <Pagination current={offset / 20 + 1} pageSize={20} total={total} hideOnSinglePage showSizeChanger={false} onChange={(page) => setOffset((page - 1) * 20)} />
      </> : <div className="studio-empty"><h3>从第一集开始</h3><p>填写分集标题，即可进入小说、剧本和分镜创作。</p><Button type="primary" disabled={saving || busy} onClick={() => editEpisode('new')}>添加第一集</Button></div>}
    </section>
    </div>
    {section === 'resources' ? <div className="project-section"><ProjectResourceLibrary projectId={session.projectId} /></div> : null}
    {section === 'collaboration' ? <div className="project-section"><ProjectCollaboration projectId={session.projectId} canManage={project.capabilities?.manage_members ?? false} /></div> : null}
    </div>
    {latestProject && <Dialog title="核对最新项目设置" onClose={() => setLatestProject(null)}><p>当前输入仍保留。核对以下服务端内容后，可以继续编辑并重新保存。</p><dl><dt>名称</dt><dd>{latestProject.name}</dd><dt>故事梗概</dt><dd style={{ whiteSpace: 'pre-wrap' }}>{latestProject.synopsis || '尚未填写'}</dd><dt>画幅 / 风格</dt><dd>{latestProject.aspect} / {latestProject.style || '未设置'}</dd></dl><Button onClick={() => { onProjectUpdated(latestProject); setLatestProject(null); setConflict(false); setSaveError('请手动合并后重新保存。'); }}>保留输入，基于最新版本继续编辑</Button></Dialog>}
    {editing && <Dialog title={editing === 'new' ? '新增一集' : '编辑分集'} canClose={!busy} onClose={() => setEditing(null)}><form className="studio-form" onSubmit={saveEpisode}>
      <label htmlFor="episode-title">标题<Input id="episode-title" autoFocus required maxLength={255} disabled={busy} value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} /></label>
      <label htmlFor="episode-synopsis">内容概要<Input.TextArea id="episode-synopsis" rows={3} maxLength={500} disabled={busy} value={draft.synopsis} onChange={(e) => setDraft({ ...draft, synopsis: e.target.value })} /></label>
      {editing === 'new' ? <p className="muted">画幅和风格使用项目已保存的默认设置。</p> : <><label htmlFor="episode-aspect">本集画幅<Select id="episode-aspect" value={draft.aspect} disabled={busy} options={aspects} onChange={(aspect: '16:9' | '9:16') => setDraft({ ...draft, aspect })} /></label><label htmlFor="episode-style">本集风格<Input id="episode-style" maxLength={255} value={draft.style} disabled={busy} onChange={(e) => setDraft({ ...draft, style: e.target.value })} /></label></>}
      {error && <p role="alert" className="notice">{error}</p>}
      {episodeConflict && editing !== 'new' && <Button disabled={busy} onClick={async () => { try { const latest = await projectsApi.getEpisode(project.projectId, editing.id); setEditing(latest); setError(`最新设置：${latest.title}；${latest.synopsis || '无概要'}；${latest.aspect}；${latest.style || '未设置风格'}。输入已保留，请核对并手动合并后重新保存。`); setEpisodeConflict(false); } catch (cause) { setError(projectError(cause)); } }}>读取最新分集设置，保留当前输入</Button>}
      <div className="dialog-actions"><Button disabled={busy} onClick={() => setEditing(null)}>取消</Button><Button htmlType="submit" type="primary" loading={busy}>{editing === 'new' ? '添加分集' : '保存分集'}</Button></div>
    </form></Dialog>}
    {deleting && <Dialog title={deleting === 'project' ? (auth.enabled ? '归档项目' : '删除项目') : '删除分集'} canClose={!busy} onClose={() => setDeleting(null)}>
      {deleting === 'project' && auth.enabled ? <><p>确定归档“{project.name}”吗？</p><p className="muted">项目将从成员的列表中隐藏，并停止新的生成提交。历史创作和产出仍保留；恢复需由运维处理。</p></> : <><p>确定删除“{deleting === 'project' ? project.name : deleting.title}”吗？此操作无法撤销。</p><p className="muted">存在关联素材或制作内容时，需要先清理关联内容。</p></>}
      {error && <p role="alert" className="notice">{error}</p>}
      <div className="dialog-actions"><Button disabled={busy} onClick={() => setDeleting(null)}>取消</Button><Button danger type="primary" loading={busy} onClick={() => void remove()}>{deleting === 'project' && auth.enabled ? '确认归档' : '确认删除'}</Button></div>
    </Dialog>}
  </div>;
}
