import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const file = "../../src/components/canvas/infinite-canvas.tsx";
const parsed = ts.createSourceFile(file, readFileSync(new URL(file, import.meta.url), "utf8"), ts.ScriptTarget.ES2022, true, ts.ScriptKind.TSX);
let wheel;
function visit(node) { if (ts.isVariableDeclaration(node) && node.name.getText(parsed) === "handleWheel") wheel = node.getText(parsed); ts.forEachChild(node, visit); }
visit(parsed);
const helperUrl = new URL("../../src/lib/canvas/canvas-wheel.ts", import.meta.url);
const helper = existsSync(helperUrl) ? readFileSync(helperUrl, "utf8").replace(/export /g, "") : "";
const code = ts.transpileModule(`${helper}\nconst ${wheel};`, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None } }).outputText;

function setup() {
    let now = 1000;
    const viewportRef = { current: { x: 0, y: 0, k: 1 } };
    const context = vm.createContext({
        Element: class {}, useCallback: fn => fn, viewportRef,
        wheelGestureRef: { current: null }, interactingRef: { current: false }, containerRef: { current: null }, containerRectRef: { current: { left: 0, top: 0 } },
        CANVAS_WHEEL_IGNORE_SELECTOR: "", WHEEL_ZOOM_DELTA: 72, TRACKPAD_PINCH_ZOOM_DELTA: 24,
        performance: { now: () => now }, wheelDeltaToPixels: (delta, mode) => delta * (mode === 1 ? 16 : mode === 2 ? 800 : 1),
        clampScale: value => Math.max(.1, Math.min(6, value)), scheduleViewportChange: value => { viewportRef.current = value; },
    });
    vm.runInContext(`${code}\nglobalThis.wheel = handleWheel;`, context);
    return { viewportRef, wheel: (deltaY, extra = {}, elapsed = 20) => { now += elapsed; context.wheel({ target: null, deltaX: 0, deltaY, deltaMode: 0, ctrlKey: false, metaKey: false, shiftKey: false, clientX: 100, clientY: 100, preventDefault() {}, ...extra }); } };
}

test("60/80/100/120 像素滚轮均缩放，连续小步长保留缩放意图", () => {
    for (const delta of [60, 80, 100, 120]) {
        const h = setup();
        h.wheel(delta);
        assert.ok(h.viewportRef.current.k < 1, `${delta} 应缩放`);
        const before = h.viewportRef.current.k;
        h.wheel(7);
        assert.ok(h.viewportRef.current.k < before, "连续手势不能误切换平移");
    }
});

test("触控板连续事件保持平移，手势结束与修饰键变化立即重新判断", () => {
    const h = setup();
    h.wheel(6);
    h.wheel(60);
    assert.equal(h.viewportRef.current.k, 1);
    h.wheel(60, {}, 250);
    assert.ok(h.viewportRef.current.k < 1);
    const k = h.viewportRef.current.k;
    h.wheel(50, { shiftKey: true });
    assert.equal(h.viewportRef.current.k, k);
    h.wheel(10, { ctrlKey: true });
    assert.ok(h.viewportRef.current.k < k);
});
