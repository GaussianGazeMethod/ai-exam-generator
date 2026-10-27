import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntdApp, ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import App from "./App";
import "./index.css";
import "./app.css";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
});

/**
 * 主题与 UIdesign 评审稿色板对齐（app.css 的 CSS 变量是同一套色值的来源）。
 * 评审稿视觉主线：蓝色=主要行动，青色=完成/合规，琥珀=提醒，红色=弃用/错误。
 */
const theme = {
  token: {
    colorPrimary: "#2f6bff",
    colorSuccess: "#24a477",
    colorWarning: "#e8a63d",
    colorError: "#e15d65",
    colorInfo: "#2f6bff",
    colorTextBase: "#17243a",
    colorBgLayout: "#f4f7fb",
    borderRadius: 10,
    fontSize: 13,
    fontFamily:
      'Inter, "PingFang SC", "Microsoft YaHei", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
  },
  components: {
    Card: { borderRadiusLG: 14 },
    Table: { headerBg: "#fbfcfe", headerColor: "#8795a8", cellPaddingBlock: 12 },
    Button: { controlHeight: 34, fontWeight: 500 },
    Menu: { itemSelectedBg: "#2a4770" },
    Tabs: { titleFontSize: 13 },
  },
};

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ConfigProvider locale={zhCN} theme={theme}>
      <AntdApp>
        <QueryClientProvider client={queryClient}>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </QueryClientProvider>
      </AntdApp>
    </ConfigProvider>
  </React.StrictMode>,
);