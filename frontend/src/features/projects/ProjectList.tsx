import { useEffect, useState } from 'react';
import { Button } from 'antd';
import { projectsApi, projectError, type RemoteProject, type RemoteEpisode } from '../../api/modules/projects';
import { ProjectCard } from './ProjectCard';
import { Icon } from '../../components/ui/Icon';

export function ProjectList({ recent, total, filtered, disabled, onOpen, onContinue, onCreate, onClear }: { recent: RemoteProject[]; total: number; filtered: boolean; disabled: boolean; onCreate: () => void; onClear: () => void; onOpen: (id: string) => void; onContinue: (id: string, episode: RemoteEpisode) => void }) {
  const first = recent.find((p) => p.episodeCount > 0);
  const [episodes, setEpisodes] = useState<RemoteEpisode[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setEpisodes([]); setError(''); setLoading(!!first);
    if (first) void projectsApi.listEpisodes(first.projectId, 0, 3, controller.signal).then((page) => {
      if (!controller.signal.aborted) setEpisodes(page.items);
    }).catch((cause) => { if (!controller.signal.aborted) setError(projectError(cause)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [first?.projectId, revision]);
  return <div className="project-dashboard">
    <section className="recent-projects" aria-label="最近项目">
      <div className="project-list-heading"><h2>{filtered ? '搜索结果' : '全部项目'} <span>{total}</span></h2><span>按最近打开排序</span></div>
      {recent.length ? <div className="web-project-list">{recent.map((p) => <ProjectCard key={p.projectId} project={p} disabled={disabled} onOpen={onOpen} />)}</div> : <div className="studio-empty"><h3>{filtered ? '没有找到项目' : '开始你的第一部短剧'}</h3><p>{filtered ? '试试其他项目名称。' : '点击「新建项目」，从故事梗概开始。'}</p><Button type={filtered ? 'default' : 'primary'} onClick={filtered ? onClear : onCreate}>{filtered ? '清除搜索' : '新建第一个项目'}</Button></div>}
    </section>
    <aside className="project-context">
      <section className="continue-project"><div className="context-heading"><Icon name="film" size={18} /><h2>继续创作</h2></div>
        {loading ? <p role="status">正在加载分集…</p> : error ? <><p role="alert">{error}</p><Button onClick={() => setRevision((v) => v + 1)}>重试</Button></> : first ? <><h3>{first.name}</h3><p>从分集进入，继续打磨你的故事。</p><div className="continue-episodes">{episodes.map((e, i) => <button key={e.id} disabled={disabled} onClick={() => onContinue(first.projectId, e)}><span>{String(i + 1).padStart(2, '0')}</span><strong>{e.title}</strong><Icon name="arrow" size={16}/></button>)}</div></> : <p>添加分集后，在这里继续制作。</p>}
      </section>
      <section className="workflow-guide"><h2>从故事到镜头</h2><ol><li><strong>整理剧本</strong><span>导入原文，确认本集故事。</span></li><li><strong>准备素材</strong><span>统一角色、场景与道具。</span></li><li><strong>制作分镜</strong><span>细化镜头和画面提示词。</span></li></ol></section>
    </aside>
  </div>;
}
