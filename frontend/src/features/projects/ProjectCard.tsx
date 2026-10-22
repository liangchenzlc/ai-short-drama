import { useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { Button, Dropdown } from 'antd';
import type { RemoteProject } from '../../api/modules/projects';
import { projectPath } from '../../app/paths';
import { Icon } from '../../components/ui/Icon';
export function ProjectCard({ project, onDelete }: { project: RemoteProject; onDelete: (project: RemoteProject) => void }) {
  const [failedCover, setFailedCover] = useState<string | null>(null);
  const moreTrigger = useRef<HTMLButtonElement | null>(null);
  const date = new Date(project.lastOpenedAt);
  const hasDate = !Number.isNaN(date.getTime());
  const isCanvas = project.workspaceMode === 'infinite_canvas';
  const coverUrl = project.coverUrl === failedCover ? null : project.coverUrl;
  return <article className="project-tile">
    <Link className="project-tile-link" to={projectPath(project.projectId)} target="_blank" rel="noopener noreferrer" aria-label={`打开项目 ${project.name}`}>
    <div className="project-tile-media">
      {coverUrl ? <img src={coverUrl} alt="" loading="lazy" decoding="async" onError={() => setFailedCover(coverUrl)} />
        : <div className="project-tile-placeholder"><Icon name={isCanvas ? 'scene' : 'film'} size={36} /><span>{project.coverUrl ? '封面暂不可用' : '暂无封面'}</span></div>}
      <span className="project-tile-mode">{isCanvas ? `无限画布模式 / ${project.canvasCount} 个画布` : '创作模式'}</span>
    </div>
    <div className="project-tile-copy">
      <h3 title={project.name}>{project.name}</h3>
      <time className="project-tile-recent" dateTime={hasDate ? date.toISOString() : undefined} title={hasDate ? `最近打开 ${date.toLocaleString('zh-CN')}` : '尚未打开'}>{hasDate ? date.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' }) : '未打开'}</time>
    </div>
    </Link>
    <Dropdown trigger={['click']} menu={{ items: [{ key: 'delete', label: '删除项目', danger: true }], onClick: () => { moreTrigger.current?.focus(); onDelete(project); } }}>
      <Button ref={moreTrigger} type="text" className="project-tile-more" icon={<Icon name="more" size={18} />} aria-label={`项目 ${project.name} 的更多操作`} title="更多操作" />
    </Dropdown>
  </article>;
}
