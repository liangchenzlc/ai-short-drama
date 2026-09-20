import { useEffect, useState } from 'react';
import { Alert, Button, Spin } from 'antd';
import { useNavigate, useParams } from 'react-router-dom';
import { projectsApi, projectError, type RemoteEpisode, type RemoteProject } from '../../api/modules/projects';
import { ProjectDetailHeader } from '../../features/projects/ProjectDetailHeader';
import { ProjectOverview } from '../../features/projects/ProjectOverview';
import { EpisodePage } from './EpisodePage';
import type { ProjectSession } from '../../types/projects';
import { episodePath, projectPath } from '../../app/paths';

export function ProjectRoute() {
  const { projectId = '', episodeId } = useParams();
  return <RemoteProjectRoute key={`${projectId}:${episodeId ?? ''}`} projectId={projectId} episodeId={episodeId} />;
}
function RemoteProjectRoute({ projectId, episodeId }: { projectId: string; episodeId?: string }) {
  const navigate = useNavigate();
  const [project, setProject] = useState<RemoteProject | null>(null);
  const [episode, setEpisode] = useState<RemoteEpisode | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError('');
    void Promise.all([
      projectsApi.get(projectId, controller.signal),
      episodeId ? projectsApi.getEpisode(projectId, episodeId, controller.signal) : Promise.resolve(null),
    ]).then(([result, item]) => {
      if (!controller.signal.aborted) { setProject(result); setEpisode(item); }
    }).catch((cause) => { if (!controller.signal.aborted) setError(projectError(cause)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [projectId, episodeId, revision]);
  useEffect(() => {
    const controller = new AbortController();
    // Recent-open tracking must not prevent viewing the project if tracking fails.
    void projectsApi.open(projectId, controller.signal).catch(() => {});
    return () => controller.abort();
  }, [projectId]);
  if (loading) return <div className="studio-empty" role="status"><Spin /> 正在打开项目…</div>;
  if (error || !project) return <section className="projects-home"><Button onClick={() => navigate('/projects')}>返回项目管理</Button><Alert type="error" showIcon message={error || '项目不存在。'} action={<Button onClick={() => setRevision((v) => v + 1)}>重试</Button>} /></section>;
  const session: ProjectSession = { projectId, projectSessionId: projectId, mode: 'write', project };
  if (episodeId && episode) return <EpisodePage session={session} episode={episode} number={episode.number ?? 1} ready onBack={() => navigate(projectPath(projectId))} />;
  return <section className="projects-home">
    <ProjectDetailHeader session={session} canClose onBack={() => navigate('/projects')} onClose={() => navigate('/projects')} />
    <ProjectOverview session={session} project={project} onProjectUpdated={setProject} onDeleted={() => navigate('/projects', { replace: true })} onOpenEpisode={(item) => navigate(episodePath(projectId, item.id))} />
  </section>;
}
