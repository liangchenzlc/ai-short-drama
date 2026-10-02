import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react';
import { Alert, Button, Input, Spin } from 'antd';
import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { ApiError, errorMessage, http } from '../../api/http';
import { Dialog } from '../../components/ui/Dialog';
import { setAttemptAccount } from '../generations/attempt';

export interface Account { id: string; username: string; display_name: string; email: string; email_verified: boolean }
interface Session { enabled: boolean; user: Account | null; loading: boolean; error: string; expired: boolean; refresh: () => Promise<void>; login: (username: string, password: string) => Promise<void>; logout: () => Promise<void> }
const Context = createContext<Session>({ enabled: false, user: null, loading: false, error: '', expired: false, refresh: async () => {}, login: async () => {}, logout: async () => {} });
export function useAuth() { return useContext(Context); }
export function AuthProvider({ children }: { children: ReactNode }) {
  const [enabled, setEnabled] = useState(true);
  const [user, setUser] = useState<Account | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [expired, setExpired] = useState(false);
  const refresh = useCallback(async () => {
    setLoading(true); setError('');
    try {
      const capability = (await http.get<{ enabled: boolean }>('/auth/capabilities')).data;
      setEnabled(capability.enabled);
      if (capability.enabled) {
        try { const current = (await http.get<{ user: Account }>('/auth/me')).data.user; setAttemptAccount(current.id); setUser(current); setExpired(false); }
        catch (cause) { if (cause instanceof ApiError && cause.status === 401) { setAttemptAccount(null); setUser(null); setExpired(false); } else throw cause; }
      } else { setAttemptAccount(null); setUser(null); setExpired(false); }
    } catch (cause) { setError(errorMessage(cause)); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    const handler = () => { if (user) setExpired(true); };
    window.addEventListener('session-expired', handler);
    return () => window.removeEventListener('session-expired', handler);
  }, [user]);
  async function login(username: string, password: string) {
    const result = (await http.post<{ user: Account }>('/auth/login', { username, password })).data;
    setAttemptAccount(result.user.id);
    setUser(result.user); setExpired(false);
  }
  async function logout() {
    await http.post('/auth/logout');
    setAttemptAccount(null); setUser(null); setExpired(false);
  }
  return <Context.Provider value={{ enabled, user, loading, error, expired, refresh, login, logout }}>{children}</Context.Provider>;
}

export function AuthGate() {
  const session = useAuth();
  const location = useLocation();
  if (session.loading) return <div className="identity-page" role="status"><Spin /> 正在确认登录状态…</div>;
  if (session.error) return <div className="identity-page"><Alert type="error" message={session.error} /><Button onClick={() => void session.refresh()}>重新连接</Button></div>;
  if (session.enabled && !session.user) return <Navigate to={`/login?next=${encodeURIComponent(location.pathname + location.search)}`} replace />;
  return <><Outlet />{session.expired && <ExpiredSession />}</>;
}

function ExpiredSession() {
  const session = useAuth();
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  return <Dialog title="重新登录，继续创作" canClose={false} onClose={() => {}}><form className="identity-form identity-form-embedded" onSubmit={async e => { e.preventDefault(); if (busy) return; setBusy(true); setError(''); try { await session.login(session.user!.username, password); setPassword(''); } catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); } }}>
    <p>当前页面的编辑仍保留。请登录账号 {session.user?.username} 后继续保存。</p>
    <label htmlFor="resume-password">密码<Input.Password id="resume-password" autoFocus autoComplete="current-password" required value={password} onChange={e => setPassword(e.target.value)} /></label>
    {error && <Alert type="error" message={error} />}<Button type="primary" htmlType="submit" loading={busy}>登录并返回编辑</Button>
  </form></Dialog>;
}

export function safeReturn(value: string | null) { return value && value.startsWith('/') && !value.startsWith('//') && !value.includes('\\') && !/^\/(login|register|verify-email|reset-password)(?:[/?]|$)/.test(value) ? value : '/projects'; }
