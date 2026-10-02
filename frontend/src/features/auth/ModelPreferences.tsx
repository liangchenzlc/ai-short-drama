import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { errorMessage, http } from '../../api/http';
import { useAuth } from './AuthSession';

interface Preferences {
  enabled: boolean; ready: boolean; error: string; values: Record<string, string>;
  save: (key: string, value?: string) => Promise<void>;
}
const Context = createContext<Preferences>({ enabled: false, ready: true, error: '', values: {}, save: async () => {} });
export function useModelPreferences() { return useContext(Context); }
export function ModelPreferencesProvider({ children }: { children: ReactNode }) {
  const auth = useAuth();
  const enabled = auth.enabled && !!auth.user;
  const [values, setValues] = useState<Record<string, string>>({});
  const [ready, setReady] = useState(!enabled);
  const [error, setError] = useState('');
  const writes = useRef(new Map<string, Promise<void>>());
  const requests = useRef<AbortController | null>(null);
  useEffect(() => {
    if (!enabled) { setReady(true); setValues({}); return; }
    const controller = new AbortController(); requests.current = controller; writes.current.clear(); setReady(false); setError(''); setValues({});
    void http.get<{ items: { context_key: string; config_id: string }[] }>('/users/me/model-preferences', { signal: controller.signal })
      .then(r => { if (!controller.signal.aborted) setValues(Object.fromEntries(r.data.items.map(v => [v.context_key, v.config_id]))); })
      .catch(cause => { if (!controller.signal.aborted) setError(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setReady(true); });
    return () => { controller.abort(); requests.current = null; };
  }, [enabled, auth.user?.id]);
  async function save(key: string, value?: string) {
    const controller = requests.current;
    if (!enabled || !controller || controller.signal.aborted) return;
    setValues(v => { const next = { ...v }; if (value) next[key] = value; else delete next[key]; return next; });
    // Preserve choice order when users change a selector faster than the network.
    const previous = writes.current.get(key) ?? Promise.resolve();
    const write = previous.catch(() => {}).then(async () => {
      if (controller.signal.aborted) return;
      await http.put('/users/me/model-preferences', { context_key: key, config_id: value ?? null }, { signal: controller.signal });
    });
    writes.current.set(key, write);
    try { await write; if (!controller.signal.aborted) setError(''); } catch (cause) { if (!controller.signal.aborted) setError(errorMessage(cause)); }
  }
  return <Context.Provider value={{ enabled, ready, error, values, save }}>{children}</Context.Provider>;
}
