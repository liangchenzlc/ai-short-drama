import '@ant-design/v5-patch-for-react-19';
import { App } from '@host/app/App';
import { AppProviders } from '@host/app/providers/AppProviders';
import React from 'react';
import { createRoot } from 'react-dom/client';
import './app/styles/index.css';
import './pages/identity/identity.css';
createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <AppProviders>
      <App />
    </AppProviders>
  </React.StrictMode>,
);
