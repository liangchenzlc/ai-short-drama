import { useLayoutEffect } from 'react';
import { Navigate, Outlet, Route, Routes, useLocation, useMatch, useParams } from 'react-router-dom';
import { lazy, Suspense, useEffect } from 'react';
import { Skeleton } from 'antd';
import { RouteBoundary } from '../components/ui/RouteBoundary';
import { Sidebar, type MainPage } from '../components/layout/Sidebar';
import { AiConfigSessionProvider } from '../features/ai-config/AiConfigSession';
import { ProjectsPage } from '../pages/projects/ProjectsPage';
import { NotFoundPage } from './NotFoundPage';
import './generations.css';
import { AuthGate, useAuth } from '../features/auth/AuthSession';
import { AccountControls } from '../features/auth/AccountControls';
import { LoginPage, RegisterPage, EmailProofPage, InvitationPage } from '../features/auth/AccountPages';
import { ModelPreferencesProvider } from '../features/auth/ModelPreferences';
import { ConfigCatalogProvider } from '../features/ai-config/ConfigCatalogProvider';
import { preloadable } from '../components/ui/preloadable';
import { accountPath, aiConfigPath } from './paths';

const AssetsPage = lazy(() => import('../pages/assets/AssetsPage').then(module => ({ default: module.AssetsPage })));
const AiConfigPage = lazy(() => import('../pages/ai-config/AiConfigPage').then(module => ({ default: module.AiConfigPage })));
const AccountCenterPage = lazy(() => import('../features/auth/AccountCenter').then(module => ({ default: module.AccountCenterPage })));
const loadProjectRoute = () => import('../pages/projects/ProjectRoute');
const ProjectRoute = preloadable(() => loadProjectRoute().then(module => ({ default: module.ProjectRoute })));
const TasksPage = lazy(() => import('../pages/tasks/TasksPage').then(module => ({ default: module.TasksPage })));
const MediaLibraryPage = lazy(() => import('../pages/media-library/MediaLibraryPage').then(module => ({ default: module.MediaLibraryPage })));

function AssetRoute() {
  const { kind } = useParams();
  if (kind !== 'character' && kind !== 'scene' && kind !== 'prop') return <NotFoundPage message="素材分类不存在。" />;
  return <AssetsPage key={kind} kind={kind} />;
}
function TaskRoute() {
  const { kind } = useParams();
  if (kind !== 'text' && kind !== 'image' && kind !== 'video' && kind !== 'audio') return <NotFoundPage message="任务类型不存在。" />;
  return <TasksPage key={kind} kind={kind} />;
}
function MediaLibraryRoute() {
  const { kind } = useParams();
  if (kind !== 'image' && kind !== 'video') return <NotFoundPage message="资产类型不存在。" />;
  return <MediaLibraryPage key={kind} kind={kind} />;
}
function StudioLayout() {
  const { pathname } = useLocation();
  const auth = useAuth();
  const episode = useMatch('/projects/:projectId/episodes/:episodeId/:stage?');
  const project = useMatch('/projects/:projectId');
  const asset = useMatch('/assets/:kind');
  const canvas = useMatch('/projects/:projectId/canvases/:canvasId');
  const page: MainPage = asset ? 'assets' : pathname.startsWith('/tasks') ? 'tasks' : pathname.startsWith('/media-library') ? 'media-library' : pathname === aiConfigPath ? 'ai' : pathname === accountPath ? 'account' : 'projects';
  const pageLabels: Record<MainPage, string> = { projects: '项目管理', assets: '素材库', tasks: '任务管理', 'media-library': '资产库', ai: 'AI 配置', account: '账号中心' };
  const detail = !!(episode || project || canvas);
  useLayoutEffect(() => { window.scrollTo({ top: 0, behavior: 'instant' }); }, [pathname]);
  return <div className={detail ? `studio studio-detail studio-${episode ? 'episode' : 'detail'}` : 'studio'}>
    <a className="skip-link" href="#main">跳到主内容</a>
    {!detail && <Sidebar page={page} />}
    <div className="studio-content">
      {!detail && <header className="studio-topbar"><span>创作空间 <span className="topbar-divider">/</span> {pageLabels[page]}</span>{auth.enabled && auth.user ? <AccountControls /> : <span className="workspace-label">个人创作空间</span>}</header>}
      <main id="main" tabIndex={-1} className="studio-main"><RouteBoundary resetKey={pathname}><Suspense fallback={<div className="route-loading" role="status" aria-label="正在加载工作区"><Skeleton active title paragraph={{ rows: 4 }}/></div>}><Outlet /></Suspense></RouteBoundary></main>
    </div>
  </div>;
}
function ProjectLayout() { return <div className="studio-projects"><Outlet /></div>; }
export function App() {
  const auth = useAuth();
  const location = useLocation();
  const episode = useMatch('/projects/:projectId/episodes/:episodeId/:stage?');
  const project = useMatch('/projects/:projectId');
  const episodeId = episode?.params.episodeId;
  const stage = episode?.params.stage;
  const projectId = project?.params.projectId;
  const section = new URLSearchParams(location.search).get('section') ?? undefined;
  useEffect(() => {
    if (!episodeId && !projectId) return;
    // Load the requested view's code during authentication; AuthGate still owns data access.
    void Promise.all([ProjectRoute.preload(), loadProjectRoute().then(module => module.preloadProjectView(episodeId, stage, section))]).catch(() => {
      // RouteBoundary presents a failed optional preload when the route is rendered.
    });
  }, [episodeId, stage, projectId, section]);
  return <AiConfigSessionProvider key={auth.user?.id ?? 'anonymous'}><ModelPreferencesProvider><ConfigCatalogProvider><Routes>
    <Route path="login" element={<LoginPage />} />
    <Route path="register" element={<RegisterPage />} />
    <Route path="verify-email" element={<EmailProofPage />} />
    <Route path="reset-password" element={<EmailProofPage reset />} />
    <Route path="invite/:token" element={<InvitationPage />} />
    <Route element={<AuthGate />}>
    <Route element={<StudioLayout />}>
      <Route index element={<Navigate to="/projects" replace />} />
      <Route path="projects" element={<ProjectLayout />}>
        <Route index element={<ProjectsPage />} />
        <Route path=":projectId" element={<ProjectRoute />} />
        <Route path=":projectId/canvases/:canvasId" element={<ProjectRoute />} />
        <Route path=":projectId/episodes/:episodeId/:stage?" element={<ProjectRoute />} />
      </Route>
      <Route path="assets" element={<Navigate to="/assets/character" replace />} />
      <Route path="assets/:kind" element={<AssetRoute />} />
      <Route path="ai" element={<Navigate to={aiConfigPath} replace />} />
      <Route path="ai_config" element={<AiConfigPage />} />
      <Route path="account" element={<AccountCenterPage />} />
      <Route path="tasks" element={<Navigate to="/tasks/text" replace />} />
      <Route path="tasks/:kind" element={<TaskRoute />} />
      <Route path="media-library" element={<Navigate to="/media-library/image" replace />} />
      <Route path="media-library/:kind" element={<MediaLibraryRoute />} />
      </Route>
    <Route path="*" element={<NotFoundPage />} />
    </Route>
  </Routes></ConfigCatalogProvider></ModelPreferencesProvider></AiConfigSessionProvider>;
}

