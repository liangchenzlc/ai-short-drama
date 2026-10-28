import '@host/app/styles/index.css';
import React from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import '@ant-design/v5-patch-for-react-19';
import { AssemblyStage } from '../../../src/pages/creation/assembly/index';
import { ConfigCatalogProvider } from '../../../src/features/ai-config/catalog/ConfigCatalogProvider';
import { CreationWorkspace } from '../../../src/features/creation/workspace/layout/CreationWorkspace';
import { StudioProvider } from '../../../src/shared/ui/StudioProvider';
import { StandardSaveStatus } from '../../../src/features/creation/shared/feedback/StandardSaveStatus';

const stage = <AssemblyStage projectId="10" readOnly={false} registerBarrier={() => {}} onStoryboard={() => {}}/>;
const inline = new URLSearchParams(location.search).has('inline');
createRoot(document.getElementById('root')!).render(<React.StrictMode><StudioProvider><MemoryRouter>
  <ConfigCatalogProvider><main className="web-episode" style={{ padding: 24, maxWidth: 1360, margin: '0 auto' }}><header className="creation-top"><StandardSaveStatus/></header>{inline ? <CreationWorkspace stage="assembly" assistantOpen={false} assistantPanel={null} onCloseAssistant={() => {}} workRequest={0} subject={null} onSubject={() => {}}>{stage}</CreationWorkspace> : stage}</main></ConfigCatalogProvider>
</MemoryRouter></StudioProvider></React.StrictMode>);
