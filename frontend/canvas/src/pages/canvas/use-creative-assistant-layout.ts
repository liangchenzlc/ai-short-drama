import { useCallback, useState } from "react";
import { scopedLocalStorage } from "@/lib/user-scope";

export const ASSISTANT_MIN_WIDTH = 320;
export const ASSISTANT_MAX_WIDTH = 560;
export const ASSISTANT_DEFAULT_WIDTH = 380;
const WIDTH_KEY = "canvas:assistant-width";

export function useCreativeAssistantLayout() {
    const [open, setOpen] = useState(false);
    const [width, setWidthState] = useState(() => clampWidth(Number(scopedLocalStorage.getItem(WIDTH_KEY)) || ASSISTANT_DEFAULT_WIDTH));
    const setWidth = useCallback((value: number) => {
        const next = clampWidth(value);
        setWidthState(next);
        scopedLocalStorage.setItem(WIDTH_KEY, String(next));
    }, []);
    return { open, setOpen, width, setWidth };
}

function clampWidth(value: number) {
    return Number.isFinite(value) ? Math.min(ASSISTANT_MAX_WIDTH, Math.max(ASSISTANT_MIN_WIDTH, Math.round(value))) : ASSISTANT_DEFAULT_WIDTH;
}

export type CreativeAssistantLayout = ReturnType<typeof useCreativeAssistantLayout>;
