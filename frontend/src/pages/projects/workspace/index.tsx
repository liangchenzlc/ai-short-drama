import { projectError, projectsApi } from '@host/api/projects/management';
import { CanvasLaunch } from '@host/features/canvas-integration/launch/CanvasLaunch';
import { preloadable } from '@host/shared/async/preloadable';
import { creationStages } from '@host/shared/navigation/paths';
import type { RemoteProject } from '@host/types/projects/management';
import { Button, Spin } from 'antd';
import { Suspense, useEffect, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import '../../../features/projects/list/index.css';
const loadCreation = () => import('../../creation/index');
const CreationPage = preloadable(() => loadCreation().then((module) => ({ default: module.CreationPage })));
export async function preloadProjectView(stage?: string): Promise<void> {
  await Promise.all([CreationPage.preload(), loadCreation().then((module) => module.preloadCreationStage(stage))]);
}
export function RetiredFeaturePage() {
  return (
    <section className="projects-home studio-empty">
      <h1>此功能已停用</h1>
      <p>原分集、共享素材库与协作功能已移除，请从项目列表开始创作。</p>
      <Link to="/projects">返回项目列表</Link>
    </section>
  );
}
export function ProjectRoute() {
  const { projectId = '', canvasId, stage } = useParams();
  const [search] = useSearchParams();
  if (search.has('section') || (stage && !creationStages.includes(stage as (typeof creationStages)[number])))
    return <RetiredFeaturePage />;
  return <RemoteProjectRoute key={projectId} projectId={projectId} canvasId={canvasId} />;
}
function RemoteProjectRoute({ projectId, canvasId }: { projectId: string; canvasId?: string }) {
  const [project, setProject] = useState<RemoteProject | null>(null);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setError('');
    void projectsApi
      .get(projectId, controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) setProject(result);
      })
      .catch((cause) => {
        if (!controller.signal.aborted) setError(projectError(cause));
      });
    void projectsApi.open(projectId, controller.signal).catch(() => {});
    return () => controller.abort();
  }, [projectId, revision]);
  if (error)
    return (
      <section className="projects-home studio-empty">
        <p role="alert">{error}</p>
        <Button onClick={() => setRevision((value) => value + 1)}>重试</Button>
        <Link to="/projects">返回项目列表</Link>
      </section>
    );
  if (!project)
    return (
      <div className="studio-empty" role="status">
        <Spin /> 正在打开项目…
      </div>
    );
  if (project.workspaceMode === 'infinite_canvas')
    return <CanvasLaunch projectId={projectId} canvasId={canvasId ?? project.primaryCanvasId} />;
  if (canvasId) return <RetiredFeaturePage />;
  return (
    <Suspense
      fallback={
        <div className="studio-empty" role="status">
          <Spin /> 正在载入创作工作区…
        </div>
      }
    >
      <CreationPage session={{ projectId, project }} project={project} onProjectUpdated={setProject} />
    </Suspense>
  );
}
