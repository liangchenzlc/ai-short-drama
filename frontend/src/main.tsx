import React from "react";
import "@ant-design/v5-patch-for-react-19";
import { createRoot } from "react-dom/client";
import { AuthProvider } from "./features/auth/AuthSession";
import { App } from "./app/App";
import { StudioProvider } from './components/ui/StudioProvider';
import { BrowserRouter } from 'react-router-dom';
import "./app/styles.css";
import "./app/studio.css";
import "./app/web.css";
import "./app/production.css";
import './app/workbench.css';
import './app/identity.css';
createRoot(document.getElementById("root")!).render(
  <React.StrictMode><StudioProvider><BrowserRouter><AuthProvider><App /></AuthProvider></BrowserRouter></StudioProvider></React.StrictMode>,
);
