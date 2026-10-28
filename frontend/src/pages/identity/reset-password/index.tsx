import { AccountFrame } from '@host/app/layouts/account/index';
import { EmailProofContent } from '@host/features/identity/forms/EmailProofContent';
import './index.css';
export function ResetPasswordPage() {
  return (
    <AccountFrame title="找回账号密码">
      <EmailProofContent reset />
    </AccountFrame>
  );
}
