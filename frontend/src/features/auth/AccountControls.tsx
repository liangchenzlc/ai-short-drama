import { Icon } from '../../components/ui/Icon';
import { Link, useLocation } from 'react-router-dom';
import { accountPath } from '../../app/paths';
import { useAuth } from './AuthSession';
import './account-center.css';

export function AccountControls({ className = '' }: { className?: string }) {
  const auth = useAuth();
  const location = useLocation();
  if (!auth.enabled || !auth.user) return null;
  const name = auth.user.display_name || auth.user.username;
  return <div className={`${className} account-controls`}>
    <Link className="account-trigger" aria-label={`账号中心：${name}`} title="账号中心" to={location.pathname === accountPath ? accountPath + location.search : `${accountPath}?next=${encodeURIComponent(location.pathname + location.search)}`} aria-current={location.pathname === accountPath ? 'page' : undefined}>
      <span className="account-avatar" aria-hidden="true"><Icon name="person" size={16} /></span>
      <span className="account-trigger-name">{name}</span>
    </Link>
  </div>;
}
