import { Sidebar } from '@host/app/layouts/studio/components/Sidebar';
import { AccountControls } from '@host/features/identity/account/AccountControls';
import { useAuth } from '@host/features/identity/session/AuthSession';
import { accountPath, aiConfigPath, skillsPath } from '@host/shared/navigation/paths';
import { RouteBoundary } from '@host/shared/ui/RouteBoundary';
import type { MainPage } from '@host/types/app/layouts/studio/components/Sidebar';
import { Skeleton } from 'antd';
import { Suspense, useLayoutEffect } from 'react';
import { Outlet, useLocation, useMatch } from 'react-router-dom';
import '../../../features/generations/tasks/generations.css';
import './index.css';
export function StudioLayout() {
  const { pathname } = useLocation();
  const auth = useAuth();
  const creation = useMatch('/projects/:projectId/:stage');
  const project = useMatch('/projects/:projectId');
  const canvas = useMatch('/projects/:projectId/canvases/:canvasId');
  const page: MainPage = pathname.startsWith('/media-library')
    ? 'media-library'
    : pathname === skillsPath
      ? 'skills'
      : pathname === aiConfigPath
        ? 'ai'
        : pathname === accountPath
          ? 'account'
          : 'projects';
  const pageLabels: Record<MainPage, string> = {
    projects: '项目管理',
    'media-library': '资产库',
    skills: 'Skill 管理',
    ai: 'AI 配置',
    account: '账号中心',
  };
  const detail = !!(creation || project || canvas);
  useLayoutEffect(() => {
    window.scrollTo({ top: 0, behavior: 'instant' });
  }, [pathname]);
  return (
    <div className={detail ? `studio studio-detail studio-${creation ? 'creation' : 'detail'}` : 'studio'}>
      <a className="skip-link" href="#main">
        跳到主内容
      </a>
      {!detail && <Sidebar page={page} />}
      <div className="studio-content">
        {!detail && (
          <header className="studio-topbar">
            <span>
              创作空间 <span className="topbar-divider">/</span> {pageLabels[page]}
            </span>
            {auth.enabled && auth.user ? <AccountControls /> : <span className="workspace-label">个人创作空间</span>}
          </header>
        )}
        <main id="main" tabIndex={-1} className="studio-main">
          <RouteBoundary resetKey={pathname}>
            <Suspense
              fallback={
                <div className="route-loading" role="status" aria-label="正在加载工作区">
                  <Skeleton active title paragraph={{ rows: 4 }} />
                </div>
              }
            >
              <Outlet />
            </Suspense>
          </RouteBoundary>
        </main>
      </div>
    </div>
  );
}
