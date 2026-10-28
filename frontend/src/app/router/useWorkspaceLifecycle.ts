import { hasStandardUnpersistedChanges, retryStandardSessions } from '@host/features/creation/shared/sessions/registry';
import { useEffect } from 'react';
import { useMatch } from 'react-router-dom';
import '../../features/generations/tasks/generations.css';
/** 只对无法持久保留的输入拦截关闭；页面切换不会销毁创作会话。 */
export function useWorkspaceLifecycle() {
  const creation = useMatch('/projects/:projectId/:stage');
  const project = useMatch('/projects/:projectId');
  const stage = creation?.params.stage;
  const projectId = project?.params.projectId;
  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (hasStandardUnpersistedChanges()) {
        event.preventDefault();
        event.returnValue = '';
      }
    };
    const online = () => {
      void retryStandardSessions();
    };
    window.addEventListener('beforeunload', beforeUnload);
    window.addEventListener('online', online);
    return () => {
      window.removeEventListener('beforeunload', beforeUnload);
      window.removeEventListener('online', online);
    };
  }, []);
  useEffect(() => {
    if (!creation && !projectId) return;
    // Load the requested view's code during authentication; AuthGate still owns data access.
    void Promise.all([
      import('../../pages/projects/workspace').then((module) => module.preloadProjectView(stage)),
    ]).catch(() => {
      // RouteBoundary presents a failed optional preload when the route is rendered.
    });
  }, [stage, projectId]);
}
