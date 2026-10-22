import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { ConfigProvider } from 'antd';
import '@ant-design/v5-patch-for-react-19';
import { CreativeAssistantPanel } from '../../../src/features/ai-assistant/CreativeAssistantPanel';
import { studioTheme } from '../../../src/app/theme';
import '../../../src/app/styles.css';
import '../../../src/app/studio.css';
import '../../../src/app/workbench.css';

const parameters = new URLSearchParams(location.search);
const standard = parameters.get('layout') === 'standard';
const projectId = parameters.get('project') ?? '10';
createRoot(document.getElementById('root')!).render(<StrictMode><ConfigProvider theme={studioTheme} button={{ autoInsertSpace: false }}>
  <main style={{ width: 'min(360px, 100%)', height: '100dvh' }}><CreativeAssistantPanel
    accountId="9007199254740993" projectId={projectId} projectTitle="展示兼容性" layout={standard ? 'standard' : undefined}
    selectedConversationId={parameters.get('conversation')}
    executionTarget={standard ? { mode: 'standard', project_id: projectId } : { mode: 'infinite_canvas', project_id: projectId, canvas_id: 'canvas-key' }}
    prepareContext={async () => null} onClose={() => {}} onOpenModelSettings={() => {}}/>
  </main>
</ConfigProvider></StrictMode>);
