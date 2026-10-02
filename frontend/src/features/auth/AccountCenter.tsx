import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { Button } from 'antd';
import { errorMessage } from '../../api/http';
import { confirmAction } from '../../components/ui/confirm';
import { Dialog } from '../../components/ui/Dialog';
import { Icon } from '../../components/ui/Icon';
import { useAuth } from './AuthSession';
import { EmailProofForm } from './EmailProofForm';

const Context = createContext({ open: false, busy: false, show: () => {} });
export function useAccountCenter() { return useContext(Context); }

export function AccountCenterProvider({ children }: { children: ReactNode }) {
  const auth = useAuth();
  const [open, setOpen] = useState(false);
  const [passwordOpen, setPasswordOpen] = useState(false);
  const [passwordBusy, setPasswordBusy] = useState(false);
  const [logoutBusy, setLogoutBusy] = useState(false);
  const [error, setError] = useState('');
  const busy = passwordBusy || logoutBusy;

  useEffect(() => {
    setOpen(false); setPasswordOpen(false); setError('');
  }, [auth.expired, auth.user?.id]);

  function close() { setOpen(false); setPasswordOpen(false); setError(''); }
  async function logout() {
    if (busy || !await confirmAction('退出登录前，请确认创作内容已保存。', { title: '退出登录', confirmText: '退出登录' })) return;
    setLogoutBusy(true); setError('');
    try { await auth.logout(); }
    catch (cause) { setError(errorMessage(cause)); }
    finally { setLogoutBusy(false); }
  }
  function passwordChanged() {
    close();
    // The password endpoint revokes every session. Reauthenticate in place so drafts stay mounted.
    window.dispatchEvent(new Event('session-expired'));
  }

  const user = auth.user;
  return <Context.Provider value={{ open, busy, show: () => setOpen(true) }}>
    {children}
    {auth.enabled && user && open && !auth.expired && <Dialog title="账号中心" className="account-center-dialog" canClose={!busy} onClose={close}>
      <div className="account-center-body">
        <div className="account-profile">
          <span className="account-profile-avatar" aria-hidden="true"><Icon name="person" size={26} /></span>
          <strong>{user.display_name || user.username}</strong>
        </div>
        <section className="account-section" aria-labelledby="account-profile-title">
          <h3 id="account-profile-title">账号信息</h3>
          <dl className="account-details">
            <div><dt>账号名</dt><dd>{user.username}</dd></div>
            <div><dt>账号 ID</dt><dd>{user.id}</dd></div>
            <div><dt>注册邮箱</dt><dd><span>{user.email || '未设置邮箱'}</span>{user.email && <span className={`account-email-status${user.email_verified ? ' is-verified' : ''}`}>{user.email_verified ? '已验证' : '未验证'}</span>}</dd></div>
          </dl>
        </section>
        <section className="account-section" aria-labelledby="account-security-title">
          <h3 id="account-security-title">账号安全</h3>
          <div className="account-security-entry">
            <div><strong>登录密码</strong><p>{user.email ? '通过注册邮箱验证身份后设置新密码。' : '未设置邮箱，暂无法通过邮箱修改密码。'}</p></div>
            <Button disabled={busy || !user.email} aria-expanded={passwordOpen} aria-controls="account-password-form" onClick={() => setPasswordOpen(!passwordOpen)}>{passwordOpen ? '取消修改' : '修改密码'}</Button>
          </div>
          {passwordOpen && <div id="account-password-form" className="account-password-form">
            <p>密码更新后，所有登录会话将失效。当前页面的编辑会保留，请用新密码重新登录。</p>
            <EmailProofForm reset initialEmail={user.email} readOnlyEmail onSuccess={passwordChanged} onBusyChange={setPasswordBusy} />
          </div>}
        </section>
      </div>
      <footer className="account-center-footer">
        <Button danger loading={logoutBusy} disabled={busy} onClick={() => void logout()}>退出登录</Button>
        <Button disabled={busy} onClick={close}>返回创作</Button>
      </footer>
    </Dialog>}
    {error && <Dialog title="退出登录未完成" onClose={() => setError('')}>
      <div className="account-error"><p role="alert">{error}</p><p>当前账号仍保持登录，可以返回创作后重试。</p>
        <div className="dialog-actions"><Button onClick={close}>返回创作</Button></div>
      </div>
    </Dialog>}
  </Context.Provider>;
}
