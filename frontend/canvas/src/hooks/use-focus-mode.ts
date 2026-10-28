import { useCallback, useState } from "react";

const FOCUS_MODE_KEY = "canvas-focus-mode-v2";

// 首次进入保留桌面工具栏，之后沿用用户保存的专注模式偏好。
function readInitialPreference(): boolean {
    const stored = window.localStorage.getItem(FOCUS_MODE_KEY);
    if (stored !== null) return stored === "true";
    return false;
}

export function useFocusMode() {
    const [userPreference, setUserPreference] = useState<boolean>(readInitialPreference);
    const focusMode = userPreference;

    const persist = useCallback((next: boolean) => {
        setUserPreference(next);
        try {
            window.localStorage.setItem(FOCUS_MODE_KEY, String(next));
        } catch {
            // 忽略 localStorage 不可用场景，专注模式仍可在本次会话生效。
        }
    }, []);

    const enterFocusMode = useCallback(() => persist(true), [persist]);
    const exitFocusMode = useCallback(() => persist(false), [persist]);
    const toggleFocusMode = useCallback(() => persist(!userPreference), [persist, userPreference]);

    return {
        focusMode,
        enterFocusMode,
        exitFocusMode,
        toggleFocusMode,
    };
}
