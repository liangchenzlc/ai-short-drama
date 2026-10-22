import type { ReactNode } from "react";
import { useEffect, useRef } from "react";
import { App } from "antd";

import { navigateToSettings } from "@/lib/settings-navigation";
import { initializeClientDiagnostics, setDiagnosticUserScope } from "@/services/diagnostics/client-diagnostics";
import { usePluginStore } from "@/stores/use-plugin-store";
import { useAssetStore } from "@/stores/use-asset-store";
import { useCanvasStore } from "@/stores/canvas/use-canvas-store";
import { useUserStore } from "@/stores/use-user-store";
import { isLocalWorkspaceMode } from "@/services/workspace-mode";
import { getActiveUserScope } from "@/lib/user-scope";
import { confirmDesktopUpdateStartup } from "@/services/desktop-update-startup";
import { getRegisteredPlugin } from "@/lib/plugins/plugin-registry";

export function ClientRootInit({ children }: { children: ReactNode }) {
    const userId = useUserStore((state) => state.user?.id || "");
    const storageMode = useUserStore((state) => state.storageMode);
    const user = useUserStore((state) => state.user);
    const assetsHydrated = useAssetStore((state) => state.hydrated);
    const canvasHydrated = useCanvasStore((state) => state.hydrated);
    const localMode = isLocalWorkspaceMode() || storageMode === "local" || user?.username === "local";
    const { message } = App.useApp();
    const handledConfigParams = useRef(false);
    const pluginStoreHydrated = usePluginStore((state) => state.hydrated);
    const localMediaCleanupScope = useRef("");
    const updateStartupConfirmed = useRef(false);

    useEffect(() => {
        if (!pluginStoreHydrated) return;
        for (const id of ["prompt-optimizer", "ai-art-critique"]) {
            const plugin = getRegisteredPlugin(id);
            if (plugin) usePluginStore.getState().ensurePlugin(plugin.manifest);
        }
    }, [pluginStoreHydrated]);

    useEffect(() => {
        if (!localMode) return;
        const confirm = () => {
            if (updateStartupConfirmed.current || !useAssetStore.persist.hasHydrated() || !useCanvasStore.persist.hasHydrated()) return;
            updateStartupConfirmed.current = true;
            void confirmDesktopUpdateStartup().catch((error) => console.warn("升级文件清理将在下次启动时重试", error));
        };
        const stopAssets = useAssetStore.persist.onFinishHydration(confirm);
        const stopCanvases = useCanvasStore.persist.onFinishHydration(confirm);
        confirm();
        return () => { stopAssets(); stopCanvases(); };
    }, [localMode]);

    useEffect(() => {
        initializeClientDiagnostics();
    }, []);

    useEffect(() => {
        if (!localMode || !assetsHydrated || !canvasHydrated) return;
        const scope = getActiveUserScope();
        if (localMediaCleanupScope.current === scope) return;
        localMediaCleanupScope.current = scope;
        void useAssetStore.getState().cleanupImages().catch((error) => {
            // Cache cleanup is best effort; it must never block opening the local workspace.
            console.warn("本地媒体缓存清理失败，已保留当前工作区", { scope, error });
        });
    }, [assetsHydrated, canvasHydrated, localMode]);

    useEffect(() => {
        setDiagnosticUserScope(userId);
    }, [userId]);

    useEffect(() => {
        const interactiveSelector = 'button, [role="button"], a, [class*="card"], [class*="Card"]';
        const blurPointerFocus = (event: PointerEvent) => {
            if (event.pointerType === "mouse" && event.button !== 0) return;
            const target = event.target instanceof Element ? event.target.closest<HTMLElement>(interactiveSelector) : null;
            if (!target || target.hasAttribute("disabled") || target.getAttribute("aria-disabled") === "true") return;
            // 浏览器可能把鼠标点击误判为 :focus-visible；下一帧只清掉这次指针点击产生的焦点。
            window.requestAnimationFrame(() => {
                if (document.activeElement === target) target.blur();
            });
        };
        document.addEventListener("pointerdown", blurPointerFocus, true);
        return () => document.removeEventListener("pointerdown", blurPointerFocus, true);
    }, []);

    useEffect(() => {
        if (handledConfigParams.current) return;
        const searchParams = new URLSearchParams(window.location.search);
        const baseUrl = searchParams.get("baseUrl") || searchParams.get("baseurl");
        const ignoredApiKey = searchParams.has("apiKey") || searchParams.has("apikey");
        if (!baseUrl && !ignoredApiKey) return;
        handledConfigParams.current = true;
        searchParams.delete("baseUrl");
        searchParams.delete("baseurl");
        searchParams.delete("apiKey");
        searchParams.delete("apikey");
        window.history.replaceState(null, "", `${window.location.pathname}${searchParams.size ? `?${searchParams}` : ""}${window.location.hash}`);
        void navigateToSettings({ section: "channels" });
        if (ignoredApiKey) message.warning("出于安全考虑，链接中的 API Key 已忽略，请在配置中手动填写");
        else message.info("请在工作台模型配置中维护服务地址");
    }, [message]);

    return <>{children}</>;
}
