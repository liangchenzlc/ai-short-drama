import { useEffect, useState, type FormEvent } from 'react';
import { Alert, Button, Input, Pagination, Radio, Select, Skeleton } from 'antd';
import { useNavigate } from 'react-router-dom';
import { Dialog } from '../../components/ui/Dialog';
import { projectPath } from '../../app/paths';
import { ProjectList } from '../../features/projects/ProjectList';
import { projectsApi, projectError, type RemoteProject, type WorkspaceMode } from '../../api/modules/projects';
import { clearProjectCreation, prepareProjectCreation, readProjectCreation } from '../../features/projects/project-creation';
import { useAuth } from '../../features/auth/AuthSession';
import { isCancelled } from '../../api/http';
import { Icon } from '../../components/ui/Icon';
import { PageHeader } from '../../components/ui/Workspace';
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
    const timer = window.setTimeout(() => { setQuery(search.trim()); setOffset(0); }, 300);
    return () => window.clearTimeout(timer);
  }, [search]);
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [name, setName] = useState('');
  const [aspect, setAspect] = useState<'16:9' | '9:16'>('16:9');
  const [workspaceMode, setWorkspaceMode] = useState<WorkspaceMode>('standard');
  const [error, setError] = useState('');
  function openCreation() {
    try {
      const pending = user ? readProjectCreation(sessionStorage, user.id) : null;
      setName(pending?.body.name ?? '');
      if (pending) setAspect(pending.body.aspect);
      setWorkspaceMode(pending ? 'infinite_canvas' : 'standard');
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
      const body = { name: name.trim(), aspect, synopsis: '', style: '', workspace_mode: workspaceMode };
      const attempt = workspaceMode === 'infinite_canvas' && user
        ? prepareProjectCreation(sessionStorage, user.id, body, () => crypto.randomUUID()) : null;
      const project = await projectsApi.create(body, attempt?.key);
      if (attempt && user) clearProjectCreation(sessionStorage, user.id, attempt.key);
      navigate(projectPath(project.projectId));
    } catch (cause) { setError(workspaceMode === 'infinite_canvas' ? `${projectError(cause)} 保留原内容重试会核对同一次创建。` : projectError(cause, 'create')); }
    finally { setBusy(false); }
  }
  return <section className="projects-home">
    <PageHeader title="项目管理" description="管理你的故事、分集与创作素材。" actions={<Button type="primary" icon={<Icon name="plus" size={16} />} onClick={openCreation}>新建项目</Button>} />
    <div className="project-search-bar"><Input.Search className="project-search" value={search} onChange={(event) => setSearch(event.target.value)} aria-label="搜索项目名称" placeholder="搜索项目名称" maxLength={120} allowClear onSearch={(value) => { setQuery(value.trim()); setOffset(0); }} /><span className="project-search-hint">找到故事，接着创作</span></div>
    {loadError ? <Alert type="error" showIcon message={loadError} action={<Button onClick={() => setRevision((v) => v + 1)}>重试</Button>} />
      : loading ? <div className="project-skeleton" role="status" aria-label="正在加载项目">{[0, 1, 2].map(item => <Skeleton key={item} title paragraph={{ rows: 2 }} />)}</div>
      : <><ProjectList recent={projects} total={total} filtered={!!query} onCreate={openCreation} onClear={() => { setSearch(''); setQuery(''); setOffset(0); }} />
        <Pagination current={offset / 20 + 1} pageSize={20} total={total} hideOnSinglePage showSizeChanger={false} onChange={(page) => setOffset((page - 1) * 20)} /></>}
    {creating && <Dialog title="新建项目" className="project-create-dialog" canClose={!busy} onClose={() => setCreating(false)}>
      <form className="project-form project-create-form" onSubmit={create}>
        <fieldset className="project-mode-field"><legend>创作模式</legend><Radio.Group name="workspace-mode" value={workspaceMode} disabled={busy} onChange={(event) => setWorkspaceMode(event.target.value)} options={[{ value: 'standard', label: '标准模式' }, { value: 'infinite_canvas', label: '无限画布模式' }]} /></fieldset>
        <label htmlFor="project-name">项目名称<Input id="project-name" autoFocus required maxLength={120} placeholder="例如：雨夜借光" value={name} disabled={busy} onChange={(e) => setName(e.target.value)} /></label>
        <label htmlFor="project-aspect">默认画幅<Select id="project-aspect" value={aspect} disabled={busy} onChange={setAspect} options={[{ value: '16:9', label: '横屏 16:9' }, { value: '9:16', label: '竖屏 9:16' }]} /></label>
        <p className="muted">{workspaceMode === 'standard' ? '创建后可填写故事梗概，并添加第一集。' : '创建后进入主画布，自由组织节点、连接素材并创作。'}</p>
        {error && <p role="alert" className="notice">{error}</p>}
        <div className="dialog-actions"><Button disabled={busy} onClick={() => setCreating(false)}>取消</Button><Button loading={busy} type="primary" htmlType="submit">创建项目</Button></div>
      </form>
    </Dialog>}
  </section>;
}
