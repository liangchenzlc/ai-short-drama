import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Alert, Button, Input, type InputRef } from 'antd';
import { errorMessage, http } from '../../api/http';

export function EmailProofForm({ reset = false, initialEmail = '', initialChallenge = '', readOnlyEmail = false, onSuccess, onBusyChange }: {
  reset?: boolean; initialEmail?: string; initialChallenge?: string; readOnlyEmail?: boolean;
  onSuccess: () => void; onBusyChange?: (busy: boolean) => void;
}) {
  const [email, setEmail] = useState(initialEmail);
  const [challenge, setChallenge] = useState(initialChallenge);
  const [code, setCode] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [remaining, setRemaining] = useState(initialChallenge ? 60 : 0);
  const emailInput = useRef<InputRef>(null);
  useEffect(() => {
    if (remaining <= 0) return;
    const timer = window.setTimeout(() => setRemaining(value => value - 1), 1000);
    return () => window.clearTimeout(timer);
  }, [remaining]);

  function processing(value: boolean) { setBusy(value); onBusyChange?.(value); }
  async function send() {
    if (busy || remaining || !emailInput.current?.input?.reportValidity()) return;
    processing(true); setError('');
    try {
      setChallenge((await http.post<{ challenge_id: string }>(reset ? '/auth/password/request' : '/auth/verification/request', { email })).data.challenge_id);
      setRemaining(60);
    } catch (cause) { setError(errorMessage(cause)); }
    finally { processing(false); }
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || !challenge) return;
    processing(true); setError('');
    try {
      await http.post(reset ? '/auth/password/reset' : '/auth/verification/confirm', { challenge_id: challenge, code, ...(reset ? { password } : {}) });
      setPassword(''); onSuccess();
    } catch (cause) { setError(errorMessage(cause)); }
    finally { processing(false); }
  }

  return <form className="email-proof-form" onSubmit={submit}>
    <label htmlFor="proof-email">注册邮箱<Input ref={emailInput} id="proof-email" type="email" autoComplete="email" required maxLength={254} readOnly={readOnlyEmail} disabled={busy} value={email} onChange={event => { setEmail(event.target.value); setChallenge(''); setError(''); }} /></label>
    <Button disabled={busy || remaining > 0 || !email} onClick={() => void send()}>{remaining ? `${remaining} 秒后可重发` : '发送验证码'}</Button>
    {challenge && <p role="status">若此邮箱可用于验证，验证码将发送到邮箱，十分钟内有效。请查看收件箱或垃圾邮件。</p>}
    <label htmlFor="proof-code">邮箱验证码<Input id="proof-code" autoComplete="one-time-code" inputMode="numeric" pattern="[0-9]{6}" maxLength={6} required disabled={busy} value={code} onChange={event => setCode(event.target.value)} /></label>
    {reset && <label htmlFor="new-password">新密码<Input.Password id="new-password" autoComplete="new-password" minLength={12} maxLength={128} required disabled={busy} value={password} onChange={event => setPassword(event.target.value)} /></label>}
    {error && <Alert type="error" message={error} />}
    <Button type="primary" block htmlType="submit" aria-label={reset ? '验证并更新密码' : '验证邮箱'} loading={busy} disabled={!challenge || busy}>{reset ? '验证并更新密码' : '验证邮箱'}</Button>
  </form>;
}
