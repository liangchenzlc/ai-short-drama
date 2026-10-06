import { createRoot } from "react-dom/client";
import { WorkspaceErrorState } from "@/components/layout/workspace-state";
import "antd/dist/reset.css";
import "@/styles/globals.css";
import "@/styles/beeftv-local-overrides.css";

export function renderStartupError() {
    createRoot(document.getElementById("root")!).render(
        <WorkspaceErrorState title="工作区暂时无法加载" description="无法确认当前账号或连接画布服务。请重新连接后继续。" onRetry={() => window.location.reload()} />,
    );
}
