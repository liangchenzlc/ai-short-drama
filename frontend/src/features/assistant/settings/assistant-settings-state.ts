import type { AssistantSettings, AssistantSettingsPatch } from '@host/types/assistant/api';
import type { AssistantSettingsState } from '@host/types/assistant/settings/editor';

export const settingsState = (): AssistantSettingsState => ({ saved: null, draft: null, revision: 0 });
const patchOf = (value: AssistantSettings): AssistantSettingsPatch => ({
  custom_instructions: value.custom_instructions,
  model_call_limit: value.model_call_limit,
  output_token_limit: value.output_token_limit,
  subagent_max_concurrency: value.subagent_max_concurrency,
  default_media_limits: { ...value.default_media_limits },
  settings_version: value.settings_version,
});
export function editSettings(state: AssistantSettingsState, patch: Partial<AssistantSettingsPatch>): AssistantSettingsState {
  return state.draft ? { ...state, revision: state.revision + 1, draft: { ...state.draft, ...patch } } : state;
}
export function settingsPatch(state: AssistantSettingsState): AssistantSettingsPatch {
  return { ...state.draft!, settings_version: state.saved!.settings_version };
}
export function settingsDirty(state: AssistantSettingsState) {
  return !!state.saved && JSON.stringify(settingsPatch(state)) !== JSON.stringify(patchOf(state.saved));
}
export function receiveSettings(state: AssistantSettingsState, next: AssistantSettings, startedRevision: number, saved = false): AssistantSettingsState {
  if (state.saved && BigInt(next.settings_version) < BigInt(state.saved.settings_version)) return state;
  // 读取和保存都不能覆盖请求发出后的输入；刷新时也保留已修改的草稿。
  const keep = state.draft && (state.revision !== startedRevision || (!saved && settingsDirty(state)));
  return { ...state, saved: next, draft: keep ? { ...state.draft!, settings_version: next.settings_version } : patchOf(next) };
}
