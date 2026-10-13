import { useState } from "react";
import { createRoot } from "react-dom/client";
import { AIMessageMarkdown } from "@/components/ai/ai-message-markdown";
import "@/styles/globals.css";

function Fixture() {
    const [content, setContent] = useState("");
    const [streaming, setStreaming] = useState(true);
    return <main style={{ width: 700, padding: 24 }}>
        <button onClick={() => setContent("# 初始标题\n\n**实际正文**\n\n|名称|数量|\n|---|---|\n|样本|2|")}>显示内容</button>
        <button onClick={() => setContent("# 更新标题\n\n**更新正文**\n\n|名称|数量|\n|---|---|\n|更新|3|")}>更新内容</button>
        <button onClick={() => setContent("")}>隐藏内容</button>
        <button onClick={() => setStreaming(false)}>完成输出</button>
        <section aria-label="Markdown内容"><AIMessageMarkdown isStreaming={streaming}>{content}</AIMessageMarkdown></section>
    </main>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
