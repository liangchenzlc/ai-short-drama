import React from "react";
import "@ant-design/v5-patch-for-react-19";
import { createRoot } from "react-dom/client";
import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import { App } from "./app/App";
import { BrowserRouter } from 'react-router-dom';
import "./app/styles.css";
import "./app/studio.css";
import "./app/web.css";
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ConfigProvider
      button={{ autoInsertSpace: false }}
      locale={zhCN}
      theme={{
        token: {
          colorPrimary: "#94601f",
          colorLink: "#94601f",
          colorLinkHover: "#79501d",
          colorLinkActive: "#634017",
          colorBgLayout: "#f3f3f1",
          colorBorder: "#d9dad5",
          colorText: "#292d32",
          colorTextPlaceholder: "#72746e",
          colorTextSecondary: "#666a65",
          borderRadius: 7,
          controlHeight: 36,
          fontSize: 13,
          fontFamily: '"Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
        },
        components: {
          Button: {
            colorPrimary: "#94601f",
            colorPrimaryHover: "#79501d",
            colorPrimaryActive: "#634017",
          },
        },
      }}
    >
      <BrowserRouter><App /></BrowserRouter>
    </ConfigProvider>
  </React.StrictMode>,
);
