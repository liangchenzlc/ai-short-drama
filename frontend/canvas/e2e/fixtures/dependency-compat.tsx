import { useState } from "react";
import { createRoot } from "react-dom/client";
import "@ant-design/v5-patch-for-react-19";
import "@fontsource-variable/inter";
import "@fontsource-variable/jetbrains-mono";
import "@/styles/globals.css";
import { App, ConfigProvider } from "antd";
import { CanvasPromptOptimizerDrawer } from "@/components/canvas/canvas-prompt-optimizer-drawer";
import { defaultConfig } from "@/stores/use-config-store";
import { getAntThemeConfig } from "@/lib/app-theme";
import { ModelIconPicker, ModelLogo } from "@/components/model-logo";

declare global {
    interface Window {
        __dependencyCompat: { inputs: unknown[]; applied: string | null };
    }
}

window.__dependencyCompat = { inputs: [], applied: null };
const optimizedPrompt = Array.from({ length: 30 }, (_, index) => `镜头细节${index}：夜晚城市，光线、构图、人物动作。`).join("\n");
const provider = {
    optimize: async (input: unknown, options?: { onDelta?: (value: string) => void }) => {
        window.__dependencyCompat.inputs.push(input);
        options?.onDelta?.("开始整理");
        await new Promise((resolve) => setTimeout(resolve, 80));
        return { optimizedPrompt, negativePrompt: "避免模糊", changes: Array.from({ length: 12 }, (_, index) => `补充细节${index}`), assumptions: [], variants: [] };
    },
};

function Fixture() {
    const [open, setOpen] = useState(true);
    const [logo, setLogo] = useState("Codex");
    return <ConfigProvider theme={getAntThemeConfig(false)}><App>
        <CanvasPromptOptimizerDrawer open={open} prompt="初始镜头" generationMode="image" targetModel="fixture-model" config={{ ...defaultConfig, channels: [] }} optimizerModel="" references={[]} provider={provider} onClose={() => setOpen(false)} onApply={(value) => { window.__dependencyCompat.applied = value; }}>
            <button type="button" onClick={() => setOpen(true)}>打开提示词助手</button>
        </CanvasPromptOptimizerDrawer>
        <div style={{ marginLeft: 800, marginTop: 20 }}><ModelLogo icon={logo} /><ModelIconPicker value={logo} onChange={setLogo} /></div>
    </App></ConfigProvider>;
}

createRoot(document.getElementById("root")!).render(<Fixture />);
