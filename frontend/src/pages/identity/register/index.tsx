import { errorMessage, http } from '@host/api/http';
import { AccountFrame } from '@host/app/layouts/account/index';
import { safeReturn } from '@host/features/identity/session/AuthSession';
import { Alert, Button, Input } from 'antd';
import { useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import './index.css';
export function RegisterPage() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [fields, setFields] = useState({
    username: '',
    display_name: '',
    email: '',
    password: '',
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const patch = (key: keyof typeof fields, value: string) => setFields((f) => ({ ...f, [key]: value }));
  return (
    <AccountFrame title="创建你的创作账号">
      <p>邮箱用于验证身份和找回密码。</p>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          if (busy) return;
          setBusy(true);
          setError('');
          try {
            const result = (await http.post<{ challenge_id: string }>('/auth/register', fields)).data;
            setFields((f) => ({ ...f, password: '' }));
            navigate(
              `/verify-email?challenge=${result.challenge_id}&email=${encodeURIComponent(fields.email)}&next=${encodeURIComponent(safeReturn(params.get('next')))}`,
            );
          } catch (cause) {
            setError(errorMessage(cause));
          } finally {
            setBusy(false);
          }
        }}
      >
        <label htmlFor="register-name">
          账号名
          <Input
            id="register-name"
            autoComplete="username"
            required
            pattern="[A-Za-z0-9_-]{3,32}"
            maxLength={32}
            value={fields.username}
            onChange={(e) => patch('username', e.target.value)}
          />
          <small>3–32 位字母、数字、下划线或短横线</small>
        </label>
        <label htmlFor="register-display">
          显示人名
          <Input
            id="register-display"
            autoComplete="name"
            required
            maxLength={80}
            value={fields.display_name}
            onChange={(e) => patch('display_name', e.target.value)}
          />
        </label>
        <label htmlFor="register-email">
          邮箱
          <Input
            id="register-email"
            type="email"
            autoComplete="email"
            required
            maxLength={254}
            value={fields.email}
            onChange={(e) => patch('email', e.target.value)}
          />
        </label>
        <label htmlFor="register-password">
          密码
          <Input.Password
            id="register-password"
            autoComplete="new-password"
            required
            minLength={12}
            maxLength={128}
            value={fields.password}
            onChange={(e) => patch('password', e.target.value)}
          />
          <small>至少 12 位，请使用独立密码</small>
        </label>
        {error && <Alert type="error" message={error} />}
        <Button type="primary" block htmlType="submit" loading={busy}>
          注册并发送验证码
        </Button>
      </form>
      <Link to={`/login?next=${encodeURIComponent(safeReturn(params.get('next')))}`}>已有账号，返回登录</Link>
    </AccountFrame>
  );
}
