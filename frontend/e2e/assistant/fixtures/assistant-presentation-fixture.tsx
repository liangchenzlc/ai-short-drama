import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { ConfigProvider } from 'antd';
import '@ant-design/v5-patch-for-react-19';
import { CreativeAssistantPanel } from '../../../src/features/ai-assistant/CreativeAssistantPanel';
import { studioTheme } from '../../../src/app/theme';
import '../../../src/app/styles.css';
import '../../../src/app/studio.css';
import '../../../src/app/workbench.css';

const standard = new URLSearchParams(location.search).get('layout') === 'standard';
createRoot(document.getElementById('root')!).render(<StrictMode><ConfigProvider theme={studioTheme} button={{ autoInsertSpace: false }}>
  <main style={{ width: 'min(360px, 100%)', height: '100dvh' }}><CreativeAssistantPanel
    accountId="9007199254740993" projectId="10" projectTitle="展示兼容性" layout={standard ? 'standard' : undefined}
    prepareContext={async () => null} onClose={() => {}} onOpenModelSettings={() => {}}/>
  </main>
</ConfigProvider></StrictMode>);
