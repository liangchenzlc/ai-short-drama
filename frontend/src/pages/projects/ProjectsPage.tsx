import { useEffect, useState, type FormEvent } from 'react';
import { Alert, Button, Input, Pagination, Radio, Skeleton } from 'antd';
import { useNavigate } from 'react-router-dom';
import { Dialog } from '../../components/ui/Dialog';
import { projectPath } from '../../app/paths';
import { ProjectList } from '../../features/projects/ProjectList';
import { CanvasArchiveDialog } from '../../features/projects/CanvasArchiveDialog';
import { projectsApi, projectError, type RemoteProject, type WorkspaceMode } from '../../api/modules/projects';
import { clearProjectCreation, prepareProjectCreation, readProjectCreation } from '../../features/projects/project-creation';
import { useAuth } from '../../features/auth/AuthSession';
import { ApiError, isCancelled } from '../../api/http';
import { Icon } from '../../components/ui/Icon';
import { PageHeader } from '../../components/ui/Workspace';
import { StudioFeedback } from '../../components/ui/StudioFeedback';
import '../../features/projects/projects.css';

export function ProjectsPage() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const [projects, setProjects] = useState<RemoteProject[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState('');
  useEffect(() => {
    if (search.trim() === query) return;
    const timer = window.setTimeout(() => { setQuery(search.trim()); setOffset(0); }, 300);
    return () => window.clearTimeout(timer);
  }, [search, query]);
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [creating, setCreating] = useState(false);
  const [archiveOpen, setArchiveOpen] = useState(false);
  const [deleting, setDeleting] = useState<RemoteProject | null>(null);
  const [busy, setBusy] = useState(false);
  const [name, setName] = useState('');
  const [workspaceMode, setWorkspaceMode] = useState<WorkspaceMode>('standard');
  const [error, setError] = useState('');
  function openCreation() {
    try {
      const pending = readProjectCreation(sessionStorage, user?.id ?? 'anonymous');
      setName(pending?.body.name ?? '');
      setWorkspaceMode(pending?.body.workspace_mode ?? 'standard');
      setError(pending ? '上次创建结果尚未确认。保留原内容重试会核对同一次创建，不会重复创建项目。' : '');
    } catch { setName(''); setWorkspaceMode('standard'); setError(''); }
    setCreating(true);
  }
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setLoadError('');
    void projectsApi.list(offset, query, controller.signal).then((page) => {
      if (controller.signal.aborted) return;
      if (!page.items.length && offset > 0 && page.total <= offset) {
        setOffset(Math.max(0, Math.ceil(page.total / 20) - 1) * 20); return;
      }
      setProjects(page.items); setTotal(page.total);
    }).catch((cause) => { if (!isCancelled(cause) && !controller.signal.aborted) setLoadError(projectError(cause)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [offset, query, revision]);
  async function create(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    if (!name.trim()) { setError('请输入项目名称。'); return; }
    if (workspaceMode === 'infinite_canvas' && !user) { setError('无限画布需要登录账号后创建。'); return; }
    setBusy(true); setError('');
    try {
      const body = { name: name.trim(), workspace_mode: workspaceMode };
      const attempt = prepareProjectCreation(sessionStorage, user?.id ?? 'anonymous', body, () => crypto.randomUUID());
      const project = await projectsApi.create(body, attempt.key);
      clearProjectCreation(sessionStorage, user?.id ?? 'anonymous', attempt.key);
      navigate(projectPath(project.projectId));
    } catch (cause) { setError(workspaceMode === 'infinite_canvas' ? `${projectError(cause)} 保留原内容重试会核对同一次创建。` : projectError(cause, 'create')); }
    finally { setBusy(false); }
  }
  return <section className="projects-home">
    <PageHeader title="项目管理" description="选择项目，开始创作。" actions={<><Button onClick={() => setArchiveOpen(true)} disabled={!user}>画布导入导出</Button><Button type="primary" icon={<Icon name="plus" size={16} />} onClick={openCreation}>新建项目</Button></>} />
    <div className="project-collection-toolbar">
      <h2>{query ? '搜索结果' : '全部项目'}{!loading && !loadError && <span className="project-count">{total}</span>}</h2>
      <div className="project-collection-controls">
        <span className="project-sort-label">按最近打开排序</span>
        <Input className="project-search" prefix={<Icon name="search" size={16} />} value={search} onChange={(event) => setSearch(event.target.value)} aria-label="搜索项目名称" placeholder="搜索项目名称" maxLength={120} allowClear onPressEnter={() => { setQuery(search.trim()); setOffset(0); }} />
      </div>
    </div>
    {loadError ? <Alert type="error" showIcon message={loadError} action={<Button onClick={() => setRevision((v) => v + 1)}>重试</Button>} />
      : loading ? <div className="project-skeleton" role="status" aria-label="正在加载项目">{[0, 1, 2].map(item => <Skeleton key={item} title paragraph={{ rows: 2 }} />)}</div>
      : <><ProjectList recent={projects} filtered={!!query} onCreate={openCreation} onClear={() => { setSearch(''); setQuery(''); setOffset(0); }} onDelete={setDeleting} />
        <Pagination current={offset / 20 + 1} pageSize={20} total={total} hideOnSinglePage showSizeChanger={false} onChange={(page) => setOffset((page - 1) * 20)} /></>}
    {archiveOpen && <CanvasArchiveDialog onClose={() => setArchiveOpen(false)} onImported={() => setRevision(value => value + 1)} />}
    {deleting && <ProjectDeleteDialog project={deleting} onClose={() => setDeleting(null)} onDeleted={() => { setDeleting(null); setRevision(value => value + 1); }} />}
    {creating && <Dialog title="新建项目" className="project-create-dialog project-start-dialog" canClose={!busy} onClose={() => setCreating(false)}>
      <form className="project-create-form" onSubmit={create}>
        <label className="project-name-field" htmlFor="project-name">项目名称<Input id="project-name" autoFocus required maxLength={120} placeholder="例如：雨夜借光" value={name} disabled={busy} onChange={(e) => setName(e.target.value)} /></label>
        <fieldset className="project-mode-field">
          <legend>项目模式</legend>
          <Radio.Group className="project-mode-options" name="workspace-mode" value={workspaceMode} disabled={busy} onChange={(event) => setWorkspaceMode(event.target.value)}>
            <Radio className="project-mode-option" value="standard" aria-label="创作模式">
              <span className="project-mode-copy"><Icon name="film" size={22} /><strong>创作模式</strong><span>创建后直接进入剧本、素材、分镜与成片创作。</span></span>
            </Radio>
            <Radio className="project-mode-option" value="infinite_canvas" aria-label="无限画布模式">
              <span className="project-mode-copy"><Icon name="scene" size={22} /><strong>无限画布模式</strong><span>创建后进入主画布，自由组织节点、连接素材并创作。</span></span>
            </Radio>
          </Radio.Group>
        </fieldset>
        {error && <p role="alert" className="notice">{error}</p>}
        <div className="dialog-actions"><Button disabled={busy} onClick={() => setCreating(false)}>取消</Button><Button loading={busy} type="primary" htmlType="submit">创建项目</Button></div>
      </form>
    </Dialog>}
  </section>;
}

function ProjectDeleteDialog({ project, onClose, onDeleted }: { project: RemoteProject; onClose: () => void; onDeleted: () => void }) {
  const [target, setTarget] = useState(project);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<{ message: string; conflict: boolean } | null>(null);
  async function remove() {
    if (busy || failure?.conflict) return;
    setBusy(true); setFailure(null);
    try { await projectsApi.remove(target.projectId, target.rowVersion); onDeleted(); }
    catch (cause) {
      if (cause instanceof ApiError && cause.status === 404) { onDeleted(); return; }
      const conflict = cause instanceof ApiError && cause.status === 409;
      setFailure({ conflict, message: conflict ? '项目已被其他页面修改，请重新核对后再决定是否删除。' : projectError(cause) });
    } finally { setBusy(false); }
  }
  async function recheck() {
    if (busy) return;
    setBusy(true);
    try { setTarget(await projectsApi.get(target.projectId)); setFailure(null); }
    catch (cause) {
      if (cause instanceof ApiError && cause.status === 404) { onDeleted(); return; }
      setFailure(previous => ({ conflict: previous?.conflict ?? false, message: projectError(cause) }));
    } finally { setBusy(false); }
  }
  return <Dialog title="删除项目" className="studio-confirm-dialog project-delete-dialog" canClose={!busy} onClose={onClose}>
    <div className="studio-confirm-content is-danger"><span className="studio-confirm-icon"><Icon name="warning" size={22} /></span><p>确定删除「{target.name}」？删除后项目及其中的创作内容将无法访问。已受理的生成任务不会自动取消。</p></div>
    {failure && <StudioFeedback type={failure.conflict ? 'warning' : 'error'} title="删除项目" message={failure.message} noticeKey={`project:${target.projectId}:delete`} actions={<Button disabled={busy} onClick={() => void recheck()}>重新核对项目</Button>} />}
    <div className="dialog-actions"><Button disabled={busy} autoFocus data-dialog-autofocus onClick={onClose}>取消</Button>{failure?.conflict
      ? <Button key="recheck" type="primary" aria-label="重新核对项目" aria-busy={busy} disabled={busy} loading={busy} onClick={() => void recheck()}>重新核对项目</Button>
      : <Button key="delete" type="primary" danger aria-label="删除项目" aria-busy={busy} disabled={busy} loading={busy} onClick={() => void remove()}>删除项目</Button>}</div>
  </Dialog>;
}
