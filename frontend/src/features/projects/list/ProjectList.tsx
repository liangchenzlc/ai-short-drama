import { ProjectCard } from '@host/features/projects/list/ProjectCard';
import type { RemoteProject } from '@host/types/projects/management';
import { Button } from 'antd';
export function ProjectList({
  recent,
  filtered,
  onCreate,
  onClear,
  onDelete,
}: {
  recent: RemoteProject[];
  filtered: boolean;
  onCreate: () => void;
  onClear: () => void;
  onDelete: (project: RemoteProject) => void;
}) {
  return (
    <section className="project-collection" aria-label="项目列表">
      {recent.length ? (
        <div className="project-card-grid">
          {recent.map((project) => (
            <ProjectCard key={project.projectId} project={project} onDelete={onDelete} />
          ))}
        </div>
      ) : (
        <div className="studio-empty">
          <h3>{filtered ? '没有找到项目' : '开始你的第一部短剧'}</h3>
          <p>{filtered ? '试试其他项目名称。' : '点击「新建项目」，直接进入创作流程。'}</p>
          <Button type={filtered ? 'default' : 'primary'} onClick={filtered ? onClear : onCreate}>
            {filtered ? '清除搜索' : '新建项目'}
          </Button>
        </div>
      )}
    </section>
  );
}
