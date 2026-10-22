import { useEffect, useState, type FormEvent } from 'react';
import { Alert, Button, Input, Spin } from 'antd';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { errorMessage, http } from '../../api/http';
import { safeReturn, useAuth } from './AuthSession';
import { Icon } from '../../components/ui/Icon';
import { EmailProofForm } from './EmailProofForm';

function AccountFrame({ title, children }: { title: string; children: React.ReactNode }) { return <main className="identity-page"><section className="identity-form" aria-labelledby="account-title"><Link className="identity-brand" to="/projects"><Icon name="film" size={22} /><span>短剧工作台</span></Link><h1 id="account-title">{title}</h1>{children}</section></main>; }
export function LoginPage() {
  const auth = useAuth(); const navigate = useNavigate(); const [params] = useSearchParams();
  const [username, setUsername] = useState(''); const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  async function submit(event: FormEvent) { event.preventDefault(); if (busy) return; setBusy(true); setError(''); try { await auth.login(username, password); setPassword(''); const next = safeReturn(params.get('next')); if (next.startsWith('/canvas-app/')) window.location.replace(next); else navigate(next, { replace: true }); } catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); } }
  return <AccountFrame title="登录，继续你的故事"><form onSubmit={submit}>
    <label htmlFor="login-name">账号名<Input id="login-name" autoComplete="username" autoFocus required maxLength={32} value={username} onChange={e => setUsername(e.target.value)} /></label>
    <label htmlFor="login-password">密码<Input.Password id="login-password" autoComplete="current-password" required maxLength={128} value={password} onChange={e => setPassword(e.target.value)} /></label>
    {error && <Alert type="error" message={error} />}<Button type="primary" block htmlType="submit" loading={busy}>登录</Button>
  </form><nav className="identity-links"><Link to={`/register?next=${encodeURIComponent(safeReturn(params.get('next')))}`}>创建账号</Link><Link to="/verify-email">验证注册邮箱</Link><Link to="/reset-password">忘记密码</Link></nav></AccountFrame>;
}

export function RegisterPage() {
  const navigate = useNavigate(); const [params] = useSearchParams();
  const [fields, setFields] = useState({ username: '', display_name: '', email: '', password: '' });
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const patch = (key: keyof typeof fields, value: string) => setFields(f => ({ ...f, [key]: value }));
  return <AccountFrame title="创建你的创作账号"><p>邮箱用于验证身份和找回密码。</p><form onSubmit={async e => { e.preventDefault(); if (busy) return; setBusy(true); setError(''); try { const result = (await http.post<{ challenge_id: string }>('/auth/register', fields)).data; setFields(f => ({ ...f, password: '' })); navigate(`/verify-email?challenge=${result.challenge_id}&email=${encodeURIComponent(fields.email)}&next=${encodeURIComponent(safeReturn(params.get('next')))}`); } catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); } }}>
    <label htmlFor="register-name">账号名<Input id="register-name" autoComplete="username" required pattern="[A-Za-z0-9_-]{3,32}" maxLength={32} value={fields.username} onChange={e => patch('username', e.target.value)} /><small>3–32 位字母、数字、下划线或短横线</small></label>
    <label htmlFor="register-display">显示人名<Input id="register-display" autoComplete="name" required maxLength={80} value={fields.display_name} onChange={e => patch('display_name', e.target.value)} /></label>
    <label htmlFor="register-email">邮箱<Input id="register-email" type="email" autoComplete="email" required maxLength={254} value={fields.email} onChange={e => patch('email', e.target.value)} /></label>
    <label htmlFor="register-password">密码<Input.Password id="register-password" autoComplete="new-password" required minLength={12} maxLength={128} value={fields.password} onChange={e => patch('password', e.target.value)} /><small>至少 12 位，请使用独立密码</small></label>
    {error && <Alert type="error" message={error} />}<Button type="primary" block htmlType="submit" loading={busy}>注册并发送验证码</Button>
  </form><Link to={`/login?next=${encodeURIComponent(safeReturn(params.get('next')))}`}>已有账号，返回登录</Link></AccountFrame>;
}

export function EmailProofPage({ reset = false }: { reset?: boolean }) {
  const [params] = useSearchParams(); const [done, setDone] = useState(false);
  return <AccountFrame title={reset ? '找回账号密码' : '验证注册邮箱'}>{done ? <><p role="status">{reset ? '密码已更新，请重新登录。' : '邮箱已验证，现在可以登录。'}</p><Link to={`/login?next=${encodeURIComponent(safeReturn(params.get('next')))}`}>返回登录</Link></> : <>
    <EmailProofForm key={`${reset}:${params.get('challenge') ?? ''}`} reset={reset} initialEmail={params.get('email') ?? ''} initialChallenge={params.get('challenge') ?? ''} onSuccess={() => setDone(true)} />
    <Link to="/login">返回登录</Link></>}</AccountFrame>;
}

