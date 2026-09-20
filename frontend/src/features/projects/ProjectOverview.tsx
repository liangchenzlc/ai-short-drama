import { useEffect, useState, type FormEvent } from 'react';
import { Alert, Button, Input, Pagination, Select, Spin } from 'antd';
import type { ProjectSession } from '../../types/projects';
import { Dialog } from '../../components/ui/Dialog';
import { projectsApi, projectError, type ProjectFields, type RemoteProject, type RemoteEpisode } from '../../api/modules/projects';
import { ProjectResourceLibrary } from './ProjectResourceLibrary';

const aspects = [{ value: '16:9', label: '横屏 16:9' }, { value: '9:16', label: '竖屏 9:16' }];
export function ProjectOverview({ session, project, onProjectUpdated, onDeleted, onOpenEpisode }: {
  session: ProjectSession; project: RemoteProject; onProjectUpdated: (project: RemoteProject) => void;
  onDeleted: () => void; onOpenEpisode: (episode: RemoteEpisode) => void;
}) {
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
      const result = await projectsApi.update(project.projectId, { ...details, name: details.name.trim() });
      onProjectUpdated(result); setDetails({ name: result.name, synopsis: result.synopsis, style: result.style, aspect: result.aspect }); setSaved(true);
    } catch (cause) { setSaveError(projectError(cause)); }
    finally { setSaving(false); }
  }
  function editEpisode(item: RemoteEpisode | 'new') {
    setEditing(item); setError('');
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
      } else await projectsApi.updateEpisode(project.projectId, editing.id, { ...draft, title: draft.title.trim() });
      setEditing(null); setRevision((v) => v + 1);
    } catch (cause) { setError(projectError(cause, editing === 'new' ? 'create' : undefined)); }
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
  return <div className="project-overview">
    <section className="overview-card overview-info" aria-labelledby="project-info-title">
      <div className="overview-heading"><h2 id="project-info-title">剧集信息</h2><Button danger disabled={saving || busy} onClick={() => { setDeleting('project'); setError(''); }}>删除项目</Button></div>
      <form className="project-settings-form" onSubmit={saveProject}>
        <label htmlFor="edit-project-name">项目名称<Input id="edit-project-name" required maxLength={120} value={details.name} disabled={saving} onChange={(e) => changeDetails({ name: e.target.value })} /></label>
        <div className="form-row"><label htmlFor="project-style">默认图片 / 视频风格<Input id="project-style" maxLength={255} placeholder="例如：清透水彩、都市写实" value={details.style} disabled={saving} onChange={(e) => changeDetails({ style: e.target.value })} /></label>
          <label htmlFor="project-aspect">默认画幅<Select id="project-aspect" value={details.aspect} disabled={saving} onChange={(aspect: '16:9' | '9:16') => changeDetails({ aspect })} options={aspects} /></label></div>
        <p className="muted">默认设置仅用于新建分集，已有分集保留各自的设置。</p>
        <label htmlFor="project-synopsis">故事梗概<Input.TextArea id="project-synopsis" maxLength={2000} rows={3} value={details.synopsis} disabled={saving} onChange={(e) => changeDetails({ synopsis: e.target.value })} /></label>
        {saveError && <p role="alert" className="notice">{saveError}</p>}
        <div className="project-form-actions"><span role="status">{saving ? '正在保存…' : dirty ? '有未保存的修改' : saved ? '已保存至服务端' : ''}</span><Button type="primary" htmlType="submit" loading={saving} disabled={!dirty}>保存项目信息</Button></div>
      </form>
    </section>
    <section className="overview-card" aria-labelledby="episodes-title">
      <div className="overview-heading"><h2 id="episodes-title">分集列表 <small>共 {total} 集</small></h2><div className="project-actions"><Button disabled={loading} onClick={() => setRevision((v) => v + 1)}>刷新</Button><Button type="primary" disabled={saving || busy} onClick={() => editEpisode('new')}>新增一集</Button></div></div>
      {loadError ? <Alert type="error" message={loadError} action={<Button onClick={() => setRevision((v) => v + 1)}>重试</Button>} /> : loading ? <div className="studio-empty" role="status"><Spin /> 正在加载分集…</div> : episodes.length ? <>
        <div className="episode-grid">{episodes.map((item, index) => <div className="episode-item" key={item.id}>
          <button type="button" className="episode-card" onClick={() => onOpenEpisode(item)} aria-label={`进入第 ${offset + index + 1} 集：${item.title}`}><span className="episode-number">第 {offset + index + 1} 集</span><strong>{item.title}</strong><span className="episode-synopsis">{item.synopsis || '尚未填写本集概要'}</span><span className="episode-enter">进入制作 →</span></button>
          <div className="episode-item-actions"><Button size="small" onClick={() => editEpisode(item)}>编辑分集</Button><Button danger size="small" onClick={() => { setDeleting(item); setError(''); }}>删除</Button></div>
        </div>)}</div>
        <Pagination current={offset / 20 + 1} pageSize={20} total={total} hideOnSinglePage showSizeChanger={false} onChange={(page) => setOffset((page - 1) * 20)} />
      </> : <p className="overview-empty">还没有分集。新增一集后，点击卡片进入制作流程。</p>}
    </section>
    <ProjectResourceLibrary projectId={session.projectId} />
    {editing && <Dialog title={editing === 'new' ? '新增一集' : '编辑分集'} canClose={!busy} onClose={() => setEditing(null)}><form className="studio-form" onSubmit={saveEpisode}>
      <label htmlFor="episode-title">标题<Input id="episode-title" autoFocus required maxLength={255} disabled={busy} value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} /></label>
      <label htmlFor="episode-synopsis">内容概要<Input.TextArea id="episode-synopsis" rows={3} maxLength={500} disabled={busy} value={draft.synopsis} onChange={(e) => setDraft({ ...draft, synopsis: e.target.value })} /></label>
      {editing === 'new' ? <p className="muted">画幅和风格使用项目已保存的默认设置。</p> : <><label htmlFor="episode-aspect">本集画幅<Select id="episode-aspect" value={draft.aspect} disabled={busy} options={aspects} onChange={(aspect: '16:9' | '9:16') => setDraft({ ...draft, aspect })} /></label><label htmlFor="episode-style">本集风格<Input id="episode-style" maxLength={255} value={draft.style} disabled={busy} onChange={(e) => setDraft({ ...draft, style: e.target.value })} /></label></>}
      {error && <p role="alert" className="notice">{error}</p>}
      <div className="dialog-actions"><Button disabled={busy} onClick={() => setEditing(null)}>取消</Button><Button htmlType="submit" type="primary" loading={busy}>{editing === 'new' ? '添加分集' : '保存分集'}</Button></div>
    </form></Dialog>}
    {deleting && <Dialog title={deleting === 'project' ? '删除项目' : '删除分集'} canClose={!busy} onClose={() => setDeleting(null)}>
      <p>确定删除“{deleting === 'project' ? project.name : deleting.title}”吗？此操作无法撤销。</p><p className="muted">存在关联分集、素材或制作内容时，需要先清理关联内容。</p>
      {error && <p role="alert" className="notice">{error}</p>}
      <div className="dialog-actions"><Button disabled={busy} onClick={() => setDeleting(null)}>取消</Button><Button danger type="primary" loading={busy} onClick={() => void remove()}>确认删除</Button></div>
    </Dialog>}
  </div>;
}
