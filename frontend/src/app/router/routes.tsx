import { StudioLayout } from '@host/app/layouts/studio/index';
import { safeCanvasReturn } from '@host/features/ai-config/editor/config-form-model';
import { AuthGate } from '@host/features/identity/session/AuthSession';
import { LoginPage } from '@host/pages/identity/login/index';
import { RegisterPage } from '@host/pages/identity/register/index';
import { ResetPasswordPage } from '@host/pages/identity/reset-password/index';
import { EmailProofPage } from '@host/pages/identity/verify-email/index';
import { NotFoundPage } from '@host/pages/not-found/index';
import { ProjectsPage } from '@host/pages/projects/list/index';
import { preloadable } from '@host/shared/async/preloadable';
import { aiConfigPath, skillsPath } from '@host/shared/navigation/paths';
import { lazy } from 'react';
import { Navigate, Outlet, Route, Routes, useLocation, useParams } from 'react-router-dom';
import '../../features/generations/tasks/generations.css';
const SkillsPage = lazy(() =>
  import('../../pages/skills/index').then((module) => ({
    default: module.SkillsPage,
  })),
);
const AiConfigPage = lazy(() =>
  import('../../pages/ai-config/index').then((module) => ({
    default: module.AiConfigPage,
  })),
);
const AccountCenterPage = lazy(() =>
  import('../../pages/identity/account').then((module) => ({
    default: module.AccountCenterPage,
  })),
);
const loadProjectRoute = () => import('../../pages/projects/workspace/index');
const ProjectRoute = preloadable(() => loadProjectRoute().then((module) => ({ default: module.ProjectRoute })));
const MediaLibraryPage = lazy(() =>
  import('../../pages/media-library/index').then((module) => ({
    default: module.MediaLibraryPage,
  })),
);
function AiConfigRoute() {
  const { search } = useLocation();
  const params = new URLSearchParams(search);
  if (params.get('section') === 'skills') {
    const returnTo = safeCanvasReturn(params.get('return_to'));
    return <Navigate to={skillsPath + (returnTo ? `?return_to=${encodeURIComponent(returnTo)}` : '')} replace />;
  }
  return <AiConfigPage />;
}
function MediaLibraryRoute() {
  const { kind } = useParams();
  if (kind !== 'all' && kind !== 'image' && kind !== 'video' && kind !== 'audio')
    return <NotFoundPage message="资产类型不存在。" />;
  return <MediaLibraryPage kind={kind} />;
}
function ProjectLayout() {
  return (
    <div className="studio-projects">
      <Outlet />
    </div>
  );
}
export function AppRoutes() {
  return (
    <Routes>
      <Route path="login" element={<LoginPage />} />
      <Route path="register" element={<RegisterPage />} />
      <Route path="verify-email" element={<EmailProofPage />} />
      <Route path="reset-password" element={<ResetPasswordPage />} />

      <Route element={<AuthGate />}>
        <Route element={<StudioLayout />}>
          <Route index element={<Navigate to="/projects" replace />} />
          <Route path="projects" element={<ProjectLayout />}>
            <Route index element={<ProjectsPage />} />
            <Route path=":projectId" element={<ProjectRoute />} />
            <Route path=":projectId/canvases/:canvasId" element={<ProjectRoute />} />
            <Route path=":projectId/:stage" element={<ProjectRoute />} />
          </Route>

          <Route path="ai" element={<Navigate to={aiConfigPath} replace />} />
          <Route path="ai_config" element={<AiConfigRoute />} />
          <Route path="skills" element={<SkillsPage />} />
          <Route path="account" element={<AccountCenterPage />} />

          <Route path="media-library" element={<Navigate to="/media-library/all" replace />} />
          <Route path="media-library/:kind" element={<MediaLibraryRoute />} />
        </Route>
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
