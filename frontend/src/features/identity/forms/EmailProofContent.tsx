import { EmailProofForm } from '@host/features/identity/forms/EmailProofForm';
import { safeReturn } from '@host/features/identity/session/AuthSession';
import { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
export function EmailProofContent({ reset = false }: { reset?: boolean }) {
  const [params] = useSearchParams();
  const [done, setDone] = useState(false);
  return (
    <>
      {done ? (
        <>
          <p role="status">{reset ? '密码已更新，请重新登录。' : '邮箱已验证，现在可以登录。'}</p>
          <Link to={`/login?next=${encodeURIComponent(safeReturn(params.get('next')))}`}>返回登录</Link>
        </>
      ) : (
        <>
          <EmailProofForm
            key={`${reset}:${params.get('challenge') ?? ''}`}
            reset={reset}
            initialEmail={params.get('email') ?? ''}
            initialChallenge={params.get('challenge') ?? ''}
            onSuccess={() => setDone(true)}
          />
          <Link to="/login">返回登录</Link>
        </>
      )}
    </>
  );
}
