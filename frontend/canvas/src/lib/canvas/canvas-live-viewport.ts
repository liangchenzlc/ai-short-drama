import type { SelectionBox, ViewportTransform } from "@/types/canvas";

export const CANVAS_VIEWPORT_PREVIEW_EVENT = "canvas:viewport-preview";
export const CANVAS_GRAPHICS_VIEWPORT_PREVIEW_EVENT = "canvas:graphics-viewport-preview";
export const CANVAS_SELECTION_PREVIEW_EVENT = "canvas:selection-preview";
export const CANVAS_NODE_DRAG_PREVIEW_EVENT = "canvas:node-drag-preview";

export type CanvasNodeDragPreview = {
    x: number;
    y: number;
    nodeIds: ReadonlySet<string>;
};

type NodeDragPreviewDomState = {
    elementsById: Map<string, HTMLElement>;
    previousIds: Set<string>;
    selectionBounds: HTMLElement | null;
};

type NodeSelectionPreviewDomState = {
    elementsById: Map<string, HTMLElement>;
    previousStates: Map<string, "include" | "remove">;
};

export type CanvasNodeSelectionPreview = {
    includeNodeIds: ReadonlySet<string>;
    removeNodeIds: ReadonlySet<string>;
};

const nodeDragPreviewDomStates = new WeakMap<HTMLDivElement, NodeDragPreviewDomState>();
const nodeSelectionPreviewDomStates = new WeakMap<HTMLDivElement, NodeSelectionPreviewDomState>();
const LIVE_VIEWPORT_PROPERTIES = ["--canvas-live-x", "--canvas-live-y", "--canvas-live-scale", "--canvas-live-inverse-scale", "--canvas-committed-scale"] as const;
type LiveViewportProperty = typeof LIVE_VIEWPORT_PROPERTIES[number];
type LiveViewportDomState = {
    worldLayer: HTMLElement | null;
    consumers?: { element: HTMLElement; properties: LiveViewportProperty[]; selectionOutline: boolean }[];
    observer?: MutationObserver;
};
const liveViewportElements = new WeakMap<HTMLDivElement, LiveViewportDomState>();
const registeredViewportStyleAPIs = new WeakSet<object>();

function registerViewportProperties() {
    if (typeof CSS === "undefined" || typeof CSS.registerProperty !== "function") return false;
    if (registeredViewportStyleAPIs.has(CSS)) return true;
    for (const name of LIVE_VIEWPORT_PROPERTIES) {
        try { CSS.registerProperty({ name, syntax: "*", inherits: false }); }
        catch (error) {
            // 热更新可能重新载入本模块；浏览器的同名注册仍然保留。
            if (!(error instanceof DOMException) || error.name !== "InvalidModificationError") throw error;
        }
    }
    registeredViewportStyleAPIs.add(CSS);
    return true;
}

function applyViewportConsumerProperties(container: HTMLDivElement, state: LiveViewportDomState) {
    if (!state.consumers) return;
    const values = new Map(LIVE_VIEWPORT_PROPERTIES.map(name => [name, container.style.getPropertyValue(name)]));
    for (const consumer of state.consumers) {
        for (const name of consumer.properties) {
            // 普通节点没有使用轮廓倍率；仅框选预览需要同步，标题等内联消费者照常更新。
            if (name === "--canvas-live-inverse-scale" && consumer.selectionOutline && !consumer.element.hasAttribute("data-canvas-selection-preview")) continue;
            const value = values.get(name)!;
            if (consumer.element.style.getPropertyValue(name) !== value) consumer.element.style.setProperty(name, value);
        }
    }
}

function observeViewportConsumers(container: HTMLDivElement, state: LiveViewportDomState) {
    if (!registerViewportProperties()) return;
    const refresh = () => {
        // 只在挂载树变化时收集消费者；逐帧不扫描DOM、不读取几何。
        state.consumers = [...container.querySelectorAll<HTMLElement>("[style],[data-node-id],[data-canvas-world-raster-layer]")]
            .filter(element => element instanceof HTMLElement)
            .map(element => {
                const cssText = element.style.cssText;
                const selectionOutline = element.hasAttribute("data-node-id") && !cssText.includes("var(--canvas-live-inverse-scale");
                return { element, selectionOutline, properties: LIVE_VIEWPORT_PROPERTIES.filter(name =>
                    cssText.includes(`var(${name}`)
                    || name === "--canvas-live-inverse-scale" && selectionOutline
                    || name === "--canvas-committed-scale" && element.hasAttribute("data-canvas-world-raster-layer")) };
            })
            .filter(consumer => consumer.properties.length > 0);
        // 虚拟化新增标题须在同一轮绘制前使用当前倍率，不能回落到旧的提交值。
        applyViewportConsumerProperties(container, state);
    };
    state.observer = new MutationObserver(mutations => {
        // 百分比、正文等纯文本变化不会增加CSS消费者，避免为其重扫整个挂载树。
        if (mutations.some(mutation => [...mutation.addedNodes, ...mutation.removedNodes].some(node => node instanceof Element))) refresh();
    });
    state.observer.observe(container, { childList: true, subtree: true });
    refresh();
}

/** React在交互期提交新的容器倍率时，同步原先由继承立即更新的消费者。 */
export function syncCanvasViewportStyleConsumers(container: HTMLDivElement | null) {
    if (!container) return;
    const state = liveViewportElements.get(container);
    if (state) applyViewportConsumerProperties(container, state);
}

export function releaseCanvasLiveViewport(container: HTMLDivElement | null) {
    if (!container) return;
    liveViewportElements.get(container)?.observer?.disconnect();
    liveViewportElements.delete(container);
}

export function applyCanvasLiveViewport(container: HTMLDivElement | null, viewport: ViewportTransform, notify = true) {
    if (!container) return;
    const committedScale = Number(container.style.getPropertyValue("--canvas-committed-scale")) || viewport.k;
    let elements = liveViewportElements.get(container);
    if (!elements) {
        elements = {
            worldLayer: container.querySelector<HTMLElement>("[data-canvas-world-layer]"),
        };
        liveViewportElements.set(container, elements);
        observeViewportConsumers(container, elements);
    }
    const worldLayer = elements.worldLayer;
    if (worldLayer) {
        // 平移期间直接更新合成层；倍率变量只同步实际消费者，避免整棵节点树重新计算样式。
        worldLayer.style.transformOrigin = "0 0";
        worldLayer.style.transform = `translate3d(${viewport.x}px, ${viewport.y}px, 0) scale(${viewport.k / committedScale})`;
        worldLayer.style.willChange = container.dataset.canvasViewportInteracting === "true" ? "transform" : "";
    }
    // Keep the live camera coordinates observable to overlays and automation
    // while the world layer is moved through a compositor transform.
    container.style.setProperty("--canvas-live-x", String(viewport.x));
    container.style.setProperty("--canvas-live-y", String(viewport.y));
    container.style.setProperty("--canvas-live-scale", String(viewport.k));
    // 外置节点标题用同一帧逆倍率抵消世界层缩放，避免等待 React 提交后再校正尺寸。
    container.style.setProperty("--canvas-live-inverse-scale", String(1 / Math.max(viewport.k, 0.05)));
    applyViewportConsumerProperties(container, elements);
    // 图形层必须逐帧跟随 DOM 世界层；浮层和滚动通知仍可按原频率节流。
    container.dispatchEvent(new CustomEvent<ViewportTransform>(CANVAS_GRAPHICS_VIEWPORT_PREVIEW_EVENT, { detail: viewport }));
    if (notify) {
        container.dispatchEvent(new CustomEvent<ViewportTransform>(CANVAS_VIEWPORT_PREVIEW_EVENT, { detail: viewport }));
        // Ant Design overlays watch scrollable ancestors, but CSS transforms do not emit layout events.
        container.dispatchEvent(new Event("scroll"));
    }
}

export function subscribeCanvasGraphicsViewportPreview(container: HTMLDivElement, listener: (viewport: ViewportTransform) => void) {
    const handlePreview = (event: Event) => listener((event as CustomEvent<ViewportTransform>).detail);
    container.addEventListener(CANVAS_GRAPHICS_VIEWPORT_PREVIEW_EVENT, handlePreview);
    return () => container.removeEventListener(CANVAS_GRAPHICS_VIEWPORT_PREVIEW_EVENT, handlePreview);
}

export function subscribeCanvasViewportPreview(container: HTMLDivElement, listener: (viewport: ViewportTransform) => void) {
    const handlePreview = (event: Event) => listener((event as CustomEvent<ViewportTransform>).detail);
    container.addEventListener(CANVAS_VIEWPORT_PREVIEW_EVENT, handlePreview);
    return () => container.removeEventListener(CANVAS_VIEWPORT_PREVIEW_EVENT, handlePreview);
}

/**
 * Applies transient node movement without changing React state. Only nodes
 * currently mounted in the virtualized world are touched; the committed
 * positions are still written by the drag-end path.
 */
export function applyCanvasNodeDragPreview(container: HTMLDivElement | null, preview: CanvasNodeDragPreview | null) {
    if (!container) return;
    if (preview) container.dataset.canvasNodeDragging = "true";
    else delete container.dataset.canvasNodeDragging;

    let state = nodeDragPreviewDomStates.get(container);
    if (!state) {
        state = { elementsById: new Map(), previousIds: new Set(), selectionBounds: null };
        nodeDragPreviewDomStates.set(container, state);
    }

    for (const nodeId of state.previousIds) {
        state.elementsById.get(nodeId)?.style.removeProperty("translate");
    }
    state.selectionBounds?.style.removeProperty("translate");

    state.previousIds.clear();
    if (preview) {
        // Build the lookup once per drag session. A single DOM scan is much
        // cheaper than one selector query per selected node on every frame.
        if (state.elementsById.size === 0) {
            container.querySelectorAll<HTMLElement>("[data-node-id]").forEach((element) => {
                const nodeId = element.dataset.nodeId;
                if (nodeId) state?.elementsById.set(nodeId, element);
            });
            state.selectionBounds = container.querySelector<HTMLElement>("[data-canvas-selection-bounds]");
        }
        for (const nodeId of preview.nodeIds) {
            const element = state.elementsById.get(nodeId);
            if (!element || !element.isConnected) continue;
            element.style.setProperty("translate", `${preview.x}px ${preview.y}px`);
            state.previousIds.add(nodeId);
        }
        if (!state.selectionBounds?.isConnected) {
            state.selectionBounds = container.querySelector<HTMLElement>("[data-canvas-selection-bounds]");
        }
        state.selectionBounds?.style.setProperty("translate", `${preview.x}px ${preview.y}px`);
    } else {
        state.elementsById.clear();
        state.selectionBounds = null;
    }

    container.dispatchEvent(new CustomEvent<CanvasNodeDragPreview | null>(CANVAS_NODE_DRAG_PREVIEW_EVENT, { detail: preview }));
}

export function subscribeCanvasNodeDragPreview(container: HTMLDivElement, listener: (preview: CanvasNodeDragPreview | null) => void) {
    const handlePreview = (event: Event) => listener((event as CustomEvent<CanvasNodeDragPreview | null>).detail);
    container.addEventListener(CANVAS_NODE_DRAG_PREVIEW_EVENT, handlePreview);
    return () => container.removeEventListener(CANVAS_NODE_DRAG_PREVIEW_EVENT, handlePreview);
}

/**
 * Shows the selection delta on mounted nodes without changing React state.
 * The controller commits the final Set once on pointer-up.
 */
export function applyCanvasNodeSelectionPreview(container: HTMLDivElement | null, preview: CanvasNodeSelectionPreview | null) {
    if (!container) return;

    let state = nodeSelectionPreviewDomStates.get(container);
    if (!state) {
        state = { elementsById: new Map(), previousStates: new Map() };
        nodeSelectionPreviewDomStates.set(container, state);
    }

    const nextStates = new Map<string, "include" | "remove">();
    if (preview) {
        for (const nodeId of preview.includeNodeIds) nextStates.set(nodeId, "include");
        for (const nodeId of preview.removeNodeIds) nextStates.set(nodeId, "remove");
    }

    if (state.elementsById.size === 0 && nextStates.size > 0) {
        container.querySelectorAll<HTMLElement>("[data-node-id]").forEach((element) => {
            const nodeId = element.dataset.nodeId;
            if (nodeId) state?.elementsById.set(nodeId, element);
        });
    }

    for (const [nodeId, previousState] of state.previousStates) {
        if (nextStates.get(nodeId) === previousState) continue;
        state.elementsById.get(nodeId)?.removeAttribute("data-canvas-selection-preview");
    }
    for (const [nodeId, nextState] of nextStates) {
        if (state.previousStates.get(nodeId) === nextState) continue;
        const element = state.elementsById.get(nodeId);
        if (!element || !element.isConnected) continue;
        element.style.setProperty("--canvas-live-inverse-scale", container.style.getPropertyValue("--canvas-live-inverse-scale"));
        element.dataset.canvasSelectionPreview = nextState;
    }

    state.previousStates = nextStates;
    if (!preview) state.elementsById.clear();
}

export function applyCanvasSelectionPreview(container: HTMLDivElement | null, selection: SelectionBox) {
    container?.dispatchEvent(new CustomEvent<SelectionBox>(CANVAS_SELECTION_PREVIEW_EVENT, { detail: selection }));
}

export function subscribeCanvasSelectionPreview(container: HTMLDivElement, listener: (selection: SelectionBox) => void) {
    const handlePreview = (event: Event) => listener((event as CustomEvent<SelectionBox>).detail);
    container.addEventListener(CANVAS_SELECTION_PREVIEW_EVENT, handlePreview);
    return () => container.removeEventListener(CANVAS_SELECTION_PREVIEW_EVENT, handlePreview);
}
