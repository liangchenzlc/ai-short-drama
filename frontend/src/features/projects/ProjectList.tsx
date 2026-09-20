import type { RecentProject } from '../../types/projects';
import { ProjectCard } from './ProjectCard';
import { readEpisodes, type Episode } from './project-detail-model';
import { Icon } from '../../components/ui/Icon';
export function ProjectList({ recent, disabled, onOpen, onContinue }: { recent: RecentProject[]; disabled: boolean; onOpen: (id: string) => void; onContinue: (id: string, episode: Episode) => void }) {
  const first = recent.find((p) => readEpisodes(p.projectId).length > 0);
  const episodes = first ? readEpisodes(first.projectId) : [];
  return <div className="project-dashboard">
    <section className="recent-projects" aria-label="最近项目">
      <div className="project-list-heading"><h2>全部项目 <span>{recent.length}</span></h2><span>按最近打开排序</span></div>
      {recent.length ? <div className="web-project-list">{recent.map((p) => <ProjectCard key={p.projectId} project={p} disabled={disabled} onOpen={onOpen} />)}</div> : <div className="studio-empty"><h3>开始你的第一部短剧</h3><p>点击「新建项目」，从故事梗概开始。</p></div>}
      <p className="project-storage-note">演示内容可自由编辑，修改自动保存在当前浏览器。</p>
    </section>
    <aside className="project-context">
      <section className="continue-project"><div className="context-heading"><Icon name="film" size={18} /><h2>继续创作</h2></div>
        {first ? <><h3>{first.name}</h3><p>从分集进入，继续打磨你的故事。</p><div className="continue-episodes">{episodes.slice(0, 3).map((e, i) => <button key={e.id} disabled={disabled} onClick={() => onContinue(first.projectId, e)}><span>{String(i + 1).padStart(2, '0')}</span><strong>{e.title}</strong><Icon name="arrow" size={16}/></button>)}</div></> : <p>创建项目后，在这里继续制作。</p>}
      </section>
      <section className="workflow-guide"><h2>从故事到镜头</h2><ol><li><strong>整理剧本</strong><span>导入原文，确认本集故事。</span></li><li><strong>准备素材</strong><span>统一角色、场景与道具。</span></li><li><strong>制作分镜</strong><span>细化镜头和画面提示词。</span></li></ol><p>当前为演示空间，不调用 AI 服务。</p></section>
    </aside>
  </div>;
}
