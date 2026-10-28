import { errorMessage } from '@host/api/http';
import { AccountFrame } from '@host/app/layouts/account/index';
import { safeReturn, useAuth } from '@host/features/identity/session/AuthSession';
import { Alert, Button, Input } from 'antd';
import { useState, type FormEvent } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import './index.css';
export function LoginPage() {
  const auth = useAuth();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      await auth.login(username, password);
      setPassword('');
      const next = safeReturn(params.get('next'));
      if (next.startsWith('/canvas-app/')) window.location.replace(next);
      else navigate(next, { replace: true });
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }
  return (
    <AccountFrame title="登录，继续你的故事">
      <form onSubmit={submit}>
        <label htmlFor="login-name">
          账号名
          <Input
            id="login-name"
            autoComplete="username"
            autoFocus
            required
            maxLength={32}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
        </label>
        <label htmlFor="login-password">
          密码
          <Input.Password
            id="login-password"
            autoComplete="current-password"
            required
            maxLength={128}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {error && <Alert type="error" message={error} />}
        <Button type="primary" block htmlType="submit" loading={busy}>
          登录
        </Button>
      </form>
      <nav className="identity-links">
        <Link to={`/register?next=${encodeURIComponent(safeReturn(params.get('next')))}`}>创建账号</Link>
        <Link to="/verify-email">验证注册邮箱</Link>
        <Link to="/reset-password">忘记密码</Link>
      </nav>
    </AccountFrame>
  );
}
