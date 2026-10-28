import { ConfigCatalogProvider } from '@host/features/ai-config/catalog/ConfigCatalogProvider';
import { AiConfigSessionProvider } from '@host/features/ai-config/editor/AiConfigSession';
import { ModelPreferencesProvider } from '@host/features/ai-config/preferences/ModelPreferences';
import { AuthProvider, useAuth } from '@host/features/identity/session/AuthSession';
import { StudioProvider } from '@host/shared/ui/StudioProvider';
import type { ReactNode } from 'react';
import { BrowserRouter } from 'react-router-dom';
function AccountProviders({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  // 切换账号必须重建私人配置上下文，防止上一账号的目录和草稿继续显示。
  return (
    <AiConfigSessionProvider key={user?.id ?? 'anonymous'}>
      <ModelPreferencesProvider>
        <ConfigCatalogProvider>{children}</ConfigCatalogProvider>
      </ModelPreferencesProvider>
    </AiConfigSessionProvider>
  );
}
export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <StudioProvider>
      <BrowserRouter>
        <AuthProvider>
          <AccountProviders>{children}</AccountProviders>
        </AuthProvider>
      </BrowserRouter>
    </StudioProvider>
  );
}
