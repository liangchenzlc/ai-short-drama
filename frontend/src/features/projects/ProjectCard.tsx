import { Link } from 'react-router-dom';
import type { RemoteProject } from '../../api/modules/projects';
import { projectPath } from '../../app/paths';
import { Icon } from '../../components/ui/Icon';

export function ProjectCard({ project }: { project: RemoteProject }) {
  const date = new Date(project.lastOpenedAt);
  return <Link className="project-tile" to={projectPath(project.projectId)} aria-label={`打开项目 ${project.name}`}>
    <div className="project-tile-media"><Icon name="film" size={32} /><span>{project.aspect}</span></div>
    <div className="project-tile-copy"><h3>{project.name}</h3><p>{project.synopsis || '尚未填写故事梗概'}</p><div className="project-tile-meta"><span>{project.workspaceMode === 'infinite_canvas' ? `${project.canvasCount} 个画布` : `${project.episodeCount} 集`}</span><span>{project.style || '未设置风格'}</span><span>{project.workspaceMode === 'infinite_canvas' ? '无限画布模式' : '标准模式'}</span></div></div>
    <div className="project-tile-footer"><time dateTime={Number.isNaN(date.getTime()) ? undefined : date.toISOString()}>{Number.isNaN(date.getTime()) ? '尚未打开' : date.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })}</time><Icon name="arrow" size={16} /></div>
  </Link>;
}
