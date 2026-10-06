export type SettingsSection = "channels" | "models" | "preferences" | "prompts" | "storage";

type NavigationGuard = { save: () => Promise<boolean>; onError: (error: unknown) => void };
let activeGuard: NavigationGuard | undefined;
let navigation: Promise<boolean> | undefined;

export function canvasSettingsReturnTo(pathname = window.location.pathname): string | undefined {
    const match = /^\/canvas-app\/canvas\/([^/]+)$/.exec(pathname);
    if (!match) return;
    try {
        const key = decodeURIComponent(match[1]);
        if (!key || key === "." || key === ".." || /[\/\\%?#\u0000-\u001f]/u.test(key)) return;
        return `/canvas-app/canvas/${encodeURIComponent(key)}`;
    } catch { return; }
}

export function isModelSettingsPath(path: string) {
    return /^\/(?:settings|ai_config)(?:[?#]|$)/u.test(path);
}

export function settingsPath(_section: SettingsSection = "channels", _continueCreation = false) {
    const returnTo = canvasSettingsReturnTo() || canvasSettingsReturnTo(new URLSearchParams(window.location.search).get("return_to") || "");
    const params = new URLSearchParams();
    if (returnTo) params.set("return_to", returnTo);
    return `/ai_config${params.size ? `?${params}` : ""}`;
}

/** 当前编辑器注册可靠保存入口；卸载旧编辑器不能清除新画布的保护。 */
export function registerSettingsNavigationGuard(save: NavigationGuard["save"], onError: NavigationGuard["onError"]) {
    const guard = { save, onError };
    activeGuard = guard;
    return () => { if (activeGuard === guard) activeGuard = undefined; };
}

/** 离开独立 HTML 前等待模型偏好与画布保存；同次点击只启动一次导航。 */
export function navigateToSettings(options?: { section?: SettingsSection; continueCreation?: boolean }): Promise<boolean> {
    return navigateToHostPage(settingsPath(options?.section, options?.continueCreation));
}

export function navigateToHostProjects(): Promise<boolean> {
    return navigateToHostPage("/projects");
}

function navigateToHostPage(to: string): Promise<boolean> {
    if (navigation) return navigation;
    const guard = activeGuard;
    navigation = (async () => {
        try {
            if (guard && !await guard.save()) return false;
            if (guard && activeGuard !== guard) return false;
            window.location.assign(to);
            return true;
        } catch (error) {
            guard?.onError(error);
            return false;
        }
    })().finally(() => { navigation = undefined; });
    return navigation;
}
