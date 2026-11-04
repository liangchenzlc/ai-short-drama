import type { AssistantPreferences, AssistantSettings } from '@host/types/assistant/api';

export interface AssistantSettingsState {
  saved: AssistantSettings | null;
  draft: AssistantPreferences | null;
  revision: number;
}
