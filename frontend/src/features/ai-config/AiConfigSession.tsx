import { createContext, useContext, useState, type ReactNode } from 'react';
import type { ServiceType } from './config-model';

export const configTabs = ['text', 'image', 'video'] as const;
function useSession() {
  const [tab, setTab] = useState<ServiceType>('text');
  return { tab, setTab };
}
const Context = createContext<ReturnType<typeof useSession> | null>(null);
export function AiConfigSessionProvider({ children }: { children: ReactNode }) {
  return <Context.Provider value={useSession()}>{children}</Context.Provider>;
}
export function useAiConfigSession() {
  const value = useContext(Context);
  if (!value) throw new Error('AI 配置需要会话 Provider');
  return value;
}
