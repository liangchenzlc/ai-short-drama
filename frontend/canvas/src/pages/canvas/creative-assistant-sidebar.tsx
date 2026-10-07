import { useCallback, useEffect, useRef, type ComponentProps } from "react";
import { CreativeAssistantPanel } from "@host/features/ai-assistant/CreativeAssistantPanel";
import { AppDrawer } from "@/components/ui/product/app-drawer";
import { ASSISTANT_MAX_WIDTH, ASSISTANT_MIN_WIDTH, type CreativeAssistantLayout } from "./use-creative-assistant-layout";
import "./canvas-assistant-sidebar.css";

type Props = ComponentProps<typeof CreativeAssistantPanel> & {
    assistant: CreativeAssistantLayout;
    dockable: boolean;
    visible: boolean;
};

/** 画布只负责原停靠/抽屉容器，消息与输入统一由工作台公共助手提供。 */
export function CreativeAssistantSidebar({ assistant, dockable, visible, ...props }: Props) {
    const sidebar = useRef<HTMLElement>(null);
    const resizeCleanup = useRef<(() => void) | null>(null);
    useEffect(() => () => resizeCleanup.current?.(), []);
    const startResize = useCallback((event: React.PointerEvent<HTMLButtonElement>) => {
        event.preventDefault();
        resizeCleanup.current?.();
        const startX = event.clientX;
        const startWidth = sidebar.current?.getBoundingClientRect().width ?? assistant.width;
        const move = (moveEvent: PointerEvent) => assistant.setWidth(startWidth + startX - moveEvent.clientX);
        const done = () => {
            window.removeEventListener("pointermove", move);
            window.removeEventListener("pointerup", done);
            window.removeEventListener("pointercancel", done);
            resizeCleanup.current = null;
        };
        resizeCleanup.current = done;
        window.addEventListener("pointermove", move);
        window.addEventListener("pointerup", done);
        window.addEventListener("pointercancel", done);
    }, [assistant]);
    const content = <CreativeAssistantPanel {...props} open={visible} />;
    if (!dockable) return <AppDrawer flush forceRender destroyOnHidden={false} open={visible} placement="right" title={null} closable={false}
        onClose={props.onClose} width="min(380px, 92vw)" aria-label="AI 创作助手">{content}</AppDrawer>;
    return <aside ref={sidebar} className="canvas-assistant-sidebar creative-assistant-canvas" hidden={!visible}
        aria-label="AI 创作助手" style={{ width: assistant.width, flexBasis: assistant.width }}>
        <button type="button" className="canvas-assistant-resize" aria-label="调整助手宽度"
            role="separator" aria-orientation="vertical" aria-valuemin={ASSISTANT_MIN_WIDTH}
            aria-valuemax={ASSISTANT_MAX_WIDTH} aria-valuenow={assistant.width} onPointerDown={startResize}
            onKeyDown={event => {
                if (event.key === "ArrowLeft") { event.preventDefault(); assistant.setWidth(assistant.width + 16); }
                else if (event.key === "ArrowRight") { event.preventDefault(); assistant.setWidth(assistant.width - 16); }
                else if (event.key === "Home") { event.preventDefault(); assistant.setWidth(ASSISTANT_MIN_WIDTH); }
                else if (event.key === "End") { event.preventDefault(); assistant.setWidth(ASSISTANT_MAX_WIDTH); }
            }} />
        {content}
    </aside>;
}
