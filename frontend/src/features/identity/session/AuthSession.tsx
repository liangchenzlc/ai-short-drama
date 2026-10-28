import { ApiError, errorMessage, http } from '@host/api/http';
import { setStandardSessionAccount } from '@host/features/creation/shared/sessions/registry';
import { setAttemptAccount } from '@host/features/shared/async/request-receipt';
import { Dialog } from '@host/shared/ui/Dialog';
import type { Account, Session } from '@host/types/identity/session/AuthSession';
import { Alert, Button, Input, Spin } from 'antd';
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react';
import { Navigate, Outlet, useLocation } from 'react-router-dom';
function setEditingAccount(id: string | null) {
  setAttemptAccount(id);
  setStandardSessionAccount(id ?? 'anonymous');
}
const Context = createContext<Session>({
  enabled: false,
  user: null,
  loading: false,
  error: '',
  expired: false,
  refresh: async () => {},
  login: async () => {},
  logout: async () => {},
});
export function useAuth() {
  return useContext(Context);
}
export function AuthProvider({ children }: { children: ReactNode }) {
  const [enabled, setEnabled] = useState(true);
  const [user, setUser] = useState<Account | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [expired, setExpired] = useState(false);
  const refresh = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const capability = (await http.get<{ enabled: boolean }>('/auth/capabilities')).data;
      setEnabled(capability.enabled);
      if (capability.enabled) {
        try {
          const current = (await http.get<{ user: Account }>('/auth/me')).data.user;
          setEditingAccount(current.id);
          setUser(current);
          setExpired(false);
        } catch (cause) {
          if (cause instanceof ApiError && cause.status === 401) {
            setEditingAccount(null);
            setUser(null);
            setExpired(false);
          } else throw cause;
        }
      } else {
        setEditingAccount(null);
        setUser(null);
        setExpired(false);
      }
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    void refresh();
  }, [refresh]);
  useEffect(() => {
    const handler = () => {
      if (user) {
        setStandardSessionAccount(null);
        setExpired(true);
      }
    };
    window.addEventListener('session-expired', handler);
    return () => window.removeEventListener('session-expired', handler);
  }, [user]);
  async function login(username: string, password: string) {
    setStandardSessionAccount(null);
    const result = (await http.post<{ user: Account }>('/auth/login', { username, password })).data;
    setEditingAccount(result.user.id);
    setUser(result.user);
    setExpired(false);
  }
  async function logout() {
    setStandardSessionAccount(null);
    try {
      await http.post('/auth/logout');
    } catch (cause) {
      setStandardSessionAccount(user?.id ?? (enabled ? null : 'anonymous'));
      throw cause;
    }
    setEditingAccount(null);
    setUser(null);
    setExpired(false);
  }
  return (
    <Context.Provider value={{ enabled, user, loading, error, expired, refresh, login, logout }}>
      {children}
    </Context.Provider>
  );
}

export function AuthGate() {
  const session = useAuth();
  const location = useLocation();
  if (session.loading)
    return (
      <div className="identity-page" role="status">
        <Spin /> 正在确认登录状态…
      </div>
    );
  if (session.error)
    return (
      <div className="identity-page">
        <Alert type="error" message={session.error} />
        <Button onClick={() => void session.refresh()}>重新连接</Button>
      </div>
    );
  if (session.enabled && !session.user)
    return <Navigate to={`/login?next=${encodeURIComponent(location.pathname + location.search)}`} replace />;
  return (
    <>
      <Outlet />
      {session.expired && (
        <ExpiredSession
          presentation={
            /^\/projects\/[^/]+(?:\/(source|assets|storyboard|assembly))?\/?$/.test(location.pathname)
              ? 'standard-creation'
              : undefined
          }
        />
      )}
    </>
  );
}

function ExpiredSession({ presentation }: { presentation?: 'standard-creation' }) {
  const session = useAuth();
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  return (
    <Dialog
      title="重新登录，继续创作"
      className={presentation ? 'standard-creation-dialog' : undefined}
      canClose={false}
      onClose={() => {}}
    >
      <form
        className="identity-form identity-form-embedded"
        onSubmit={async (e) => {
          e.preventDefault();
          if (busy) return;
          setBusy(true);
          setError('');
          try {
            await session.login(session.user!.username, password);
            setPassword('');
          } catch (cause) {
            setError(errorMessage(cause));
          } finally {
            setBusy(false);
          }
        }}
      >
        <p>当前页面的编辑仍保留。请登录账号 {session.user?.username} 后继续保存。</p>
        <label htmlFor="resume-password">
          密码
          <Input.Password
            id="resume-password"
            autoFocus
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {error && <Alert type="error" message={error} />}
        <Button type="primary" htmlType="submit" loading={busy}>
          登录并返回编辑
        </Button>
      </form>
    </Dialog>
  );
}

export function safeReturn(value: string | null) {
  return value &&
    value.startsWith('/') &&
    !value.startsWith('//') &&
    !value.includes('\\') &&
    !/^\/(login|register|verify-email|reset-password)(?:[/?]|$)/.test(value)
    ? value
    : '/projects';
}
