export const AI_CONFIGS_CHANGED = 'short-drama:ai-configs-changed';

export function notifyAiConfigsChanged() {
  window.dispatchEvent(new Event(AI_CONFIGS_CHANGED));
}
