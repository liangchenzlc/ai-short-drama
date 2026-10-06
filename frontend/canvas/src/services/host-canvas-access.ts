type Scope = { userScope: string; epoch: number };
const keyFor = (scope: Scope, id: string) => `${scope.userScope}\0${scope.epoch}\0${id}`;

export function createCanvasAccessRegistry() {
    const denied = new Map<string, string>();
    const listeners = new Set<() => void>();
    return {
        subscribe(listener: () => void) {
            listeners.add(listener);
            return () => { listeners.delete(listener); };
        },
        reason(scope: Scope, id: string) { return denied.get(keyFor(scope, id)) || ""; },
        allow(scope: Scope, id: string) {
            if (!denied.delete(keyFor(scope, id))) return;
            for (const listener of listeners) listener();
        },
        deny(scope: Scope, id: string) {
            const key = keyFor(scope, id);
            if (denied.has(key)) return;
            denied.set(key, "画布已被归档或访问权限已变化。本机草稿已保留，请重新加载后继续。");
            for (const listener of listeners) listener();
        },
        assert(scope: Scope, id: string) {
            const reason = denied.get(keyFor(scope, id));
            if (reason) throw new Error(reason);
        },
    };
}

export const hostCanvasAccess = createCanvasAccessRegistry();

/** A create may fail because source media is missing; it is not evidence of target revocation. */
export function canvasRequestCanRevoke(method: string, url: string) {
    return !(method === "put" && /^\/canvas-projects\/[^/]+$/.test(url));
}

/** The Python permission boundary hides missing/archived/unauthorized canvases as 404. */
export function canvasRequestFailureRevokes(method: string, url: string, failure: { status?: number; code?: string }) {
    // CSRF, email verification and write-rule 403s do not revoke the readable canvas.
    return canvasRequestCanRevoke(method, url) && failure.status === 404 && failure.code === "not_found";
}

/** Only failures of the canvas itself revoke access; a missing history/media item does not. */
export function canvasRequestIdentity(url: string, data?: unknown) {
    if (url === "/ops/canvas.document.commit") {
        const id = (data as { params?: { canvasId?: unknown } } | undefined)?.params?.canvasId;
        return typeof id === "string" ? id : undefined;
    }
    const match = /^\/canvas-projects\/([^/]+)(?:\/(?:viewport|view-preferences))?$/.exec(url);
    return match ? decodeURIComponent(match[1]) : undefined;
}
