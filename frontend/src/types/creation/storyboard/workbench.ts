import type { CreationPreferences } from '@host/types/creation/workspace/model';
import type { NavigationBarrier } from '@host/types/creation/writing/sessions/writing-navigation';
import type { WritingSnapshot } from '@host/types/creation/writing/sessions/writing-session';

export type StoryboardWorkspaceProps = {
  value: CreationPreferences;
  readOnly: boolean;
  onChange: (next: CreationPreferences) => void;
  projectId: string;

  contentVersion: string;
  scriptId: string | null;
  confirmed: boolean;
  writingSession: { getSnapshot(): Pick<WritingSnapshot, 'contentVersion'> };
  registerBarrier: (barrier: NavigationBarrier | null) => void;
  refreshToken?: number;
  sourceMedia?: 'image' | 'video';
};
