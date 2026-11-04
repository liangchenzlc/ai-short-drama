import { useSettingsSession } from '@host/features/assistant/settings/SettingsSession';
import { SettingRow } from '@host/shared/ui/SettingRow';
import { Button, Switch } from 'antd';

export function AssistantMemorySettings({ onManage }: { onManage: () => void }) {
  const settings = useSettingsSession();
  return <div className="assistant-settings-content">
    <SettingRow title="长期记忆" description="记住你的创作偏好，在后续交流中使用。关闭后保留已有记忆，暂停学习和使用。">
      <Switch aria-label="长期记忆" checked={settings.draft?.long_term_memory_enabled ?? false} disabled={!settings.draft} onChange={checked => settings.edit({ long_term_memory_enabled: checked }, true)} />
    </SettingRow>
    <SettingRow title="管理记忆" description="查看、修改或删除已记住的内容。">
      <Button onClick={onManage}>管理记忆</Button>
    </SettingRow>
  </div>;
}
