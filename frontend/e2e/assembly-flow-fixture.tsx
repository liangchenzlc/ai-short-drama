import React from 'react';
import { createRoot } from 'react-dom/client';
import { ConfigProvider } from 'antd';
import '@ant-design/v5-patch-for-react-19';
import { AssemblyStage } from '../src/pages/projects/episode/AssemblyStage';
import { ConfigCatalogProvider } from '../src/features/ai-config/ConfigCatalogProvider';
import { studioTheme } from '../src/app/theme';
import '../src/app/styles.css';
import '../src/app/studio.css';
import '../src/app/web.css';
import '../src/app/workbench.css';

createRoot(document.getElementById('root')!).render(<React.StrictMode><ConfigProvider theme={studioTheme} button={{ autoInsertSpace: false }}>
  <ConfigCatalogProvider><main style={{ padding: 24, maxWidth: 1360, margin: '0 auto' }}><AssemblyStage projectId="10" episodeId="20" readOnly={false} registerBarrier={() => {}} onStoryboard={() => {}}/></main></ConfigCatalogProvider>
</ConfigProvider></React.StrictMode>);
