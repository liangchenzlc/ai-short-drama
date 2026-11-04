import type { AssistantPreferences, AssistantSettings, AssistantSettingsPatch } from '@host/types/assistant/api';
import type { AssistantSettingsState } from '@host/types/assistant/settings/editor';

export const settingFields = ['custom_instructions', 'workbench_enabled', 'media_generation_enabled', 'long_term_memory_enabled'] as const;
export const settingsState = (): AssistantSettingsState => ({ saved: null, draft: null, revision: 0 });
const preferences = (value: AssistantSettings): AssistantPreferences => ({
  custom_instructions: value.custom_instructions,
  workbench_enabled: value.workbench_enabled,
  media_generation_enabled: value.media_generation_enabled,
  long_term_memory_enabled: value.long_term_memory_enabled,
});

export function editSettings(state: AssistantSettingsState, patch: Partial<AssistantPreferences>): AssistantSettingsState {
  return state.draft ? { ...state, revision: state.revision + 1, draft: { ...state.draft, ...patch } } : state;
}

export function settingsPatch(state: AssistantSettingsState): AssistantSettingsPatch {
  return {
    settings_version: state.saved!.settings_version,
    ...Object.fromEntries(settingFields.filter(key => state.draft![key] !== state.saved![key]).map(key => [key, state.draft![key]])),
  };
}

export function settingsDirty(state: AssistantSettingsState): boolean {
  return !!state.saved && settingFields.some(key => state.draft![key] !== state.saved![key]);
}

export function settingsConflicts(state: AssistantSettingsState, next: AssistantSettings): (keyof AssistantPreferences)[] {
  return state.saved ? settingFields.filter(key => state.draft![key] !== state.saved![key] && next[key] !== state.saved![key] && next[key] !== state.draft![key]) : [];
}

export function receiveSettings(state: AssistantSettingsState, next: AssistantSettings, startedRevision: number, saved = false, submitted?: AssistantSettingsPatch): AssistantSettingsState {
  if (state.saved && BigInt(next.settings_version) < BigInt(state.saved.settings_version)) return state;
  if (!state.saved || !state.draft) return { ...state, saved: next, draft: preferences(next) };
  // 只保留本地真正修改的字段；已提交的字段以提交快照核对，避免旧响应盖住后续输入。
  const changes = Object.fromEntries(settingFields.filter(key => {
    if (saved && submitted && key in submitted) return state.draft![key] !== submitted[key];
    if (saved && !submitted && state.revision === startedRevision) return false;
    return state.draft![key] !== state.saved![key];
  }).map(key => [key, state.draft![key]]));
  return { ...state, saved: next, draft: { ...preferences(next), ...changes } };
}
