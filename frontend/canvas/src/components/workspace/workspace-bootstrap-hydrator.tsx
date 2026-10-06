import type { ReactNode } from "react";
import { useEffect, useRef, useState } from "react";

import { FullScreenLoader } from "@/components/ui/aceternity/full-screen-loader";
import { WorkspaceErrorState } from "@/components/layout/workspace-state";
import { appPathname } from "@/lib/app-routing";
import { preloadWorkspaceRoute } from "@/lib/workspace-route-modules";
import { applyUserSession, localWorkspaceConfig } from "@/lib/user-session";
import { getWorkspaceBootstrap } from "@/services/api/workspace";
import { commitModelConfig, flushModelConfig, hydrateModelConfig, modelConfigAcknowledgementInProgress } from "@/services/model-config-repository";
import { normalizeConfigSnapshot, useConfigStore } from "@/stores/use-config-store";
import { useUserStore } from "@/stores/use-user-store";
import { hydrateLocalCanvasProjectsFromBackend } from "@/services/local-workspace-repository";
import { initializeWorkspaceState } from "@/services/workspace-bootstrap";
import { canvasSignInUrl } from "@/services/host-session";

let workspaceRestore: Promise<void> | undefined;
function restoreWorkspace() {
    workspaceRestore ??= initializeWorkspaceState({
        loadWorkspace: getWorkspaceBootstrap,
        applySession: applyUserSession,
        restoreModelConfig: hydrateLocalModelConfig,
        restoreProjects: () => hydrateLocalCanvasProjectsFromBackend({ strict: true }),
    });
    return workspaceRestore;
}

export function WorkspaceBootstrapHydrator({ children }: { children: ReactNode }) {
    const hydrated = useUserStore((state) => state.hydrated);
    const modelConfigReady = useRef(false);
    const [sessionExpired, setSessionExpired] = useState(false);
    const [restoreStatus, setRestoreStatus] = useState<"loading" | "ready" | "error">("loading");

    useEffect(() => {
        let cancelled = false;
        void restoreWorkspace()
            .then(() => {
                if (cancelled) return;
                modelConfigReady.current = true;
                setRestoreStatus("ready");
                preloadWorkspaceRoute(appPathname());
            })
            .catch(() => {
                if (cancelled) return;
                // The browser cache deliberately has no credentials. If the
                // canonical file could not be read, never autosave that cache
                // over the existing workspace when the backend comes back.
                modelConfigReady.current = false;
                useUserStore.getState().setHydrated(true);
                setRestoreStatus("error");
            });
        return () => {
            cancelled = true;
        };
    }, []);

    useEffect(() => {
        let ready = false;
        const unsubscribe = useConfigStore.subscribe((state) => {
            if (modelConfigAcknowledgementInProgress()) return;
            if (!shouldSaveLocalModelConfig({ subscriptionReady: ready, modelConfigReady: modelConfigReady.current, channelCount: state.config.channels.length })) return;
            void commitModelConfig(state.config);
        });
        const flush = () => { void flushModelConfig(); };
        window.addEventListener("pagehide", flush);
        const markReady = () => { ready = true; };
        if (modelConfigReady.current) markReady();
        else window.setTimeout(markReady, 0);
        return () => {
            unsubscribe();
            window.removeEventListener("pagehide", flush);
            void flushModelConfig();
        };
    }, []);

    useEffect(() => {
        const expired = () => { modelConfigReady.current = false; setSessionExpired(true); };
        window.addEventListener("session-expired", expired);
        return () => window.removeEventListener("session-expired", expired);
    }, []);

    if (sessionExpired) return <WorkspaceErrorState title="登录状态已变化" description="当前画布草稿已保留，请重新登录后继续。" actionLabel="重新登录" onRetry={() => window.location.assign(canvasSignInUrl())} />;
    if (restoreStatus === "error") return <WorkspaceErrorState title="工作区暂时无法加载" description="项目或模型配置未能完成读取。原有配置不会被覆盖，请重新加载后再试。" onRetry={() => window.location.reload()} />;
    return hydrated && restoreStatus === "ready" ? children : <FullScreenLoader label="正在准备工作区" detail="加载项目、画布与模型配置" />;
}

export function shouldSaveLocalModelConfig({ subscriptionReady, modelConfigReady, channelCount }: { subscriptionReady: boolean; modelConfigReady: boolean; channelCount: number }) {
    return subscriptionReady && modelConfigReady && channelCount >= 0;
}

async function hydrateLocalModelConfig() {
    const result = await hydrateModelConfig();
    const normalizedConfig = localWorkspaceConfig(normalizeConfigSnapshot({ config: result.config }).config);
    useConfigStore.getState().replaceConfig(normalizedConfig);
    if (shouldPersistHydratedModelConfig(result.health)) await commitModelConfig(normalizedConfig);
}

export function shouldPersistHydratedModelConfig(health: string) {
    return health === "migrated" || health === "recovered";
}

