import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const path = "../../src/pages/canvas/use-canvas-node-operations.ts";
const parsed = ts.createSourceFile(path, readFileSync(new URL(path, import.meta.url), "utf8"), ts.ScriptTarget.ES2022, true);
const source = parsed.statements.find(statement => statement.name?.text === "useCanvasNodeOperations").getText(parsed).replace(/^export /, "");
const code = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None } }).outputText;

function setup({ fail = false, nodes = [{ id: "video", metadata: { status: "loading", taskId: "9007199254740993" } }] } = {}) {
    const events = [], dialogs = [], listeners = [];
    const nodesRef = { current: nodes };
    let epoch = 1;
    const context = vm.createContext({
        App: { useApp: () => ({ message: { warning: value => events.push(["warning", value]) }, modal: { confirm: options => { dialogs.push(options); return { destroy() { options.afterClose?.(); } }; } } }) },
        useCallback: fn => fn,
        useRef: current => ({ current }),
        useState: current => [current, next => { if (typeof next === "string" && next) events.push(["warning", next]); }],
        useEffect: () => {},
        useLayoutEffect: fn => fn(),
        useEffectiveConfig: () => ({ runningHub: { enabled: true, workflows: [] } }),
        captureUserScope: () => epoch,
        userScopeMatches: captured => captured === epoch,
        subscribeUserScope: listener => { listeners.push(listener); return () => {}; },
        isCanvasNodeGenerating: node => node.metadata?.status === "loading",
        canvasTaskConfirmation: {},
        removeCanvasNodes: (current, ids) => ({ nodes: current.filter(node => !ids.has(node.id)), removedIds: ids }),
        applyCanvasConnectionPromptSync: (_before, _edges, next) => next,
        cancelGenerationTask: async id => { events.push(["cancel", id]); if (fail) throw new Error("cancel failed"); },
    });
    vm.runInContext(code, context);
    const operations = context.useCanvasNodeOperations({
        projectId: "canvas", defaultDrawingEngine: "tldraw", nodesRef, connectionsRef: { current: [] }, selectedNodeIdsRef: { current: new Set() },
        setNodes: next => { nodesRef.current = next; }, setConnections() {}, setSelectedNodeIds() {}, setSelectedConnectionId() {}, setContextMenu() {}, setDialogNodeId() {},
        onNodesDeleted: () => events.push(["deleted"]),
    });
    return { operations, nodesRef, events, dialogs, changeScope: () => { epoch++; listeners.forEach(fn => fn()); } };
}

test("生成中删除先确认，确认后发起取消再删除；重复点击不增加确认框", async () => {
    const h = setup();
    h.operations.deleteNodes(new Set(["video"]));
    h.operations.deleteNodes(new Set(["video"]));
    assert.equal(h.nodesRef.current.length, 1);
    assert.equal(h.dialogs.length, 1);
    await h.dialogs[0].onOk();
    assert.deepEqual(h.events, [["cancel", "9007199254740993"], ["deleted"]]);
    assert.equal(h.nodesRef.current.length, 0);
});

test("取消失败仍删除，并明确提示任务继续；普通节点无需确认", async () => {
    const h = setup({ fail: true });
    h.operations.deleteNodes(new Set(["video"]));
    assert.equal(h.dialogs.length, 1);
    await h.dialogs[0].onOk();
    assert.equal(h.nodesRef.current.length, 0);
    assert.match(h.events.at(-1)[1], /继续运行/);
    const plain = setup({ nodes: [{ id: "text", metadata: {} }] });
    plain.operations.deleteNodes(new Set(["text"]));
    assert.equal(plain.dialogs.length, 0);
    assert.equal(plain.nodesRef.current.length, 0);
});

test("等待批次也需要确认；相同任务只取消一次，账号切换使旧确认失效", async () => {
    const nodes = [{ id: "batch", metadata: { generationBatches: [{ items: [{ status: "waiting" }, { status: "running", taskId: "task" }, { status: "queued", taskId: "task" }] }] } }];
    const h = setup({ nodes });
    h.operations.deleteNodes(new Set(["batch"]));
    assert.equal(h.dialogs.length, 1);
    await h.dialogs[0].onOk();
    assert.deepEqual(h.events, [["cancel", "task"], ["deleted"]]);
    const stale = setup();
    stale.operations.deleteNodes(new Set(["video"]));
    assert.equal(stale.dialogs.length, 1);
    stale.changeScope();
    await stale.dialogs[0].onOk();
    assert.equal(stale.nodesRef.current.length, 1);
    assert.equal(stale.events.length, 0);
});
