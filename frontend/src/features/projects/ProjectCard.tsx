import type { RemoteProject } from '../../api/modules/projects';
import { Icon } from '../../components/ui/Icon';

export function ProjectCard({ project, onOpen, disabled }: { project: RemoteProject; onOpen: (id: string) => void; disabled: boolean }) {
  const date = new Date(project.lastOpenedAt);
  return <button className="web-project-row" disabled={disabled} onClick={() => onOpen(project.projectId)} aria-label={`打开项目 ${project.name}`}>
    <span className="project-slate"><Icon name="film" size={32} /><span>{project.aspect}</span></span>
    <span className="project-row-copy"><strong>{project.name}</strong><span className="project-row-synopsis">{project.synopsis || '故事尚未开始，打开项目填写梗概。'}</span><span className="project-row-meta"><span>{project.episodeCount} 集</span><span>{project.style || '未设置风格'}</span></span></span>
    <span className="project-row-tail"><time>{Number.isNaN(date.getTime()) ? '尚未打开' : date.toLocaleDateString('zh-CN', {month: '2-digit', day: '2-digit'})}</time><Icon name="arrow" size={18} /></span>
  </button>;
}
