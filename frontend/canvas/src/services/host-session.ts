import { http, ApiError } from "@host/api/http";
import { setActiveUserScope } from "@/lib/user-scope";
import type { WorkspaceBootstrapPayload } from "@/services/api/workspace";

let activeBootstrap: WorkspaceBootstrapPayload | undefined;

/** Called before importing any persistent editor store. Browser cache cannot authenticate. */
export async function establishCanvasSession(): Promise<WorkspaceBootstrapPayload> {
    const { user } = (await http.get<{ user: { id: string; username: string; display_name: string; email: string } }>("/auth/me")).data;
    if (!/^[1-9]\d*$/.test(user.id)) throw new Error("当前账号身份无效");
    setActiveUserScope(user.id);
    activeBootstrap = {
        contractVersion: 1, profile: "hosted", storageMode: "remote",
        user: { id: user.id, username: user.username, displayName: user.display_name, email: user.email, role: "user", status: "active" },
        workspace: { id: user.id, name: user.display_name || user.username, owner: user.id, storage: "mysql" },
        capabilities: { localAssets: true, providerCalls: true },
        features: { shortDramaEnabled: true, taskCenterEnabled: true, customChannelsEnabled: true, frontendModelsEnabled: false, pluginCenterEnabled: true, systemPluginsVisibleToUsers: true },
    };
    return activeBootstrap;
}

export async function getAuthenticatedCanvasBootstrap() {
    if (!activeBootstrap) throw new Error("请先登录，再读取画布工作区");
    return activeBootstrap;
}

export function canvasSignInUrl() {
    return `/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}`;
}

export function isCanvasSignInRequired(error: unknown) {
    return error instanceof ApiError && error.status === 401;
}
