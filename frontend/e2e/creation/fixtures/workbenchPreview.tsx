import '@host/app/styles/index.css';
import '@ant-design/v5-patch-for-react-19';
import '@host/pages/creation/index.css';
import '@host/features/creation/workspace/layout/workbenchView.css';
import { StudioProvider } from '@host/shared/ui/StudioProvider';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { WorkbenchPreviewApp } from './workbenchPreviewApp';

createRoot(document.getElementById('root')!).render(
  <StudioProvider><MemoryRouter><WorkbenchPreviewApp /></MemoryRouter></StudioProvider>,
);