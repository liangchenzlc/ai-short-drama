import assert from "node:assert/strict";
import test from "node:test";
import { build } from "esbuild";
import { fileURLToPath } from "node:url";

const bundled = await build({
    entryPoints: [fileURLToPath(new URL("../../src/lib/canvas/canvas-layout.ts", import.meta.url))],
    bundle: true, write: false, format: "esm", platform: "node",
    alias: { "@": fileURLToPath(new URL("../../src", import.meta.url)) },
});
const { layoutCanvasFlow, layoutCanvasAuto } = await import("data:text/javascript;base64," + Buffer.from(bundled.outputFiles[0].text).toString("base64"));
const node = (id, type, x, y, width, height, metadata = {}) => ({ id, type, position: { x, y }, width, height, metadata });
const edge = (fromNodeId, toNodeId) => ({ id: `${fromNodeId}-${toNodeId}`, fromNodeId, toNodeId });

test("连线整理保持拓扑层宽、媒体泳道和负坐标起点", () => {
    const nodes = [node("a", "text", 10, 30, 100, 60), node("b", "image", -20, -10, 200, 80), node("c", "text", 0, 0, 80, 90), node("d", "text", 20, -5, 100, 70)];
    const original = structuredClone(nodes);
    assert.deepEqual([...layoutCanvasFlow(nodes, [edge("a", "b"), edge("a", "c"), edge("c", "d")])], [
        ["a", { x: -20, y: -10 }], ["c", { x: 200, y: -10 }], ["d", { x: 520, y: -10 }], ["b", { x: 200, y: 176 }],
    ]);
    assert.deepEqual(nodes, original);
});

test("同一泳道同坐标保留输入顺序且不修改原节点", () => {
    const nodes = [node("second", "image", 5, -4, 120, 110), node("first", "image", 5, -4, 90, 70)];
    for (const item of nodes) { Object.freeze(item.position); Object.freeze(item); }
    Object.freeze(nodes);
    assert.deepEqual([...layoutCanvasFlow(nodes, [])], [["second", { x: 5, y: -4 }], ["first", { x: 5, y: 142 }]]);
});

test("环形和范围外连线沿用源分层结果且能结束", () => {
    const nodes = [node("a", "text", 0, 0, 120, 80), node("b", "text", 160, 0, 100, 90), node("c", "video", 240, 100, 140, 100)];
    assert.deepEqual([...layoutCanvasFlow(nodes, [edge("a", "b"), edge("b", "a"), edge("missing", "c"), edge("c", "outside")])], [
        ["a", { x: 0, y: 0 }], ["b", { x: 0, y: 116 }], ["c", { x: 0, y: 302 }],
    ]);
});

test("从无环节点进入循环时保留源已推导的部分层", () => {
    const nodes = [node("start", "text", 0, 0, 70, 50), node("a", "text", 100, 0, 80, 70), node("b", "text", 200, 0, 90, 80)];
    assert.deepEqual([...layoutCanvasFlow(nodes, [edge("start", "a"), edge("a", "b"), edge("b", "a")])], [
        ["start", { x: 0, y: 0 }], ["b", { x: 0, y: 86 }], ["a", { x: 210, y: 0 }],
    ]);
});

test("自动整理排除锁定节点和Frame，少于两个候选不移动", () => {
    const nodes = [node("locked", "text", -999, -999, 100, 100, { locked: true }), node("frame", "frame", -500, -500, 400, 400), node("text", "text", 20, 10, 120, 80), node("image", "image", 50, 50, 90, 100)];
    assert.deepEqual([...layoutCanvasAuto(nodes, [])], [["text", { x: 20, y: 10 }], ["image", { x: 20, y: 186 }]]);
    assert.equal(layoutCanvasAuto(nodes.slice(0, 3), []).size, 0);
    assert.equal(layoutCanvasFlow([], []).size, 0);
});

test("长链分层不对每层重复扫描全部节点", () => {
    const count = 2000;
    let identitiesRead = 0;
    const nodes = Array.from({ length: count }, (_, index) => {
        const item = node(`node-${index}`, "text", 0, index * 100, 80, 60);
        Object.defineProperty(item, "id", { enumerable: true, get: () => { identitiesRead++; return `node-${index}`; } });
        return item;
    });
    const positions = layoutCanvasFlow(nodes, Array.from({ length: count - 1 }, (_, index) => edge(`node-${index}`, `node-${index + 1}`)));
    assert.equal(positions.size, count);
    assert.deepEqual(positions.get(`node-${count - 1}`), { x: (count - 1) * 200, y: 0 });
    assert.ok(identitiesRead < count * 48, `长链读取身份${identitiesRead}次，存在逐层全表扫描`);
});
