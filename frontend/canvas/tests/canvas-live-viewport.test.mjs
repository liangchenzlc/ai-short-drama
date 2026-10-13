import assert from "node:assert/strict";
import test from "node:test";
import { applyCanvasLiveViewport, releaseCanvasLiveViewport, syncCanvasViewportStyleConsumers } from "../src/lib/canvas/canvas-live-viewport.ts";

class Style {
    constructor(values = {}) { this.values = new Map(Object.entries(values)); }
    get cssText() { this.reads = (this.reads || 0) + 1; return [...this.values].map(([name, value]) => `${name}:${value}`).join(";"); }
    getPropertyValue(name) { return this.values.get(name) || ""; }
    setProperty(name, value) { this.values.set(name, value); }
}
class Element {
    constructor(attributes = [], styles = {}) { this.attributes = new Set(attributes); this.style = new Style(styles); }
    hasAttribute(name) { return this.attributes.has(name); }
    getBoundingClientRect() { throw new Error("逐帧预览不能读取几何"); }
}
class Container extends Element {
    constructor(elements) {
        super([], { "--canvas-committed-scale": "1" });
        this.elements = elements; this.dataset = {}; this.events = []; this.scans = 0;
        this.world = new Element();
    }
    querySelector() { return this.world; }
    querySelectorAll() { this.scans++; return this.elements; }
    dispatchEvent(event) { this.events.push(event); return true; }
}
function modern(run, duplicate = false) {
    const old = { CSS: globalThis.CSS, HTMLElement: globalThis.HTMLElement, MutationObserver: globalThis.MutationObserver, Element: globalThis.Element };
    const observers = [], registrations = [];
    globalThis.CSS = { registerProperty: definition => { registrations.push(definition); if (duplicate) throw new DOMException("已注册", "InvalidModificationError"); } };
    globalThis.HTMLElement = Element;
    globalThis.Element = Element;
    globalThis.MutationObserver = class {
        constructor(callback) { this.callback = callback; this.active = true; observers.push(this); }
        observe(target, options) { this.target = target; this.options = options; }
        disconnect() { this.active = false; }
        emit(records = [{ addedNodes: [new Element()], removedNodes: [] }]) { if (this.active) this.callback(records); }
    };
    try { run({ observers, registrations }); }
    finally { for (const [name, value] of Object.entries(old)) { if (value === undefined) delete globalThis[name]; else globalThis[name] = value; } }
}

test("不支持属性注册时保留原世界变换、容器数值和通知频率", () => {
    const container = new Container([]);
    container.dataset.canvasViewportInteracting = "true";
    applyCanvasLiveViewport(container, { x: 10, y: 20, k: 2 }, false);
    assert.equal(container.world.style.transform, "translate3d(10px, 20px, 0) scale(2)");
    assert.equal(container.world.style.willChange, "transform");
    assert.equal(container.style.getPropertyValue("--canvas-live-inverse-scale"), "0.5");
    assert.deepEqual(container.events.map(event => event.type), ["canvas:graphics-viewport-preview"]);
    applyCanvasLiveViewport(container, { x: 11, y: 20, k: 2 });
    assert.deepEqual(container.events.slice(1).map(event => event.type), ["canvas:graphics-viewport-preview", "canvas:viewport-preview", "scroll"]);
    releaseCanvasLiveViewport(container);
});

test("倍率只同步标题、技能内容、选择轮廓和世界栅格，普通子元素保持原样", () => modern(({ registrations }) => {
    const header = new Element([], { transform: "scale(var(--canvas-live-inverse-scale, 1))" });
    const skill = new Element([], { width: "min(260px, calc(240px * var(--canvas-live-scale, 1)))", transform: "scale(var(--canvas-live-inverse-scale, 1))" });
    const node = new Element(["data-node-id", "data-canvas-selection-preview"]);
    const unselected = new Element(["data-node-id"]);
    const raster = new Element(["data-canvas-world-raster-layer"]);
    const ordinary = new Element([], { color: "red" });
    const container = new Container([header, skill, node, unselected, raster, ordinary]);
    applyCanvasLiveViewport(container, { x: 15, y: -3, k: 1.25 });
    assert.equal(header.style.getPropertyValue("--canvas-live-inverse-scale"), "0.8");
    assert.equal(skill.style.getPropertyValue("--canvas-live-scale"), "1.25");
    assert.equal(node.style.getPropertyValue("--canvas-live-inverse-scale"), "0.8");
    assert.equal(unselected.style.getPropertyValue("--canvas-live-inverse-scale"), "");
    assert.equal(header.style.reads, 1, "收集消费者时每个元素仅序列化一次样式");
    assert.equal(raster.style.getPropertyValue("--canvas-committed-scale"), "1");
    assert.equal(ordinary.style.cssText, "color:red");
    assert.equal(registrations.length, 5);
    assert.ok(registrations.every(property => !property.inherits && property.syntax === "*" && !("initialValue" in property)));
    for (let index = 0; index < 24; index++) applyCanvasLiveViewport(container, { x: index, y: 0, k: 1.5 }, false);
    assert.equal(container.scans, 1, "逐帧不能反复扫描挂载树");
    container.style.setProperty("--canvas-committed-scale", "1.5");
    applyCanvasLiveViewport(container, { x: 23, y: 0, k: 1.5 });
    assert.equal(container.world.style.transform, "translate3d(23px, 0px, 0) scale(1)");
    assert.equal(raster.style.getPropertyValue("--canvas-committed-scale"), "1.5");
    releaseCanvasLiveViewport(container);
}));

test("虚拟化新标题立即获得当前倍率，释放后旧观察器停止", () => modern(({ observers }) => {
    const container = new Container([]);
    applyCanvasLiveViewport(container, { x: 0, y: 0, k: 0.5 });
    const header = new Element([], { transform: "scale(var(--canvas-live-inverse-scale, 3))" });
    container.elements.push(header);
    observers[0].emit();
    assert.equal(header.style.getPropertyValue("--canvas-live-inverse-scale"), "2");
    assert.deepEqual(observers[0].options, { childList: true, subtree: true });
    releaseCanvasLiveViewport(container);
    assert.equal(observers[0].active, false);
    applyCanvasLiveViewport(container, { x: 0, y: 0, k: 2 });
    assert.equal(observers.length, 2);
    assert.equal(header.style.getPropertyValue("--canvas-live-inverse-scale"), "0.5");
    releaseCanvasLiveViewport(container);
}));

test("同文档两个画布注册一次，但各自倍率和观察器不串用", () => modern(({ observers, registrations }) => {
    const firstTitle = new Element([], { transform: "scale(var(--canvas-live-inverse-scale, 1))" });
    const secondTitle = new Element([], { transform: "scale(var(--canvas-live-inverse-scale, 1))" });
    const first = new Container([firstTitle]), second = new Container([secondTitle]);
    applyCanvasLiveViewport(first, { x: 0, y: 0, k: 2 });
    applyCanvasLiveViewport(second, { x: 0, y: 0, k: 0.5 });
    assert.equal(registrations.length, 5);
    assert.equal(firstTitle.style.getPropertyValue("--canvas-live-inverse-scale"), "0.5");
    assert.equal(secondTitle.style.getPropertyValue("--canvas-live-inverse-scale"), "2");
    releaseCanvasLiveViewport(first);
    assert.equal(observers[1].active, true);
    releaseCanvasLiveViewport(second);
}));

test("热更新遇到同名注册仍绑定新画布消费者", () => modern(({ registrations }) => {
    const header = new Element([], { transform: "scale(var(--canvas-live-inverse-scale, 1))" });
    const container = new Container([header]);
    applyCanvasLiveViewport(container, { x: 0, y: 0, k: 2 });
    assert.equal(registrations.length, 5);
    assert.equal(header.style.getPropertyValue("--canvas-live-inverse-scale"), "0.5");
    releaseCanvasLiveViewport(container);
}, true));


test("纯文本更新不重扫CSS消费者，新增或移除控件才刷新", () => modern(({ observers }) => {
    const header = new Element([], { transform: "scale(var(--canvas-live-inverse-scale, 1))" });
    const container = new Container([header]);
    applyCanvasLiveViewport(container, { x: 0, y: 0, k: 1.5 });
    for (let index = 0; index < 24; index++) observers[0].emit([{ addedNodes: [{ nodeType: 3 }], removedNodes: [{ nodeType: 3 }] }]);
    assert.equal(container.scans, 1);
    const control = new Element([], { transform: "scale(var(--canvas-live-inverse-scale, 1))" });
    container.elements.push(control);
    observers[0].emit([{ addedNodes: [control], removedNodes: [] }]);
    assert.equal(container.scans, 2);
    assert.equal(control.style.getPropertyValue("--canvas-live-inverse-scale"), String(1 / 1.5));
    container.elements.pop();
    observers[0].emit([{ addedNodes: [], removedNodes: [control] }]);
    assert.equal(container.scans, 3);
    releaseCanvasLiveViewport(container);
}));


test("交互期React提交容器倍率时仍同步原继承值，不改已有世界变换", () => modern(() => {
    const raster = new Element(["data-canvas-world-raster-layer"]);
    const header = new Element([], { transform: "scale(var(--canvas-live-inverse-scale, 1))" });
    const container = new Container([raster, header]);
    applyCanvasLiveViewport(container, { x: 0, y: 0, k: 1.1 });
    container.style.setProperty("--canvas-committed-scale", "1.1");
    container.style.setProperty("--canvas-live-inverse-scale", String(1 / 1.1));
    const transform = container.world.style.transform;
    syncCanvasViewportStyleConsumers(container);
    assert.equal(raster.style.getPropertyValue("--canvas-committed-scale"), "1.1");
    assert.equal(header.style.getPropertyValue("--canvas-live-inverse-scale"), String(1 / 1.1));
    assert.equal(container.world.style.transform, transform);
    releaseCanvasLiveViewport(container);
}));
