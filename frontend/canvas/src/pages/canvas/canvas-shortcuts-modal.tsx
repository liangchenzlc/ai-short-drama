import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { CanvasShortcutsPopover } from "@/components/canvas/canvas-shortcuts-popover";
import { canvasThemes } from "@/lib/canvas-theme";
import { useActiveTheme } from "@/stores/canvas/use-canvas-theme-store";

export function CanvasShortcutsModal({ open, onClose }: { open: boolean; onClose: () => void }) {
    const theme = canvasThemes[useActiveTheme()];
    const rootRef = useRef<HTMLDivElement>(null);
    const closeRef = useRef(onClose);
    closeRef.current = onClose;
    useEffect(() => {
        if (!open) return;
        const previous = document.querySelector<HTMLButtonElement>('.canvas-main-toolbar button[aria-label="画布快捷键"]')
            || (document.activeElement instanceof HTMLElement ? document.activeElement : null);
        let restoreFocus = true;
        const frame = requestAnimationFrame(() => rootRef.current?.querySelector<HTMLElement>("[tabindex]")?.focus());
        const outside = (event: PointerEvent) => {
            if (event.target instanceof Node && !rootRef.current?.contains(event.target)) {
                restoreFocus = false;
                closeRef.current();
            }
        };
        const keyboard = (event: KeyboardEvent) => {
            if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); closeRef.current(); }
        };
        document.addEventListener("pointerdown", outside);
        document.addEventListener("keydown", keyboard, true);
        return () => {
            cancelAnimationFrame(frame);
            document.removeEventListener("pointerdown", outside);
            document.removeEventListener("keydown", keyboard, true);
            if (restoreFocus) previous?.focus();
        };
    }, [open]);
    if (!open) return null;
    return createPortal(
        <div ref={rootRef} className="fixed bottom-20 left-1/2 w-[min(360px,calc(100vw-24px))] -translate-x-1/2" style={{ zIndex: "var(--z-modal, 1100)" }}>
            <CanvasShortcutsPopover x={180} theme={theme} />
        </div>,
        document.body,
    );
}
