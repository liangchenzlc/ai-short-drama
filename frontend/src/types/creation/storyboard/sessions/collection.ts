import type { AgentSubject } from '@host/types/assistant/references/agent-scope';
import type { RefObject } from 'react';
export interface StoryboardCollectionOptions {
  projectId: string;
  account: string;
  refreshToken: number;
  subject: AgentSubject | null;
  selectSubject?: (subject: AgentSubject | null) => void;
  shotLocks: RefObject<Map<string, symbol>>;
  context: { epoch: RefObject<number>; isCurrent: (epoch: number) => boolean };
  setMessage: (message: string) => void;
}
