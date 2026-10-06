import { Suspense, useEffect, useState } from 'react';
import { Alert, Button, Spin } from 'antd';
import { useNavigate, useParams } from 'react-router-dom';
import { projectsApi, projectError, type RemoteEpisode, type RemoteProject } from '../../api/modules/projects';
import { ProjectDetailHeader } from '../../features/projects/ProjectDetailHeader';
import type { ProjectSession } from '../../types/projects';
import { projectPath } from '../../app/paths';
import { preloadable } from '../../components/ui/preloadable';
import { CanvasLaunch } from '../../features/projects/CanvasLaunch';
import '../../features/projects/projects.css';

const loadProjectOverview = () => import('../../features/projects/ProjectOverview');
const loadEpisodePage = () => import('./EpisodePage');
const ProjectOverview = preloadable(() => loadProjectOverview().then(module => ({ default: module.ProjectOverview })));
const EpisodePage = preloadable(() => loadEpisodePage().then(module => ({ default: module.EpisodePage })));

export async function preloadProjectView(episodeId?: string, stage?: string, section?: string): Promise<void> {
  if (episodeId) {
    await Promise.all([EpisodePage.preload(), loadEpisodePage().then(module => module.preloadEpisodeStage(stage))]);
  } else {
    await Promise.all([ProjectOverview.preload(), loadProjectOverview().then(module => module.preloadProjectSection(section))]);
  }
}

export function ProjectRoute() {
  const { projectId = '', episodeId, canvasId } = useParams();
  return <RemoteProjectRoute key={`${projectId}:${episodeId ?? canvasId ?? ''}`} projectId={projectId} episodeId={episodeId} canvasId={canvasId} />;
}
function RemoteProjectRoute({ projectId, episodeId, canvasId }: { projectId: string; episodeId?: string; canvasId?: string }) {
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
  if (loading) return <section className="projects-home"><ProjectDetailHeader session={null} canClose onBack={() => navigate('/projects')} onClose={() => navigate('/projects')} /><div className="studio-empty" role="status"><Spin /> 正在打开项目…</div></section>;
  if (error || !project) return <section className="projects-home"><ProjectDetailHeader session={null} canClose fallbackTitle="项目无法打开" onBack={() => navigate('/projects')} onClose={() => navigate('/projects')} /><Alert type="error" showIcon message={error || '项目不存在。'} action={<Button onClick={() => setRevision((v) => v + 1)}>重试</Button>} /></section>;
  const session: ProjectSession = { projectId, projectSessionId: projectId, mode: 'write', project };
  if (project.workspaceMode === 'infinite_canvas') return <CanvasLaunch projectId={projectId} canvasId={canvasId ?? project.primaryCanvasId} />;
  if (canvasId) return <Alert type="error" showIcon message="此项目使用标准模式。" />;
  if (episodeId && episode) return <Suspense fallback={<div className="studio-empty" role="status"><Spin /> 正在载入分集工作区…</div>}><EpisodePage session={session} episode={episode} number={episode.number ?? 1} ready onBack={() => navigate(projectPath(projectId))} /></Suspense>;
  return <section className="projects-home">
    <ProjectDetailHeader session={session} canClose onBack={() => navigate('/projects')} onClose={() => navigate('/projects')} />
    <Suspense fallback={<div className="studio-empty" role="status"><Spin /> 正在载入项目详情…</div>}><ProjectOverview session={session} project={project} onProjectUpdated={setProject} onDeleted={() => navigate('/projects', { replace: true })} /></Suspense>
  </section>;
}
