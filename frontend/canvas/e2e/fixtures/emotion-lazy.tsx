import { useState } from "react";
import { createRoot } from "react-dom/client";
import { CanvasNodeEmotionPanel } from "@/components/canvas/canvas-node-emotion-panel";
import { canvasEmotionPresets } from "@/lib/canvas/canvas-emotion";
import "@/styles/globals.css";

function Fixture() {
    const [open, setOpen] = useState(false);
    const [preset, setPreset] = useState(canvasEmotionPresets[12]);
    const [manual, setManual] = useState(0);
    return <main style={{ width: 520, padding: 24 }}>
        <button onClick={() => setOpen(true)}>打开表情</button>
        <output aria-label="手动框选次数">{manual}</output>
        <section aria-label="表情面板">{open && <CanvasNodeEmotionPanel
            dataUrl="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg=="
            imageWidth={1} imageHeight={1} characters={[]} activeCharacterId="" preset={preset} generating={false}
            onSelectCharacter={() => {}} onManualSelect={() => setManual(value => value + 1)} onPresetChange={setPreset} onClose={() => setOpen(false)} onConfirm={() => {}}
        />}</section>
    </main>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
