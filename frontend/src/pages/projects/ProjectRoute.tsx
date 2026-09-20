import { useEffect } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { readProjects, writeProjects } from '../../data/demo';
import { readEpisodes } from '../../features/projects/project-detail-model';
import { ProjectDetailHeader } from '../../features/projects/ProjectDetailHeader';
import { ProjectOverview } from '../../features/projects/ProjectOverview';
import { EpisodePage } from './EpisodePage';
import type { ProjectSession } from '../../types/projects';
import { NotFoundPage } from '../../app/NotFoundPage';
import { episodePath, projectPath } from '../../app/paths';

export function ProjectRoute() {
  const { projectId, episodeId } = useParams();
  const navigate = useNavigate();
  const project = readProjects().find((item) => item.projectId === projectId);
  useEffect(() => {
    if (!projectId) return;
    const projects = readProjects();
    const current = projects.find((p) => p.projectId === projectId);
    if (!current) return;
    try { writeProjects([{ ...current, lastOpenedAt: new Date().toISOString() }, ...projects.filter((p) => p.projectId !== projectId)]); } catch { /* Opening remains possible when browser storage is full. */ }
  }, [projectId]);
  if (!project) return <NotFoundPage message="这个项目不在当前浏览器中，可能已被移除。" />;
  const session: ProjectSession = { projectId: project.projectId, projectSessionId: project.projectId, mode: 'write', project };
  const episodes = readEpisodes(project.projectId);
  const index = episodes.findIndex((item) => item.id === episodeId);
  if (episodeId) {
    if (index === -1) return <NotFoundPage message="这个分集不存在，请返回项目管理选择其他分集。" />;
    return <EpisodePage session={session} episode={episodes[index]} number={index + 1} ready onBack={() => navigate(projectPath(project.projectId))} />;
  }
  return <section className="projects-home">
    <ProjectDetailHeader session={session} canClose onBack={() => navigate('/projects')} onClose={() => navigate('/projects')} />
    <ProjectOverview key={project.projectId} session={session} onOpenEpisode={(item) => navigate(episodePath(project.projectId, item.id))} />
  </section>;
}
