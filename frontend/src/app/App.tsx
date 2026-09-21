import { useLayoutEffect } from 'react';
import { Navigate, Outlet, Route, Routes, useLocation, useMatch, useNavigate, useParams } from 'react-router-dom';
import { Sidebar, type MainPage } from '../components/layout/Sidebar';
import type { AssetKind } from '../features/assets/asset-model';
import { AssetsPage } from '../pages/assets/AssetsPage';
import { AiConfigPage } from '../pages/ai-config/AiConfigPage';
import { AiConfigSessionProvider } from '../features/ai-config/AiConfigSession';
import { ProjectsPage } from '../pages/projects/ProjectsPage';
import { ProjectRoute } from '../pages/projects/ProjectRoute';
import { NotFoundPage } from './NotFoundPage';
import { TasksPage } from '../pages/tasks/TasksPage';
import { MediaLibraryPage } from '../pages/media-library/MediaLibraryPage';
import './generations.css';

function AssetRoute() {
  const { kind } = useParams();
  if (kind !== 'character' && kind !== 'scene' && kind !== 'prop') return <NotFoundPage message="素材分类不存在。" />;
  return <AssetsPage key={kind} kind={kind} />;
}
function TaskRoute() {
  const { kind } = useParams();
  if (kind !== 'text' && kind !== 'image' && kind !== 'video') return <NotFoundPage message="任务类型不存在。" />;
  return <TasksPage key={kind} kind={kind} />;
}
function MediaLibraryRoute() {
  const { kind } = useParams();
  if (kind !== 'image' && kind !== 'video') return <NotFoundPage message="资产类型不存在。" />;
  return <MediaLibraryPage key={kind} kind={kind} />;
}
function StudioLayout() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const episode = useMatch('/projects/:projectId/episodes/:episodeId/:stage?');
  const project = useMatch('/projects/:projectId');
  const asset = useMatch('/assets/:kind');
  const page: MainPage = asset ? 'assets' : pathname.startsWith('/tasks') ? 'tasks' : pathname.startsWith('/media-library') ? 'media-library' : pathname === '/ai' ? 'ai' : 'projects';
  const pageLabels: Record<MainPage, string> = { projects: '项目管理', assets: '素材库', tasks: '任务管理', 'media-library': '资产库', ai: 'AI 配置' };
  const kind: AssetKind = asset?.params.kind === 'scene' ? 'scene' : asset?.params.kind === 'prop' ? 'prop' : 'character';
  const detail = !!(episode || project);
  useLayoutEffect(() => { window.scrollTo({ top: 0, behavior: 'instant' }); }, [pathname]);
  return <div className={detail ? `studio studio-detail studio-${episode ? 'episode' : 'detail'}` : 'studio'}>
    <a className="skip-link" href="#main">跳到主内容</a>
    {!detail && <Sidebar page={page} kind={kind} onSelect={(next, nextKind) => navigate(next === 'projects' ? '/projects' : next === 'ai' ? '/ai' : next === 'tasks' ? '/tasks/text' : next === 'media-library' ? '/media-library/image' : `/assets/${nextKind ?? kind}`)} />}
    <div className="studio-content">
      {!detail && <header className="studio-topbar"><span>创作空间 <span className="topbar-divider">/</span> {pageLabels[page]}</span><span className="connection ready"><i aria-hidden="true" />服务端工作区<span className="workspace-avatar" aria-label="个人工作区">创</span></span></header>}
      <main id="main" tabIndex={-1} className="studio-main"><Outlet /></main>
    </div>
  </div>;
}
function ProjectLayout() { return <div className="studio-projects"><Outlet /></div>; }
export function App() {
  return <AiConfigSessionProvider><Routes>
    <Route element={<StudioLayout />}>
      <Route index element={<Navigate to="/projects" replace />} />
      <Route path="projects" element={<ProjectLayout />}>
        <Route index element={<ProjectsPage />} />
        <Route path=":projectId" element={<ProjectRoute />} />
        <Route path=":projectId/episodes/:episodeId/:stage?" element={<ProjectRoute />} />
      </Route>
      <Route path="assets" element={<Navigate to="/assets/character" replace />} />
      <Route path="assets/:kind" element={<AssetRoute />} />
      <Route path="ai" element={<AiConfigPage />} />
      <Route path="tasks" element={<Navigate to="/tasks/text" replace />} />
      <Route path="tasks/:kind" element={<TaskRoute />} />
      <Route path="media-library" element={<Navigate to="/media-library/image" replace />} />
      <Route path="media-library/:kind" element={<MediaLibraryRoute />} />
      <Route path="*" element={<NotFoundPage />} />
    </Route>
  </Routes></AiConfigSessionProvider>;
}

