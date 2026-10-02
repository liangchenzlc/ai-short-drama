import { Icon } from '../../components/ui/Icon';
import { useAccountCenter } from './AccountCenter';
import { useAuth } from './AuthSession';

export function AccountControls({ className = '' }: { className?: string }) {
  const auth = useAuth();
  const center = useAccountCenter();
  if (!auth.enabled || !auth.user) return null;
  const name = auth.user.display_name || auth.user.username;
  return <div className={`${className} account-controls`}>
    <button type="button" className="account-trigger" aria-label={`账号中心：${name}`} title="账号中心" aria-haspopup="dialog" aria-expanded={center.open} disabled={center.busy} onClick={center.show}>
      <span className="account-avatar" aria-hidden="true"><Icon name="person" size={16} /></span>
      <span className="account-trigger-name">{name}</span>
      <span className="account-trigger-chevron" aria-hidden="true" />
    </button>
  </div>;
}
