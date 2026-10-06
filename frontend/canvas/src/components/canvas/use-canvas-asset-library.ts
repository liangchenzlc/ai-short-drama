import { useCallback, useEffect, useState } from "react";
import { captureUserScope, userScopeMatches } from "@/lib/user-scope-guard";
import { subscribeUserScope } from "@/lib/user-scope";
import { loadCanvasTrayLibrary } from "@/services/host-canvas-tray-library";
import { useUserStore } from "@/stores/use-user-store";

export function useCanvasAssetLibrary(enabled: boolean) {
    const scope = useUserStore(state => state.user?.id);
    const [attempt, setAttempt] = useState(0);
    const [state, setState] = useState({ scope, loading: enabled, error: "" });
    const reload = useCallback(() => setAttempt(value => value + 1), []);
    useEffect(() => {
        if (!enabled) return;
        const expected = captureUserScope();
        const controller = new AbortController();
        const unsubscribe = subscribeUserScope(() => controller.abort());
        setState({ scope, loading: true, error: "" });
        void loadCanvasTrayLibrary(expected, controller.signal).then(() => {
            if (!controller.signal.aborted && userScopeMatches(expected)) setState({ scope, loading: false, error: "" });
        }).catch(error => {
            if (!controller.signal.aborted && userScopeMatches(expected)) setState({ scope, loading: false, error: error instanceof Error ? error.message : "图片素材读取失败" });
        });
        return () => { controller.abort(); unsubscribe(); };
    }, [enabled, scope, attempt]);
    return { loading: enabled && (state.loading || state.scope !== scope), error: state.scope === scope ? state.error : "", reload };
}
