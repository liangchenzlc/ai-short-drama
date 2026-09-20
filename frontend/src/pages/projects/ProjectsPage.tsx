import { useState, type FormEvent } from 'react';
import { Button, Input, Select } from 'antd';
import { Dialog } from '../../components/ui/Dialog';
import { useNavigate } from 'react-router-dom';
import { projectPath, episodePath } from '../../app/paths';
import { ProjectList } from '../../features/projects/ProjectList';
import { readProjects, writeProjects } from '../../data/demo';
import { Icon } from '../../components/ui/Icon';

export function ProjectsPage() {
  const navigate = useNavigate();
  const [projects, setProjects] = useState(readProjects);
  const [creating, setCreating] = useState(false);
  const [opening, setOpening] = useState(false);
  const [name, setName] = useState('');
  const [aspect, setAspect] = useState<'16:9' | '9:16'>('16:9');
  const [seconds, setSeconds] = useState('60');
  const [query, setQuery] = useState('');
  const [error, setError] = useState('');
  function open(id: string) { navigate(projectPath(id)); }
  function create(event: FormEvent) {
    event.preventDefault();
    if (!name.trim()) { setError('请输入项目名称。'); return; }
    if (!Number.isInteger(Number(seconds)) || Number(seconds) < 1 || Number(seconds) > 3600) { setError('目标时长请输入 1–3600 的整数。'); return; }
    const project = { projectId: crypto.randomUUID(), name: name.trim(), aspect, targetMs: Number(seconds) * 1000, lastOpenedAt: new Date().toISOString() };
    try {
      const next = [project, ...projects]; writeProjects(next); setProjects(next);
      navigate(projectPath(project.projectId));
    } catch { setError('项目未能保存，请检查浏览器存储空间后重试。'); }
  }
  return <section className="projects-home">
    <>
      <div className="studio-page-head"><div><h1>项目管理</h1><p>管理你的故事、分集与创作素材。</p></div>
      <div className="project-actions"><Button onClick={() => setOpening(true)}>打开项目</Button><Button type="primary" icon={<Icon name="plus" size={16} />} onClick={() => { setError(''); setCreating(true); }}>新建项目</Button></div></div>
      <ProjectList recent={projects} disabled={false} onOpen={open} onContinue={(id, item) => navigate(episodePath(id, item.id))} />
    </>
    {creating && <Dialog title="新建项目" className="project-create-dialog" onClose={() => setCreating(false)}>
      <form className="project-form project-create-form" onSubmit={create}>
        <label htmlFor="project-name">项目名称<Input id="project-name" autoFocus required maxLength={120} placeholder="例如：雨夜借光" value={name} onChange={(e) => setName(e.target.value)} /></label>
        <div className="form-row"><label htmlFor="project-aspect">画幅<Select id="project-aspect" value={aspect} onChange={setAspect} options={[{ value: '16:9', label: '横屏 16:9' }, { value: '9:16', label: '竖屏 9:16' }]} /></label><label htmlFor="project-seconds">目标时长（秒）<Input id="project-seconds" type="number" required min={1} max={3600} step={1} value={seconds} onChange={(e) => setSeconds(e.target.value)} /></label></div>
        <p>输出设置：1080p · 24 帧/秒</p><p className="muted">项目与编辑内容保存在当前浏览器，无需选择本机目录。</p>
        {error && <p role="alert" className="notice">{error}</p>}
        <div className="dialog-actions"><Button onClick={() => setCreating(false)}>取消</Button><Button type="primary" htmlType="submit">创建项目</Button></div>
      </form>
    </Dialog>}
    {opening && <Dialog title="打开项目" onClose={() => setOpening(false)}>
      <Input aria-label="搜索项目" placeholder="搜索项目名称" value={query} onChange={(e) => setQuery(e.target.value)} />
      <div className="web-project-picker">{projects.filter((p) => p.name.includes(query.trim())).map((p) => <button key={p.projectId} onClick={() => open(p.projectId)}><strong>{p.name}</strong><span>{p.aspect} · {p.targetMs / 1000} 秒</span></button>)}</div>
      {!projects.some((p) => p.name.includes(query.trim())) && <p className="studio-empty">未找到项目，试试其他名称。</p>}
    </Dialog>}
  </section>;
}
