import { AccountFrame } from '@host/app/layouts/account/index';
import { EmailProofContent } from '@host/features/identity/forms/EmailProofContent';
import './index.css';
export function EmailProofPage() {
  return (
    <AccountFrame title="验证注册邮箱">
      <EmailProofContent />
    </AccountFrame>
  );
}
