import React from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import '@ant-design/v5-patch-for-react-19';
import { AssemblyStage } from '../../../src/pages/projects/episode/AssemblyStage';
import { ConfigCatalogProvider } from '../../../src/features/ai-config/ConfigCatalogProvider';
import { EpisodeCreationWorkspace } from '../../../src/features/projects/EpisodeCreationWorkspace';
import { StudioProvider } from '../../../src/components/ui/StudioProvider';
import { StandardSaveStatus } from '../../../src/features/projects/StandardSaveStatus';
import '../../../src/app/styles.css';
import '../../../src/app/studio.css';
import '../../../src/app/web.css';
import '../../../src/app/workbench.css';

const stage = <AssemblyStage projectId="10" readOnly={false} registerBarrier={() => {}} onStoryboard={() => {}}/>;
const inline = new URLSearchParams(location.search).has('inline');
createRoot(document.getElementById('root')!).render(<React.StrictMode><StudioProvider><MemoryRouter>
  <ConfigCatalogProvider><main className="web-episode" style={{ padding: 24, maxWidth: 1360, margin: '0 auto' }}><header className="episode-top"><StandardSaveStatus/></header>{inline ? <EpisodeCreationWorkspace stage="assembly" assistantOpen={false} assistantPanel={null} onCloseAssistant={() => {}} workRequest={0} subject={null} onSubject={() => {}}>{stage}</EpisodeCreationWorkspace> : stage}</main></ConfigCatalogProvider>
</MemoryRouter></StudioProvider></React.StrictMode>);
