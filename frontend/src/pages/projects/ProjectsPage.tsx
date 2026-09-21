import { useEffect, useState, type FormEvent } from 'react';
import { Alert, Button, Input, Pagination, Select, Skeleton } from 'antd';
import { useNavigate } from 'react-router-dom';
import { Dialog } from '../../components/ui/Dialog';
import { projectPath, episodePath } from '../../app/paths';
import { ProjectList } from '../../features/projects/ProjectList';
import { projectsApi, projectError, type RemoteProject } from '../../api/modules/projects';
import { isCancelled } from '../../api/http';
import { Icon } from '../../components/ui/Icon';

export function ProjectsPage() {
  const navigate = useNavigate();
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
  const [error, setError] = useState('');
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
    setBusy(true); setError('');
    try {
      const project = await projectsApi.create({ name: name.trim(), aspect, synopsis: '', style: '' });
      navigate(projectPath(project.projectId));
    } catch (cause) { setError(projectError(cause, 'create')); }
    finally { setBusy(false); }
  }
  return <section className="projects-home">
    <div className="studio-page-head"><div><h1>项目管理</h1><p>管理你的故事、分集与创作素材。</p></div>
      <div className="project-actions"><Button onClick={() => setRevision((v) => v + 1)} disabled={loading}>刷新</Button><Button type="primary" icon={<Icon name="plus" size={16} />} onClick={() => { setName(''); setError(''); setCreating(true); }}>新建项目</Button></div></div>
    <div className="project-search-bar"><Input.Search className="project-search" value={search} onChange={(event) => setSearch(event.target.value)} aria-label="搜索项目名称" placeholder="搜索项目名称" maxLength={120} allowClear onSearch={(value) => { setQuery(value.trim()); setOffset(0); }} /><span>找到故事，接着创作</span></div>
    {loadError ? <Alert type="error" showIcon message={loadError} action={<Button onClick={() => setRevision((v) => v + 1)}>重试</Button>} />
      : loading ? <div className="project-skeleton" role="status" aria-label="正在加载项目">{[0, 1, 2].map(item => <Skeleton key={item} title paragraph={{ rows: 2 }} />)}</div>
      : <><ProjectList recent={projects} total={total} filtered={!!query} disabled={busy} onCreate={() => { setName(''); setError(''); setCreating(true); }} onClear={() => { setSearch(''); setQuery(''); setOffset(0); }} onOpen={(id) => navigate(projectPath(id))} onContinue={(id, item) => navigate(episodePath(id, item.id))} />
        <Pagination current={offset / 20 + 1} pageSize={20} total={total} hideOnSinglePage showSizeChanger={false} onChange={(page) => setOffset((page - 1) * 20)} /></>}
    {creating && <Dialog title="新建项目" className="project-create-dialog" canClose={!busy} onClose={() => setCreating(false)}>
      <form className="project-form project-create-form" onSubmit={create}>
        <label htmlFor="project-name">项目名称<Input id="project-name" autoFocus required maxLength={120} placeholder="例如：雨夜借光" value={name} disabled={busy} onChange={(e) => setName(e.target.value)} /></label>
        <label htmlFor="project-aspect">默认画幅<Select id="project-aspect" value={aspect} disabled={busy} onChange={setAspect} options={[{ value: '16:9', label: '横屏 16:9' }, { value: '9:16', label: '竖屏 9:16' }]} /></label>
        <p className="muted">创建后可填写故事梗概，并添加第一集。</p>
        {error && <p role="alert" className="notice">{error}</p>}
        <div className="dialog-actions"><Button disabled={busy} onClick={() => setCreating(false)}>取消</Button><Button loading={busy} type="primary" htmlType="submit">创建项目</Button></div>
      </form>
    </Dialog>}
  </section>;
}
