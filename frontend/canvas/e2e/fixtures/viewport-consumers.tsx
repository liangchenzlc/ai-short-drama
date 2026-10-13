import { useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { InfiniteCanvas } from "@/components/canvas/infinite-canvas";
import { CanvasNode } from "@/components/canvas/canvas-node";
import { CanvasVideoCropEditor } from "@/components/canvas/canvas-video-crop-dialog";
import { applyCanvasNodeSelectionPreview } from "@/lib/canvas/canvas-live-viewport";
import { defaultConfig } from "@/stores/use-config-store";
import { CanvasNodeType, type CanvasNodeData } from "@/types/canvas";
import "@/styles/globals.css";

const imageUrl = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==";
const ignore = () => {};
function Fixture() {
    const container = useRef<HTMLDivElement>(null);
    const [open, setOpen] = useState(true);
    const [viewport, setViewport] = useState({ x: 0, y: 0, k: 1 });
    const [tool, setTool] = useState("");
    const [visible, setVisible] = useState(true);
    const [node, setNode] = useState<CanvasNodeData>({ id: "image", type: CanvasNodeType.Image, title: "原节点标题", width: 320, height: 180, position: { x: 420, y: 350 }, metadata: { content: imageUrl, naturalWidth: 320, naturalHeight: 180 } });
    return <main>
        <nav data-canvas-no-zoom style={{ display: "flex", gap: 12 }}>
            {["crop", "mask", "annotation", "video"].map(name => <button key={name} onClick={() => setTool(name)}>{name}</button>)}
            <button onClick={() => { setTool(""); setViewport({ x: 0, y: 0, k: 1 }); }}>复位</button>
            <button onClick={() => setVisible(value => !value)}>切换节点</button>
            <button onClick={() => setOpen(value => !value)}>切换画布</button>
            <button onClick={() => applyCanvasNodeSelectionPreview(container.current, { includeNodeIds: new Set(["image"]), removeNodeIds: new Set() })}>预览选区</button>
            <button onClick={() => applyCanvasNodeSelectionPreview(container.current, null)}>清除选区</button>
        </nav>
        <output aria-label="已改标题">{node.title}</output>
        <div style={{ height: 800 }}>{open && <InfiniteCanvas containerRef={container} viewport={viewport} onViewportChange={setViewport}>
            {visible && <CanvasNode data={node} scale={viewport.k} isSelected={false} isRelated={false} isFocusRelated={false} isConnectionTarget={false} showImageInfo
                onMouseDown={ignore} onHoverStart={ignore} onHoverEnd={ignore} onConnectStart={ignore} onResize={ignore} onContentChange={ignore} onContextMenu={ignore}
                onTitleChange={(_, title) => setNode(value => ({ ...value, title }))}
                imageCropActive={tool === "crop"} onCancelImageCrop={() => setTool("")} onConfirmImageCrop={ignore}
                maskEditActive={tool === "mask"} maskEditConfig={defaultConfig} onCancelMaskEdit={() => setTool("")} onConfirmMaskEdit={ignore}
                annotationActive={tool === "annotation"} onCancelAnnotation={() => setTool("")} onConfirmAnnotation={ignore}
            />}
            {tool === "video" && <div style={{ position: "absolute", left: 420, top: 350, width: 320, height: 180 }}>
                <CanvasVideoCropEditor videoUrl="/__viewport-sample.mp4" videoDimensions={{ width: 320, height: 180 }} onCancel={() => setTool("")} onConfirm={ignore} />
            </div>}
        </InfiniteCanvas>}</div>
    </main>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
