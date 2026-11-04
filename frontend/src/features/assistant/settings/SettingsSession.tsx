import { useAssistantSettings } from '@host/features/assistant/settings/useAssistantSettings';
import { MemoryManager } from '@host/features/assistant/settings/MemoryManager';
import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';

const Context = createContext<(ReturnType<typeof useAssistantSettings> & { openMemory: () => void }) | null>(null);
export function SettingsSessionProvider({ children, active }: { children: ReactNode; active: boolean }) {
  const settings = useAssistantSettings(active);
  const [memoryOpen, setMemoryOpen] = useState(false);
  useEffect(() => { if (!active) setMemoryOpen(false); }, [active]);
  // 抽屉关闭或切换主导航时仍保留记忆同步队列，避免 600ms 内离页丢掉输入。
  return <Context.Provider value={{ ...settings, openMemory: () => setMemoryOpen(true) }}>
    {children}
    <MemoryManager open={memoryOpen} onClose={() => setMemoryOpen(false)} />
  </Context.Provider>;
}
export function useSettingsSession() {
  const settings = useContext(Context);
  if (!settings) throw new Error('配置页需要设置会话');
  return settings;
}
