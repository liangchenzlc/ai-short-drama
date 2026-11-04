import { ModelSettings } from '@host/features/ai-config/editor/ModelSettings';
import { safeCanvasReturn } from '@host/features/ai-config/editor/config-form-model';
import { AssistantGeneralSettings } from '@host/features/assistant/settings/AssistantGeneralSettings';
import { AssistantInstructions } from '@host/features/assistant/settings/AssistantInstructions';
import { AssistantMemorySettings } from '@host/features/assistant/settings/AssistantMemorySettings';
import { McpAccessTokens } from '@host/features/assistant/settings/McpAccessTokens';
import { SettingsFeedback } from '@host/features/assistant/settings/SettingsFeedback';
import { useSettingsSession } from '@host/features/assistant/settings/SettingsSession';
import { StudioFeedbackIndicator } from '@host/shared/ui/StudioFeedback';
import type { ServiceType } from '@host/types/ai-config/editor/config-model';
import { Button, Skeleton } from 'antd';
import { Link, Navigate, NavLink, useLocation } from 'react-router-dom';
import '@host/features/assistant/settings/assistant-settings.css';
import './index.css';

const sections = {
  'models/text': '文本模型',
  'models/media/image': '媒体模型',
  'models/media/video': '媒体模型',
  'models/media/audio': '媒体模型',
  'assistant/general': '通用设置',
  'assistant/memory': '长期记忆',
  'assistant/instructions': '个性化指令',
  mcp: 'MCP 令牌',
};
const mediaTypes = ['image', 'video', 'audio'] as const;
const mediaLabels = { image: '图片模型', video: '视频模型', audio: '音频模型' };

export function SettingsPage() {
  const { pathname, search } = useLocation();
  const settings = useSettingsSession();
  const section = pathname.replace(/^\/settings\/?/, '');
  const returnTo = safeCanvasReturn(new URLSearchParams(search).get('return_to'));
  const query = returnTo ? `?return_to=${encodeURIComponent(returnTo)}` : '';
  const media = section.startsWith('models/media/');
  const assistant = section.startsWith('assistant/');
  const serviceType = section === 'models/text' ? 'text' : section.split('/')[2] as ServiceType;
  const link = (path: string, label: string, pending = false) => <NavLink to={`/settings/${path}${query}`}
    className={({ isActive }) => `settings-nav-item${isActive ? ' active' : ''}`}>
    {label}{pending && <span className="settings-pending-dot" aria-label="有未同步修改" />}
  </NavLink>;
  if (!(section in sections)) return <Navigate to={`/settings/models/text${query}`} replace />;

  return <section className="studio-page settings-page" aria-labelledby="settings-title">
    <header className="settings-heading"><h1 id="settings-title">配置</h1>
      <div><StudioFeedbackIndicator />{returnTo && <Button onClick={() => window.location.assign(returnTo)}>返回画布</Button>}</div>
    </header>
    <div className="settings-layout">
      <nav className="settings-navigation" aria-label="配置导航">
        <div className="settings-nav-group"><p>模型配置</p>
          {link('models/text', '文本模型')}
          <Link to={`/settings/models/media/image${query}`} className={`settings-nav-item${media ? ' active' : ''}`} aria-current={media ? 'page' : undefined}>媒体模型</Link>
        </div>
        <div className="settings-nav-group"><p>AI 创作助手</p>
          {link('assistant/general', '通用设置', !!settings.draft && (settings.draft.workbench_enabled !== settings.saved?.workbench_enabled || settings.draft.media_generation_enabled !== settings.saved?.media_generation_enabled))}
          {link('assistant/memory', '长期记忆', !!settings.draft && settings.draft.long_term_memory_enabled !== settings.saved?.long_term_memory_enabled)}
          {link('assistant/instructions', '个性化指令', !!settings.draft && settings.draft.custom_instructions !== settings.saved?.custom_instructions)}
        </div>
        <div className="settings-nav-group">{link('mcp', 'MCP 令牌')}</div>
      </nav>
      <div className="settings-panel" aria-label={sections[section as keyof typeof sections]}>
        {media && <nav className="settings-media-tabs" aria-label="媒体模型类型">{mediaTypes.map(kind =>
          <NavLink key={kind} to={`/settings/models/media/${kind}${query}`} className={({ isActive }) => isActive ? 'active' : ''}>{mediaLabels[kind]}</NavLink>,
        )}</nav>}
        {(section === 'models/text' || media) && <ModelSettings key={serviceType} serviceType={serviceType} />}
        {assistant && <><SettingsFeedback />
          {settings.loading && !settings.draft ? <Skeleton active paragraph={{ rows: 3 }} /> : <>
            {section === 'assistant/general' && <AssistantGeneralSettings />}
            {section === 'assistant/memory' && <AssistantMemorySettings onManage={settings.openMemory} />}
            {section === 'assistant/instructions' && <AssistantInstructions />}
          </>}
        </>}
        <div hidden={section !== 'mcp'}><McpAccessTokens active={section === 'mcp'} /></div>
      </div>
    </div>
  </section>;
}
