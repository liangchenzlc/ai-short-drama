import { useAuth } from '@host/features/identity/session/AuthSession';
import { SkillLibrary } from '@host/features/skills/library/SkillLibrary';
export function SkillsPage() {
  const auth = useAuth();
  return <SkillLibrary accountId={auth.user?.id ?? 'anonymous'} />;
}
