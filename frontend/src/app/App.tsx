import { AppRoutes } from '@host/app/router/routes';
import { useWorkspaceLifecycle } from '@host/app/router/useWorkspaceLifecycle';
export function App() {
  useWorkspaceLifecycle();
  return <AppRoutes />;
}
