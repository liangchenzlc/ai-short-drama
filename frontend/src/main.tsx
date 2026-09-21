import React from "react";
import "@ant-design/v5-patch-for-react-19";
import { createRoot } from "react-dom/client";
import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import { App } from "./app/App";
import { studioTheme } from "./app/theme";
import { BrowserRouter } from 'react-router-dom';
import "./app/styles.css";
import "./app/studio.css";
import "./app/web.css";
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ConfigProvider
      button={{ autoInsertSpace: false }}
      locale={zhCN}
      theme={studioTheme}
    >
      <BrowserRouter><App /></BrowserRouter>
    </ConfigProvider>
  </React.StrictMode>,
);
