import type { AssistantMentionReference } from '@host/types/assistant/api';

export interface MentionReference extends AssistantMentionReference {
  thumbnail?: string | null;
}
