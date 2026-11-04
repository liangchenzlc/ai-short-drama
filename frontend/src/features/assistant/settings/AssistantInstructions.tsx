import { useSettingsSession } from '@host/features/assistant/settings/SettingsSession';
import { Button, Input } from 'antd';

export function AssistantInstructions() {
  const settings = useSettingsSession();
  const value = settings.draft?.custom_instructions ?? '';
  return <div className="assistant-settings-content assistant-instructions">
    <label htmlFor="assistant-instructions">你希望助手了解什么，或如何回应你？</label>
    <Input.TextArea id="assistant-instructions" value={value} disabled={!settings.draft} maxLength={16000} rows={12}
      placeholder={settings.saved?.default_instructions ?? '例如：优先使用简洁对白，先与我讨论创作方向。'}
      onChange={event => settings.edit({ custom_instructions: event.target.value.trim() ? event.target.value : null }, !event.target.value)} />
    <div className="assistant-instructions-footer"><span>留空使用默认指令，下次回复生效。</span><span>{value.length.toLocaleString()} / 16,000</span>
      <Button type="text" disabled={!value} onClick={() => settings.edit({ custom_instructions: null }, true)}>清空</Button>
    </div>
  </div>;
}
