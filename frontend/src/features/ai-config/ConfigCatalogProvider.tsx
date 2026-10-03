import { createContext, useCallback, useContext, useEffect, useMemo, useSyncExternalStore, type ReactNode } from 'react';
import { aiModelConfigs } from '../../api/modules/ai-model-configs';
import { generationError } from '../generations/presentation';
import { useAuth } from '../auth/AuthSession';
import type { ServiceType } from './config-model';
import { AI_CONFIGS_CHANGED } from './config-events';
import { ConfigCatalog } from './config-catalog';

const Context = createContext<ConfigCatalog | null>(null);

export function ConfigCatalogProvider({ children }: { children: ReactNode }) {
  const auth = useAuth();
  const catalog = useMemo(() => new ConfigCatalog(aiModelConfigs.list, generationError), [auth.enabled, auth.user?.id]);
  useEffect(() => {
    const refresh = () => catalog.invalidate();
    window.addEventListener(AI_CONFIGS_CHANGED, refresh);
    window.addEventListener('focus', refresh);
    return () => {
      window.removeEventListener(AI_CONFIGS_CHANGED, refresh);
      window.removeEventListener('focus', refresh);
    };
  }, [catalog]);
  return <Context.Provider value={catalog}>{children}</Context.Provider>;
}

export function useConfigCatalog(kind: ServiceType) {
  const catalog = useContext(Context);
  if (!catalog) throw new Error('模型选择需要配置目录 Provider');
  const subscribe = useCallback((listener: () => void) => catalog.subscribe(kind, listener), [catalog, kind]);
  const snapshot = useCallback(() => catalog.getSnapshot(kind), [catalog, kind]);
  const result = useSyncExternalStore(subscribe, snapshot);
  return { ...result, refresh: () => catalog.refresh(kind) };
}
