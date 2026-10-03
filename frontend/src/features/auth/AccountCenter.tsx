import { useEffect, useRef, useState } from 'react';
import { Alert, Button } from 'antd';
import { Link, Navigate, useSearchParams } from 'react-router-dom';
import { errorMessage } from '../../api/http';
import { confirmAction } from '../../components/ui/confirm';
import { Icon } from '../../components/ui/Icon';
import { safeReturn, useAuth } from './AuthSession';
import { EmailProofForm } from './EmailProofForm';
import './account-center.css';

export function AccountCenterPage() {
  const auth = useAuth();
  const [params] = useSearchParams();
  const [passwordOpen, setPasswordOpen] = useState(false);
  const [passwordBusy, setPasswordBusy] = useState(false);
  const [logoutBusy, setLogoutBusy] = useState(false);
  const [error, setError] = useState('');
  const title = useRef<HTMLHeadingElement>(null);
  const busy = passwordBusy || logoutBusy;
  const returnPath = safeReturn(params.get('next'));
  const next = /^\/account(?:[/?]|$)/.test(returnPath) ? '/projects' : returnPath;

  useEffect(() => { title.current?.focus(); }, []);
  async function logout() {
    if (busy || !await confirmAction('退出登录前，请确认创作内容已保存。', {
      title: '退出登录', confirmText: '退出登录',
    })) return;
    setLogoutBusy(true); setError('');
    try { await auth.logout(); }
    catch (cause) { setError(errorMessage(cause)); }
    finally { setLogoutBusy(false); }
  }
  function passwordChanged() {
    setPasswordOpen(false);
    window.dispatchEvent(new Event('session-expired'));
  }

  const user = auth.user;
  if (!auth.enabled || !user) return <Navigate to="/projects" replace />;
  return <section className="studio-page account-center-page" aria-labelledby="account-center-title">
    <header className="account-page-heading">
      <h1 id="account-center-title" ref={title} tabIndex={-1}>账号中心</h1>
      <Link className="account-return" to={next} aria-disabled={busy}
        onClick={event => { if (busy) event.preventDefault(); }}>返回创作</Link>
    </header>
    <div className="account-page-profile">
      <span className="account-profile-avatar" aria-hidden="true"><Icon name="person" size={26} /></span>
      <strong>{user.display_name || user.username}</strong>
    </div>
    <div className="account-page-sections">
      <section className="account-section" aria-labelledby="account-profile-title">
        <h2 id="account-profile-title">账号信息</h2>
        <dl className="account-details">
          <div><dt>账号名</dt><dd>{user.username}</dd></div>
          <div><dt>账号 ID</dt><dd>{user.id}</dd></div>
          <div><dt>注册邮箱</dt><dd><span>{user.email || '未设置邮箱'}</span>{user.email
            ? <span className={`account-email-status${user.email_verified ? ' is-verified' : ''}`}>
              {user.email_verified ? '已验证' : '未验证'}</span> : null}</dd></div>
        </dl>
      </section>
      <section className="account-section" aria-labelledby="account-security-title">
        <h2 id="account-security-title">账号安全</h2>
        <div className="account-security-entry">
          <div><strong>登录密码</strong><p>{user.email
            ? '通过注册邮箱验证身份后设置新密码。' : '未设置邮箱，暂无法通过邮箱修改密码。'}</p></div>
          <Button disabled={busy || !user.email} aria-expanded={passwordOpen}
            aria-controls="account-password-form" onClick={() => setPasswordOpen(value => !value)}>
            {passwordOpen ? '取消修改' : '修改密码'}</Button>
        </div>
        {passwordOpen ? <div id="account-password-form" className="account-password-form">
          <p>密码更新后，所有登录会话将失效。请用新密码重新登录。</p>
          <EmailProofForm reset initialEmail={user.email} readOnlyEmail
            onSuccess={passwordChanged} onBusyChange={setPasswordBusy} />
        </div> : null}
      </section>
    </div>
    {error ? <Alert className="account-page-error" type="error" showIcon
      message={`退出登录未完成：${error}`} description="当前账号仍保持登录，可以重试。" /> : null}
    <footer className="account-page-footer">
      <Button danger loading={logoutBusy} disabled={passwordBusy} onClick={() => void logout()}>退出登录</Button>
    </footer>
  </section>;
}
