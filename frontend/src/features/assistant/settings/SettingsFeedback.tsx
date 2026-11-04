import { useSettingsSession } from '@host/features/assistant/settings/SettingsSession';
import { Dialog } from '@host/shared/ui/Dialog';
import { StudioFeedback } from '@host/shared/ui/StudioFeedback';
import { Button } from 'antd';
import { useState } from 'react';

const labels = { custom_instructions: '个性化指令', workbench_enabled: '工作台操作', media_generation_enabled: '媒体生成', long_term_memory_enabled: '长期记忆' };
const display = (value: string | boolean | null) => typeof value === 'boolean' ? value ? '开启' : '关闭' : value || '使用默认指令';
export function SettingsFeedback() {
  const settings = useSettingsSession();
  const [reviewing, setReviewing] = useState(true);
  return <>
    {settings.issue && <StudioFeedback persistent type="error" title="设置尚未同步" message={settings.issue}
      actions={settings.conflict
        ? <Button onClick={() => setReviewing(true)}>核对变更</Button>
        : <Button onClick={() => void settings.read()} loading={settings.loading}>重新核对</Button>} />}
    {settings.conflict && reviewing && <Dialog title="核对设置变更" onClose={() => setReviewing(false)}>
      <p className="muted">请选择每项要保留的内容，选择后自动同步。</p>
      <div className="settings-conflicts">{settings.conflictFields.map(key => <section key={key}>
        <strong>{labels[key]}</strong>
        <div><span>我的输入</span><p>{display(settings.draft![key])}</p><Button onClick={() => settings.resolve(key, false)}>保留我的输入</Button></div>
        <div><span>服务端内容</span><p>{display(settings.conflict![key])}</p><Button onClick={() => settings.resolve(key, true)}>使用服务端内容</Button></div>
      </section>)}</div>
    </Dialog>}
  </>;
}
