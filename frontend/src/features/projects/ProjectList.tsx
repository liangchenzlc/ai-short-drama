import { Button } from 'antd';
import type { RemoteProject } from '../../api/modules/projects';
import { ProjectCard } from './ProjectCard';

export function ProjectList({ recent, total, filtered, onCreate, onClear }: {
  recent: RemoteProject[];
  total: number;
  filtered: boolean;
  onCreate: () => void;
  onClear: () => void;
}) {
  return <section className="project-collection" aria-label="项目列表">
    <div className="project-list-heading"><h2>{filtered ? '搜索结果' : '全部项目'} <span>{total}</span></h2><span>按最近打开排序</span></div>
    {recent.length ? <div className="project-card-grid">{recent.map(project => <ProjectCard key={project.projectId} project={project} />)}</div>
      : <div className="studio-empty"><h3>{filtered ? '没有找到项目' : '开始你的第一部短剧'}</h3><p>{filtered ? '试试其他项目名称。' : '点击「新建项目」，从故事梗概开始。'}</p><Button type={filtered ? 'default' : 'primary'} onClick={filtered ? onClear : onCreate}>{filtered ? '清除搜索' : '新建项目'}</Button></div>}
  </section>;
}
