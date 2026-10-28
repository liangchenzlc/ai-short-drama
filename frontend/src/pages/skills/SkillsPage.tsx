import { useAuth } from '../../features/auth/AuthSession';
import { SkillLibrary } from '../../features/skills/SkillLibrary';

export function SkillsPage() {
  const auth = useAuth();
  return <SkillLibrary accountId={auth.user?.id ?? 'anonymous'}/>;
}
