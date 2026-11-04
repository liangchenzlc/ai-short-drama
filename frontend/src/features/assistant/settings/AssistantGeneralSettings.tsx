import { useSettingsSession } from '@host/features/assistant/settings/SettingsSession';
import { SettingRow } from '@host/shared/ui/SettingRow';
import { Switch } from 'antd';

export function AssistantGeneralSettings() {
  const settings = useSettingsSession();
  const value = settings.draft;
  return <div className="assistant-settings-content">
    <SettingRow title="工作台操作" description="允许助手修改作品内容、管理素材并操作工作台。关闭后仍可讨论和分析你引用的资料。">
      <Switch aria-label="工作台操作" checked={value?.workbench_enabled ?? false} disabled={!value} onChange={checked => settings.edit({ workbench_enabled: checked }, true)} />
    </SettingRow>
    {value?.workbench_enabled && <SettingRow nested title="允许生成图片、视频、音频" description="允许助手调用已配置的媒体模型。生成可能产生服务商费用。">
      <Switch aria-label="允许生成图片、视频、音频" checked={value.media_generation_enabled} onChange={checked => settings.edit({ media_generation_enabled: checked }, true)} />
    </SettingRow>}
  </div>;
}
