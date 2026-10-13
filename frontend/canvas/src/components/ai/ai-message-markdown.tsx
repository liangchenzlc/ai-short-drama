import { memo, useEffect, useState } from "react";
import { LoaderCircle } from "lucide-react";

export type AIMessageMarkdownProps = {
    children: string;
    isStreaming?: boolean;
    className?: string;
};

type MarkdownRenderer = typeof import("./ai-message-markdown-renderer").AIMessageMarkdownRenderer;
let loadedRenderer: MarkdownRenderer | undefined;
let rendererPromise: Promise<MarkdownRenderer> | undefined;

function loadRenderer() {
    return rendererPromise ??= import("./ai-message-markdown-renderer").then(module => {
        loadedRenderer = module.AIMessageMarkdownRenderer;
        return loadedRenderer;
    }).catch(error => { rendererPromise = undefined; throw error; });
}

export const AIMessageMarkdown = memo(function AIMessageMarkdown(props: AIMessageMarkdownProps) {
    const [Renderer, setRenderer] = useState<MarkdownRenderer | undefined>(() => loadedRenderer);
    const [failed, setFailed] = useState(false);
    const hasContent = Boolean(props.children.trim());
    useEffect(() => {
        if (!hasContent || Renderer) return;
        let active = true;
        setFailed(false);
        void loadRenderer().then(renderer => { if (active) setRenderer(() => renderer); })
            .catch(() => { if (active) setFailed(true); });
        return () => { active = false; };
    }, [hasContent, Renderer]);
    if (!hasContent) return null;
    if (Renderer) return <Renderer {...props} />;
    return <div className={`ai-message-markdown ${props.className || ""}`.trim()} role={failed ? "alert" : "status"}>
        {failed ? "内容加载失败，请刷新页面后重试。" : <><LoaderCircle className="mr-2 inline size-4 animate-spin" aria-hidden />正在加载内容…</>}
    </div>;
});
